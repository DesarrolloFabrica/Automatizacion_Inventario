"""
flujo_lib/drive.py — token único y acceso a la API de Google Drive.
-------------------------------------------------------------------
Unifica lo que hoy hacen CLONACION_CARPETA/clone_carpeta_drive.py,
LMS_Fabrica/generar_base_lms.py y CAMBIAR_FORMATO/convertir_jpg_a_png.py:
un solo token.json (Drive + Sheets + Gmail) en la raíz del repo, reintentos
ante fallos transitorios, listado paginado, lectura de carpetas y creación de
carpetas sin duplicar.

Nada de este módulo habla con Google al importarse; solo al llamar funciones.
"""

from __future__ import annotations

import http.client, json, os, random, re, socket, ssl, tempfile, time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from . import ROOT
from .mensajes import ErrorFlujo, traducir_excepcion

RUTA_CREDENCIALES = ROOT / "credentials.json"
RUTA_TOKEN = ROOT / "token.json"
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.send",
]
MIME_FOLDER = "application/vnd.google-apps.folder"
CAMPOS_HIJO = "id, name, mimeType, size, md5Checksum, fileExtension, webViewLink"

_ACCION_RENOVAR = "Ejecuta python renovar_token.py y vuelve a ejecutar."
_ID_DRIVE_RE = re.compile(r"[A-Za-z0-9_-]+")
_FOLDERS_RE = re.compile(r"/folders/([A-Za-z0-9_-]+)")
_ERRORES_RED = (
    http.client.IncompleteRead,
    http.client.RemoteDisconnected,
    socket.timeout,
    socket.gaierror,
    socket.error,
    ConnectionError,
    OSError,
    ssl.SSLError,
    TimeoutError,
)


# ---------------------------------------------------------------------------
# Token único (Drive + Sheets + Gmail)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EstadoToken:
    """Diagnóstico de token.json sin pedir login (para la prevalidación)."""

    existe: bool
    valido: bool
    scopes_completos: bool
    refrescable: bool
    correo: str | None
    detalle: str


