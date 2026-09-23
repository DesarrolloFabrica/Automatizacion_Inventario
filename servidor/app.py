"""
Servicio web de la fábrica de contenido.

Sirve la página de `servidor/static/` y una API pequeña para lanzar corridas y
ver cómo van. El trabajo pesado lo hace `servidor/corridas.py` en un hilo de
fondo, así que la petición que lanza una corrida responde al instante y la
página va preguntando el avance.

Arranque local:
    python -m uvicorn servidor.app:app --reload --port 8080

En Cloud Run lo arranca el Dockerfile con un único worker: las corridas viven
en memoria dentro del proceso, así que varios workers romperían el estado.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from flujo_lib import certificados, drive
from flujo_lib.mensajes import ErrorFlujo, traducir_excepcion
from flujo_lib.prevalidacion import cargar_env
from servidor import configuracion, registro
from servidor.corridas import Gestor, construir_lote

ESTATICOS = Path(__file__).resolve().parent / "static"
logger = logging.getLogger(__name__)


class PeticionLote(BaseModel):
    """Una fila origen-destino del formulario."""

    origen: str = ""
    destino: str = ""


class PeticionCorrida(BaseModel):
    """Lo que manda el formulario de la página."""

    # `origen` y `destino` conservan compatibilidad con clientes anteriores.
    origen: str | None = None
    destino: str | None = None
    lotes: list[PeticionLote] = Field(default_factory=list)
    etiqueta: str | None = None
    cliente: str | None = None
    simular: bool | None = None
    forzar_carga: bool | None = None


def _error(codigo: int, err: ErrorFlujo) -> JSONResponse:
    """Los errores viajan siempre como «qué pasó» + «qué hacer»."""
    return JSONResponse(status_code=codigo, content={"motivo": err.motivo, "accion": err.accion})


def crear_app(cfg=None, gestor: Gestor | None = None) -> FastAPI:
    """Construye la aplicación. cfg y gestor se inyectan en las pruebas."""
    # Lo primero: que todo lo que pase se vea en los registros del servidor.
    # Sin esto el contenedor es mudo y un fallo en el despliegue no deja rastro.
    registro.configurar()
    # En los equipos de la CUN el antivirus inspecciona el tráfico seguro: hay que
    # reconocer sus certificados antes de la primera llamada a Google. Fuera de
    # Windows (Cloud Run) no hace nada.
    certificados.asegurar(log=logger.info)
    cargar_env()
    cfg = cfg or configuracion.cargar()
    gestor = gestor or Gestor(cfg)

    @asynccontextmanager
    async def ciclo_de_vida(_app: FastAPI):
        yield
        gestor.detener()  # al apagar, se espera a que el hilo de corridas cierre

    app = FastAPI(
        title="Fábrica de contenido",
        description="Clonación, conversión, verificación y carga de material educativo.",
        docs_url=None,
        redoc_url=None,
        lifespan=ciclo_de_vida,
    )
    app.state.cfg = cfg
    app.state.gestor = gestor
    app.state.cuenta = None

    @app.middleware("http")
    async def evitar_estaticos_desactualizados(peticion, continuar):
        """Evita mezclar HTML nuevo con JS o CSS guardados por el navegador."""
        respuesta = await continuar(peticion)
        if peticion.url.path in {"/", "/index.html", "/app.js", "/estilos.css"}:
            respuesta.headers["Cache-Control"] = "no-store, max-age=0"
        return respuesta

    # ----- API -------------------------------------------------------------
    @app.get("/api/salud")
    def salud() -> dict:
        """Contra qué cuenta y qué esquema se está trabajando."""
        if app.state.cuenta is None:
            # Se resuelve una sola vez y no se deja caer el servicio si falla:
            # la página lo muestra como desconocido y el problema real saldrá
            # en la prevalidación de la primera corrida, ya explicado.
            try:
                creds = configuracion.cargar_credenciales(cfg)
                app.state.cuenta = drive.quien_soy(drive.construir_servicio(creds))
            except Exception as e:
                logger.warning(
                    "No se pudo saber la cuenta de Google: %s: %s", type(e).__name__, e
                )
                app.state.cuenta = ""
        return gestor.salud(cuenta=app.state.cuenta or None)

    @app.post("/api/corridas", status_code=201)
    def lanzar(peticion: PeticionCorrida):
        try:
            filas = list(peticion.lotes)
            if not filas and (peticion.origen is not None or peticion.destino is not None):
                filas = [PeticionLote(origen=peticion.origen or "", destino=peticion.destino or "")]

            lotes = []
            for numero, fila in enumerate(filas, start=1):
                origen = fila.origen.strip()
                destino = fila.destino.strip()
                if not origen and not destino:
                    continue
                if not origen or not destino:
                    falta = "origen" if not origen else "destino"
                    raise ErrorFlujo(
                        f"La fila {numero} no tiene carpeta de {falta}.",
                        "Completa los dos enlaces o elimina esa fila.",
                        paso="formulario",
                    )
                lotes.append(
                    construir_lote(
                        origen,
                        destino,
                        peticion.etiqueta or "",
                        peticion.cliente or "",
                        fila=numero,
                    )
                )

            trabajo = gestor.lanzar(
                lotes=lotes,
                simular=peticion.simular,
                forzar_carga=peticion.forzar_carga,
            )
        except ErrorFlujo as e:
            # 409 cuando el choque es con otra corrida; 400 si los datos no sirven.
            return _error(409 if "en marcha" in e.motivo else 400, e)
        except Exception as e:
            return _error(400, traducir_excepcion(e, paso="formulario"))
        return {"corrida_id": trabajo.id}

    @app.get("/api/corridas")
    def listar(limite: int = 10) -> dict:
        return {"corridas": gestor.listar(max(1, min(limite, 50)))}

    @app.get("/api/corridas/{corrida_id}")
    def ver(corrida_id: str) -> dict:
        datos = gestor.ver(corrida_id)
        if datos is None:
            raise HTTPException(status_code=404, detail="No existe esa corrida.")
        return datos

    @app.post("/api/corridas/{corrida_id}/cancelar", status_code=202)
    def cancelar(corrida_id: str) -> dict:
        if gestor.obtener(corrida_id) is None:
            raise HTTPException(status_code=404, detail="No existe esa corrida.")
        return {"cancelando": gestor.cancelar(corrida_id)}

    # ----- página ----------------------------------------------------------
    # Se monta al final y en la raíz: las rutas de /api ya están registradas y
    # se comprueban primero, y la página pide «estilos.css» y «app.js» con
    # rutas relativas, así que tienen que colgar de la raíz.
    if ESTATICOS.is_dir():
        app.mount("/", StaticFiles(directory=str(ESTATICOS), html=True), name="pagina")

    return app


app = crear_app()
