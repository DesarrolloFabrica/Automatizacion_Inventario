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
from servidor import configuracion
from servidor.corridas import Gestor

ESTATICOS = Path(__file__).resolve().parent / "static"
logger = logging.getLogger(__name__)


class PeticionCorrida(BaseModel):
    """Lo que manda el formulario de la página."""

    origen: str = Field(min_length=1)
    destino: str = Field(min_length=1)
    etiqueta: str | None = None
    cliente: str | None = None
    simular: bool | None = None
    forzar_carga: bool | None = None


def _error(codigo: int, err: ErrorFlujo) -> JSONResponse:
    """Los errores viajan siempre como «qué pasó» + «qué hacer»."""
    return JSONResponse(status_code=codigo, content={"motivo": err.motivo, "accion": err.accion})


def crear_app(cfg=None, gestor: Gestor | None = None) -> FastAPI:
    """Construye la aplicación. cfg y gestor se inyectan en las pruebas."""
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
                logger.warning("No se pudo saber la cuenta de Google: %r", e)
                app.state.cuenta = ""
        return gestor.salud(cuenta=app.state.cuenta or None)

    @app.post("/api/corridas", status_code=201)
    def lanzar(peticion: PeticionCorrida):
        try:
            trabajo = gestor.lanzar(
                origen=peticion.origen,
                destino=peticion.destino,
                etiqueta=peticion.etiqueta or "",
                cliente=peticion.cliente or "",
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