def _leer_token(ruta_token: Path) -> tuple[dict | None, Credentials | None, str]:
    """Lee token.json sin red. Devuelve (datos, credenciales, detalle del problema)."""
    nombre = Path(ruta_token).name
    try:
        datos = json.loads(Path(ruta_token).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, None, f"No se pudo leer {nombre}: {e!r}"
    if not isinstance(datos, dict):
        return None, None, f"{nombre} no tiene el formato esperado."
    try:
        # scopes=None -> google-auth toma los scopes guardados en el propio archivo.
        creds = Credentials.from_authorized_user_info(datos)
    except ValueError as e:
        return datos, None, f"{nombre} está incompleto: {e}"
    return datos, creds, ""


def _scopes_completos(datos: dict | None) -> bool:
    """True si el token guardado cubre todos los SCOPES (Drive, Sheets y Gmail)."""
    if not datos:
        return False
    scopes = datos.get("scopes") or []
    if isinstance(scopes, str):
        scopes = scopes.split()
    return set(SCOPES) <= set(scopes)


def _guardar_token(creds: Credentials, ruta_token: Path) -> None:
    """Escribe token.json de forma atómica (temporal en la misma carpeta + os.replace)."""
    ruta_token = Path(ruta_token)
    ruta_token.parent.mkdir(parents=True, exist_ok=True)
    fd, temporal = tempfile.mkstemp(
        prefix=ruta_token.name + ".", suffix=".tmp", dir=str(ruta_token.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        os.replace(temporal, ruta_token)
    except BaseException:
        try:
            os.unlink(temporal)
        except OSError:
            pass
        raise


def estado_token(ruta_token: Path = RUTA_TOKEN) -> EstadoToken:
    """
    Revisa token.json SIN pedir login: existe, vigente, scopes completos, refrescable.
    Si venció y tiene refresh_token intenta refrescarlo (capturando cualquier error);
    no guarda nada. correo sale del campo "account" del archivo (None si no está).
    """
    ruta_token = Path(ruta_token)
    if not ruta_token.is_file():
        return EstadoToken(False, False, False, False, None, f"No existe {ruta_token}.")
    datos, creds, detalle = _leer_token(ruta_token)
    completos = _scopes_completos(datos)
    correo = (datos or {}).get("account") or None
    if creds is None:
        return EstadoToken(True, False, completos, False, correo, detalle)

    refrescable = bool(creds.refresh_token)
    valido = bool(creds.valid)
    if valido:
        detalle = (
            "Token vigente."
            if completos
            else "Token vigente pero sin todos los permisos (Drive, Sheets y Gmail)."
        )
    elif refrescable and completos:
        try:
            creds.refresh(Request())
            valido = bool(creds.valid)
            detalle = "Token vencido; se refrescó sin problema."
        except RefreshError as e:
            refrescable = False
            detalle = f"El token venció y Google no lo renovó (hay que autorizar de nuevo): {e!r}"
        except Exception as e:  # sin red u otro problema: no se puede saber
            detalle = f"El token venció y no se pudo comprobar la renovación (¿sin red?): {e!r}"
    elif refrescable:
        detalle = "Token vencido y sin todos los permisos; hay que autorizar de nuevo."
    else:
        detalle = "Token vencido y sin refresh_token; hay que autorizar de nuevo."
    return EstadoToken(True, valido, completos, refrescable, correo, detalle)


def autorizar(
    ruta_credenciales: Path = RUTA_CREDENCIALES, ruta_token: Path = RUTA_TOKEN
) -> Credentials:
    """
    Login OAuth de escritorio: imprime la URL (no abre el navegador solo) para
    pegarla donde ya está la sesión de la cuenta fábrica de contenidos, y guarda token.json.
    """
    ruta_credenciales = Path(ruta_credenciales)
    ruta_token = Path(ruta_token)
    if not ruta_credenciales.is_file():
        raise ErrorFlujo(
            f"Falta el archivo {ruta_credenciales.name} en {ruta_credenciales.parent}.",
            "Pide a soporte el archivo credentials.json (aplicación de escritorio) de la "
            "cuenta fábrica de contenidos, cópialo en esa carpeta y vuelve a ejecutar.",
            paso="token",
            detalle=str(ruta_credenciales),
        )
    print(
        "No se abre el navegador solo. Copia la URL que aparece abajo y pégala en el "
        "navegador donde YA tienes abierta la cuenta fábrica de contenidos.",
        flush=True,
    )
    flow = InstalledAppFlow.from_client_secrets_file(str(ruta_credenciales), SCOPES)
    creds = flow.run_local_server(
        port=0,
        open_browser=False,
        authorization_prompt_message="Abre esta URL en ese navegador:\n{url}\n",
        success_message="Listo. Ya puedes volver a la terminal. Esta pestaña se puede cerrar.",
    )
    _guardar_token(creds, ruta_token)
    print(f"Credenciales guardadas en {ruta_token}", flush=True)
    return creds


def cargar_credenciales(
    *,
    interactivo: bool = True,
    ruta_token: Path = RUTA_TOKEN,
    ruta_credenciales: Path = RUTA_CREDENCIALES,
) -> Credentials:
    """
    Orden: token vigente con scopes completos -> se usa; vencido con refresh_token
    -> se refresca y se guarda; si hace falta login: interactivo=True -> autorizar(),
    interactivo=False -> ErrorFlujo que manda a ejecutar renovar_token.py.
    """
    ruta_token = Path(ruta_token)
    detalle = f"No existe {ruta_token.name}."
    if ruta_token.is_file():
        datos, creds, detalle = _leer_token(ruta_token)
        if creds is not None:
            if not _scopes_completos(datos):
                detalle = "El token no tiene todos los permisos (Drive, Sheets y Gmail)."
            elif creds.valid:
                return creds
            elif creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    _guardar_token(creds, ruta_token)
                    return creds
                except RefreshError as e:
                    detalle = f"Google no renovó la sesión: {e!r}"
                except (TransportError, OSError) as e:
                    raise ErrorFlujo(
                        "No hay conexión con Google o con la red.",
                        "Revisa la conexión a internet y vuelve a ejecutar.",
                        paso="token",
                        detalle=repr(e),
                    ) from e
            else:
                detalle = "El token venció y no se puede renovar solo."
    if not interactivo:
        raise ErrorFlujo(
            "Hay que autorizar la cuenta fábrica de contenidos en Google.",
            _ACCION_RENOVAR,
            paso="token",
            detalle=detalle,
        )
    print(f"Hace falta autorizar de nuevo en Google ({detalle})", flush=True)
    return autorizar(ruta_credenciales, ruta_token)


def construir_servicio(creds, api: str = "drive", version: str = "v3"):
    """Cliente de la API (drive v3 por defecto; también sheets v4 o gmail v1)."""
    return build(api, version, credentials=creds, static_discovery=True, cache_discovery=False)


# ---------------------------------------------------------------------------
# Reintentos
# ---------------------------------------------------------------------------
def _estado_http(exc: BaseException) -> int | None:
    estado = getattr(getattr(exc, "resp", None), "status", None)
    try:
        return int(estado) if estado is not None else None
    except (TypeError, ValueError):
        return None


def es_error_transitorio(exc: BaseException) -> bool:
    """True si vale la pena reintentar: 408/429/5xx o un fallo de red/socket."""
    estado = _estado_http(exc)
    if estado is not None:
        return estado in (408, 429) or 500 <= estado <= 599
    return isinstance(exc, _ERRORES_RED)


def _espera(intento: int) -> float:
    return min(2**intento * (0.4 + random.random() * 0.4), 90.0)


def ejecutar(req, max_intentos: int = 12, num_retries_http: int = 5, dormir=time.sleep):
    """
    Ejecuta una petición de la API con reintentos ante fallos transitorios.
    400/401/403/404 (y cualquier otro error no transitorio) se relanzan de una.
    """
    ultimo: BaseException | None = None
    for intento in range(max_intentos):
        try:
            return req.execute(num_retries=num_retries_http)
        except Exception as e:
            if not es_error_transitorio(e):
                raise
            ultimo = e
        if intento < max_intentos - 1:
            dormir(_espera(intento))
    if ultimo is not None:
        raise ultimo
    raise RuntimeError("Error desconocido al ejecutar la petición")


# ---------------------------------------------------------------------------
# Carpetas y archivos
# ---------------------------------------------------------------------------
def extraer_id_carpeta(texto: str) -> str | None:
    """
    Acepta ".../folders/ID", ".../open?id=ID", "?id=ID" o el ID suelto
    (con o sin comillas y espacios). Devuelve el ID o None si no es usable.
    """
    if texto is None:
        return None
    limpio = str(texto).strip().strip("\"'").strip()
    if not limpio:
        return None
    coincidencia = _FOLDERS_RE.search(limpio)
    if coincidencia:
        return coincidencia.group(1)
    if "id=" in limpio:
        consulta = urlparse(limpio).query
        if not consulta and limpio.startswith("id="):
            consulta = limpio
        candidato = (parse_qs(consulta).get("id") or [""])[0].strip()
        if candidato and _ID_DRIVE_RE.fullmatch(candidato):
            return candidato
    if _ID_DRIVE_RE.fullmatch(limpio):
        return limpio
    return None


def _escapar_q(texto: str) -> str:
    """Escapa \\ y ' para meter un valor entre comillas simples dentro de q."""
    return str(texto).replace("\\", "\\\\").replace("'", "\\'")


def listar_hijos(
    svc,
    parent_id: str,
    *,
    solo_carpetas: bool = False,
    campos: str = CAMPOS_HIJO,
    page_size: int = 1000,
    ejecutar=ejecutar,
) -> list[dict]:
    """
    Hijos directos de una carpeta (no recursivo), sin papelera, con Shared Drives.
    Pagina hasta agotar nextPageToken.
    """
    q = f"'{_escapar_q(parent_id)}' in parents and trashed = false"
    if solo_carpetas:
        q += f" and mimeType = '{MIME_FOLDER}'"
    salida: list[dict] = []
    pagina = None
    while True:
        r = ejecutar(
            svc.files().list(
                q=q,
                spaces="drive",
                fields=f"nextPageToken, files({campos})",
                pageToken=pagina,
                pageSize=page_size,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
            )
        )
        salida.extend(r.get("files", []))
        pagina = r.get("nextPageToken")
        if not pagina:
            break
    return salida


def obtener_carpeta(svc, folder_id: str, *, ejecutar=ejecutar, contexto: str = "") -> dict:
    """
    files().get de una carpeta activa (id, name, mimeType, driveId, trashed).
    Si el ID no es una carpeta o Drive falla, lanza ErrorFlujo con frase clara.
    """
    try:
        carpeta = ejecutar(
            svc.files().get(
                fileId=folder_id,
                fields="id, name, mimeType, driveId, trashed",
                supportsAllDrives=True,
            )
        )
    except ErrorFlujo:
        raise
    except Exception as e:
        raise traducir_excepcion(e, paso="drive", contexto=contexto) from e
    if carpeta.get("mimeType") != MIME_FOLDER:
        que = f"{contexto[:1].upper()}{contexto[1:]}" if contexto else "El enlace indicado"
        raise ErrorFlujo(
            f"{que} no es una carpeta de Drive.",
            "Revisa el enlace en el Excel.",
            paso="drive",
            contexto=contexto,
            detalle=f"id={folder_id} mimeType={carpeta.get('mimeType')}",
        )
    if carpeta.get("trashed"):
        que = f"{contexto[:1].upper()}{contexto[1:]}" if contexto else "La carpeta indicada"
        raise ErrorFlujo(
            f"{que} está en la papelera de Drive.",
            "Restáurala o corrige el enlace en el Excel.",
            paso="drive",
            contexto=contexto,
            detalle=f"id={folder_id} trashed=true",
        )
    return carpeta


def crear_carpeta(
    svc, nombre: str, parent_id: str, *, listar=listar_hijos, dormir=time.sleep
) -> tuple[dict, bool]:
    """
    Crea la carpeta `nombre` dentro de parent_id sin duplicarla.
    Devuelve ({"id", "name"}, creada). Ante un fallo transitorio vuelve a listar
    las subcarpetas: si Drive ya la creó antes de fallar, la devuelve (creada=True).
    400/401/403/404 se relanzan sin reintentar. Máximo 6 intentos.
    """
    ultimo: BaseException | None = None
    for intento in range(6):
        try:
            creada = (
                svc.files()
                .create(
                    body={"name": nombre, "parents": [parent_id], "mimeType": MIME_FOLDER},
                    fields="id, name",
                    supportsAllDrives=True,
                )
                .execute(num_retries=0)
            )
            return {"id": creada["id"], "name": creada.get("name", nombre)}, True
        except Exception as e:
            if not es_error_transitorio(e):
                raise
            ultimo = e
        # Puede que Drive sí la haya creado antes de fallar: buscarla por nombre exacto.
        for hijo in listar(svc, parent_id, solo_carpetas=True):
            if hijo.get("name") == nombre:
                return {"id": hijo["id"], "name": hijo["name"]}, True
        if intento < 5:
            dormir(_espera(intento))
    assert ultimo is not None
    raise ultimo


def enviar_a_papelera(svc, file_id: str, *, ejecutar=ejecutar) -> None:
    """Marca como trashed (papelera, sin borrado permanente). 404/410 se ignoran."""
    try:
        ejecutar(
            svc.files().update(fileId=file_id, body={"trashed": True}, supportsAllDrives=True)
        )
    except HttpError as e:
        if _estado_http(e) not in (404, 410):
            raise


def quien_soy(svc, *, ejecutar=ejecutar) -> str:
    """Correo de la cuenta dueña del token, o "(desconocido)"."""
    r = ejecutar(svc.about().get(fields="user(emailAddress)"))
    return (r.get("user") or {}).get("emailAddress") or "(desconocido)"
