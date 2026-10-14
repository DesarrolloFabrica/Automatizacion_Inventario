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
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from flujo_lib import certificados, drive
from flujo_lib.mensajes import ErrorFlujo, traducir_excepcion
from flujo_lib.prevalidacion import cargar_env
from servidor import autenticacion, configuracion, registro
from servidor.autenticacion import AccesoDenegado, AccesoInvalido, ConfiguracionAcceso
from servidor.corridas import Gestor, construir_lote

ESTATICOS = Path(__file__).resolve().parent / "static"
logger = logging.getLogger(__name__)

# Lo único que se sirve sin haber iniciado sesión: la propia pantalla de
# entrada, su hoja de estilos y los tres pasos del ida y vuelta a Google.
PUBLICAS = frozenset(
    {
        "/login",
        "/login.html",
        "/login.js",
        "/estilos.css",
        "/favicon.ico",
        "/api/sesion/iniciar",
        "/api/sesion/callback",
        "/api/sesion/cerrar",
    }
)


class PeticionLote(BaseModel):
    """Una fila origen-destino del formulario."""

    origen: str = ""
    destino: str = ""
    crear_destino: bool = True


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


def crear_app(
    cfg=None,
    gestor: Gestor | None = None,
    acceso: ConfiguracionAcceso | None = None,
    canjear=autenticacion.canjear_codigo,
) -> FastAPI:
    """
    Construye la aplicación.

    cfg, gestor y acceso se inyectan en las pruebas; `canjear` es la llamada a
    Google que cambia el código por la identidad, y se sustituye para no salir
    a la red durante las pruebas.
    """
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
    acceso = acceso or autenticacion.cargar()
    if not acceso.configurado:
        # No se corta el arranque: un servicio caído en Cloud Run reintenta en
        # bucle y no deja ver el motivo. Así arranca, queda en los registros y
        # cualquier petición explica exactamente qué falta configurar.
        logger.error(
            "Falta configurar el acceso con Google: define GOOGLE_OAUTH_CLIENT_ID, "
            "GOOGLE_OAUTH_CLIENT_SECRET y SESION_SECRETO. Nadie podrá entrar."
        )

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
    app.state.acceso = acceso
    app.state.cuenta = None

    @app.middleware("http")
    async def evitar_estaticos_desactualizados(peticion, continuar):
        """Evita mezclar HTML nuevo con JS o CSS guardados por el navegador."""
        respuesta = await continuar(peticion)
        if peticion.url.path in {
            "/", "/index.html", "/app.js", "/estilos.css", "/login", "/login.html", "/login.js",
        }:
            respuesta.headers["Cache-Control"] = "no-store, max-age=0"
        return respuesta

    # ----- acceso ----------------------------------------------------------
    # Se declara después del middleware de caché, así que envuelve a aquel y se
    # ejecuta primero: nada llega al resto de la aplicación sin sesión válida.
    @app.middleware("http")
    async def exigir_sesion(peticion: Request, continuar):
        ruta = peticion.url.path
        if ruta in PUBLICAS:
            return await continuar(peticion)

        if not acceso.configurado:
            return _error(
                503,
                ErrorFlujo(
                    "El acceso con Google todavía no está configurado en este servicio.",
                    "Pide a quien administra el despliegue que defina "
                    "GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET y SESION_SECRETO.",
                    paso="acceso",
                ),
            )

        sesion = autenticacion.leer_sesion(acceso, peticion.cookies.get(autenticacion.COOKIE))
        if sesion is None:
            if ruta.startswith("/api/"):
                # La página está abierta y la sesión caducó: el JS lo detecta
                # por el 401 y manda a la pantalla de entrada.
                return _error(
                    401,
                    ErrorFlujo(
                        "Tu sesión no está activa.",
                        "Vuelve a entrar con tu cuenta de Google.",
                        paso="acceso",
                    ),
                )
            return RedirectResponse(f"/login?destino={quote(peticion.url.path, safe='')}", 303)

        peticion.state.sesion = sesion
        return await continuar(peticion)

    @app.get("/login", include_in_schema=False)
    def pantalla_de_entrada():
        return FileResponse(ESTATICOS / "login.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/sesion/iniciar", include_in_schema=False)
    def iniciar_sesion(peticion: Request, destino: str = "/"):
        if not acceso.configurado:
            return RedirectResponse("/login?error=sin_configurar", 303)
        retorno = autenticacion.direccion_de_retorno(acceso, str(peticion.base_url))
        # El «estado» va firmado: al volver demuestra que la vuelta corresponde
        # a una ida que salió de aquí, y de paso recuerda a dónde iba la persona.
        # Solo rutas internas: «//sitio.malo» también empieza por barra y el
        # navegador lo entendería como otra web.
        interno = destino.startswith("/") and not destino.startswith("//")
        estado = autenticacion.firmar(
            acceso.secreto_sesion,
            {"destino": destino if interno else "/"},
            segundos=autenticacion.VIDA_ESTADO,
        )
        return RedirectResponse(autenticacion.url_autorizacion(acceso, retorno, estado), 303)

    @app.get("/api/sesion/callback", include_in_schema=False)
    def volver_de_google(
        peticion: Request, code: str = "", state: str = "", error: str = ""
    ):
        if error or not code:
            return RedirectResponse("/login?error=cancelado", 303)

        datos_estado = autenticacion.verificar(acceso.secreto_sesion, state)
        if datos_estado is None:
            return RedirectResponse("/login?error=caducado", 303)

        retorno = autenticacion.direccion_de_retorno(acceso, str(peticion.base_url))
        try:
            identidad = canjear(acceso, code, retorno)
            if not autenticacion.permitido(acceso, identidad["correo"]):
                raise AccesoDenegado(identidad["correo"])
        except AccesoDenegado as e:
            logger.warning("Acceso denegado a %s: no está en USUARIOS_PERMITIDOS.", e.correo)
            return RedirectResponse(
                f"/login?error=sin_permiso&correo={quote(e.correo, safe='')}", 303
            )
        except AccesoInvalido as e:
            logger.warning("No se pudo completar la entrada con Google: %s", e)
            return RedirectResponse("/login?error=fallo", 303)

        logger.info("Entra %s", identidad["correo"])
        respuesta = RedirectResponse(datos_estado.get("destino", "/") or "/", 303)
        respuesta.set_cookie(
            autenticacion.COOKIE,
            autenticacion.crear_sesion(acceso, identidad["correo"], identidad["nombre"]),
            max_age=acceso.horas_sesion * 3600,
            httponly=True,
            samesite="lax",
            secure=acceso.cookie_segura,
            path="/",
        )
        return respuesta

    @app.get("/api/sesion/cerrar", include_in_schema=False)
    def cerrar_sesion():
        respuesta = RedirectResponse("/login?error=cerrada", 303)
        respuesta.delete_cookie(autenticacion.COOKIE, path="/")
        return respuesta

    @app.get("/api/sesion")
    def ver_sesion(peticion: Request) -> dict:
        """Quién está dentro. El middleware ya garantizó que hay sesión."""
        sesion = peticion.state.sesion
        return {"correo": sesion["correo"], "nombre": sesion.get("nombre", "")}

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
                        crear_destino=fila.crear_destino,
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
