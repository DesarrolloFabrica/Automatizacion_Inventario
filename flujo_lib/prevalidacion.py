"""
flujo_lib/prevalidacion.py — revisión previa al run único (antes de tocar Drive).
---------------------------------------------------------------------------------
Antes de clonar nada comprueba, en este orden, que todo lo que el flujo va a
necesitar esté listo: el Excel de rutas, el token único de la cuenta fábrica de
contenidos, el acceso a cada carpeta de origen y destino de los lotes, los
correos de aviso y la conexión a la base de datos (Cloud SQL) con el esquema
indicado.

Nada se lanza como excepción: cada comprobación termina en un Hallazgo (ok,
aviso o error) para mostrarlos todos de una vez y que la persona corrija el
Excel o el .env en una sola pasada. Con --simular, correo y base de datos solo
generan avisos (no impiden arrancar).

psycopg2 se importa solo al conectar, para que este módulo cargue aunque falte.
"""

from __future__ import annotations

import os, re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from . import ROOT, drive
from .excel import Lote, leer_lotes
from .mensajes import traducir_excepcion

NIVELES = ("ok", "aviso", "error")
AREAS = ("excel", "token", "drive", "correo", "db")
VARIABLES_DB = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
PUERTO_DB_DEFECTO = 5432
TIEMPO_CONEXION_DB = 30  # segundos, igual que LMS_Fabrica/cargar_base_gcp.py
CONSULTA_ESQUEMA = "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s"

_PREFIJOS = {"ok": "OK", "aviso": "AVISO", "error": "ERROR"}
_SEPARADOR_CORREOS = re.compile(r"[,;]")


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------
@dataclass
class Hallazgo:
    """Una comprobación: qué se revisó (area), cómo salió (nivel) y qué hacer."""

    nivel: str  # "ok" | "aviso" | "error"
    area: str  # "excel" | "token" | "drive" | "correo" | "db"
    mensaje: str
    accion: str = ""
    detalle: str = ""  # texto técnico; va solo al log, nunca al correo

    def __post_init__(self) -> None:
        if self.nivel not in NIVELES:
            raise ValueError(f"nivel inválido {self.nivel!r}; usa uno de {NIVELES}")
        if self.area not in AREAS:
            raise ValueError(f"area inválida {self.area!r}; usa una de {AREAS}")

    def texto(self) -> str:
        """Línea lista para la consola: 'ERROR <mensaje> <acción>'."""
        return " ".join(p for p in (_PREFIJOS[self.nivel], self.mensaje, self.accion) if p)


@dataclass
class ResultadoPrevalidacion:
    """Todo lo que la prevalidación averiguó y lo que el flujo reutiliza después."""

    hallazgos: list[Hallazgo] = field(default_factory=list)
    lotes: list[Lote] = field(default_factory=list)
    creds: object = None
    svc: object = None
    cuenta: str = ""
    carpetas: dict[str, dict] = field(default_factory=dict)  # id -> dict de obtener_carpeta

    @property
    def ok(self) -> bool:
        """True si no hay ningún hallazgo de nivel error."""
        return not self.errores()

    def errores(self) -> list[Hallazgo]:
        return [h for h in self.hallazgos if h.nivel == "error"]

    def avisos(self) -> list[Hallazgo]:
        return [h for h in self.hallazgos if h.nivel == "aviso"]

    def agregar(self, nivel: str, area: str, mensaje: str, accion: str = "", detalle: str = "") -> Hallazgo:
        """Registra un hallazgo y lo devuelve."""
        hallazgo = Hallazgo(nivel, area, mensaje, accion, detalle)
        self.hallazgos.append(hallazgo)
        return hallazgo


# ---------------------------------------------------------------------------
# Entorno (.env)
# ---------------------------------------------------------------------------
def cargar_env(root: Path = ROOT) -> None:
    """
    Carga el .env de la raíz del repo y, sin pisar lo ya definido, los .env
    antiguos de LMS_Fabrica y CLONACION_CARPETA (por si el equipo aún los usa).
    Las variables ya presentes en el sistema nunca se sobreescriben.
    """
    root = Path(root)
    load_dotenv(root / ".env")
    for carpeta in ("LMS_Fabrica", "CLONACION_CARPETA"):
        load_dotenv(root / carpeta / ".env", override=False)


def correos_aviso(env: Mapping[str, str] = os.environ) -> list[str]:
    """Destinatarios de CORREOS_AVISO separados por coma (o punto y coma), sin vacíos."""
    crudo = env.get("CORREOS_AVISO", "") or ""
    return [c.strip() for c in _SEPARADOR_CORREOS.split(str(crudo)) if c.strip()]


def _parece_correo(texto: str) -> bool:
    usuario, arroba, dominio = texto.partition("@")
    return bool(arroba and usuario and dominio and "@" not in dominio)


def _nivel_falla(simular: bool) -> str:
    """Con --simular, correo y base de datos no frenan el flujo: solo avisan."""
    return "aviso" if simular else "error"


# ---------------------------------------------------------------------------
# Comprobaciones (cada una termina en hallazgos; ninguna lanza)
# ---------------------------------------------------------------------------
def _revisar_excel(res: ResultadoPrevalidacion, excel: Path, log) -> None:
    log(f"Revisando el Excel {excel.name}...")
    try:
        res.lotes = leer_lotes(excel)
    except Exception as e:
        err = traducir_excepcion(e, paso="excel", contexto=excel.name)
        res.agregar("error", "excel", err.motivo, err.accion, err.detalle)
        return
    res.agregar("ok", "excel", f"Excel {excel.name}: {len(res.lotes)} lote(s) por procesar.")


def _revisar_token(
    res: ResultadoPrevalidacion, *, interactivo: bool, cargar_credenciales, construir_servicio, log
) -> None:
    log("Revisando la sesión de Google de la cuenta fábrica de contenidos...")
    try:
        res.creds = cargar_credenciales(interactivo=interactivo)
        svc = construir_servicio(res.creds)
        res.cuenta = drive.quien_soy(svc)
    except Exception as e:
        err = traducir_excepcion(e, paso="token")
        res.agregar("error", "token", err.motivo, err.accion, err.detalle)
        return
    res.svc = svc
    res.agregar("ok", "token", f"Cuenta de Google: {res.cuenta}")


def _revisar_drive(res: ResultadoPrevalidacion, log) -> None:
    """Origen y raíz de destino de cada lote deben existir y ser carpetas accesibles."""
    if res.svc is None or not res.lotes:
        return
    log(f"Revisando el acceso a las carpetas de {len(res.lotes)} lote(s) en Drive...")
    for lote in res.lotes:
        nombres: dict[str, str] = {}
        for papel, folder_id in (("origen", lote.origen_id), ("destino", lote.destino_raiz_id)):
            contexto = f"la carpeta {papel} del lote «{lote.etiqueta}»"
            carpeta = res.carpetas.get(folder_id)  # misma carpeta en varios lotes: una sola consulta
            if carpeta is None:
                try:
                    carpeta = drive.obtener_carpeta(res.svc, folder_id, contexto=contexto)
                except Exception as e:
                    err = traducir_excepcion(e, paso="drive", contexto=contexto)
                    res.agregar("error", "drive", err.motivo, err.accion, err.detalle)
                    continue
                res.carpetas[folder_id] = carpeta
            nombres[papel] = carpeta.get("name") or folder_id
        if len(nombres) == 2:
            res.agregar(
                "ok",
                "drive",
                f"Lote «{lote.etiqueta}»: origen «{nombres['origen']}» y destino "
                f"«{nombres['destino']}» accesibles.",
            )


def _revisar_correo(res: ResultadoPrevalidacion, env: Mapping[str, str], simular: bool, log) -> None:
    log("Revisando los correos de aviso (CORREOS_AVISO)...")
    nivel = _nivel_falla(simular)
    correos = correos_aviso(env)
    if not correos:
        res.agregar(
            nivel,
            "correo",
            "No hay destinatarios de correo configurados.",
            "Agrega CORREOS_AVISO en el archivo .env.",
        )
        return
    malos = [c for c in correos if not _parece_correo(c)]
    if malos:
        res.agregar(
            nivel,
            "correo",
            f"Hay correos de aviso mal escritos: {', '.join(malos)}.",
            "Corrige CORREOS_AVISO en el archivo .env y vuelve a ejecutar.",
        )
        return
    res.agregar("ok", "correo", f"Correos de aviso: {', '.join(correos)}.")


def _conectar_psycopg2(**parametros):
    """Conexión real a Cloud SQL; el import va aquí para no exigir psycopg2 al importar."""
    import psycopg2

    return psycopg2.connect(**parametros)


def _esquema_existe(conn, schema: str) -> bool:
    """SELECT 1 (la conexión responde) y consulta del esquema en information_schema."""
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.execute(CONSULTA_ESQUEMA, (schema,))
        return cur.fetchone() is not None
    finally:
        cur.close()


def _revisar_db(
    res: ResultadoPrevalidacion, env: Mapping[str, str], schema: str, simular: bool, conectar_db, log
) -> None:
    log(f"Revisando la conexión a la base de datos (esquema {schema})...")
    nivel = _nivel_falla(simular)
    faltan = [v for v in VARIABLES_DB if not str(env.get(v) or "").strip()]
    if faltan:
        res.agregar(
            nivel,
            "db",
            f"Faltan datos de la base de datos en el archivo .env: {', '.join(faltan)}.",
            "Completa esas variables en el archivo .env (guíate por .env.example) y vuelve a ejecutar.",
        )
        return
    puerto_crudo = str(env.get("DB_PORT") or "").strip() or str(PUERTO_DB_DEFECTO)
    try:
        puerto = int(puerto_crudo)
    except ValueError:
        res.agregar(
            nivel,
            "db",
            f"El valor de DB_PORT en el archivo .env («{puerto_crudo}») no es un número.",
            f"Corrige DB_PORT (normalmente {PUERTO_DB_DEFECTO}) y vuelve a ejecutar.",
        )
        return
    host = str(env["DB_HOST"]).strip()
    dbname = str(env["DB_NAME"]).strip()
    conectar = conectar_db or _conectar_psycopg2
    conn = None
    try:
        conn = conectar(
            host=host,
            port=puerto,
            dbname=dbname,
            user=str(env["DB_USER"]).strip(),
            password=str(env["DB_PASSWORD"]),
            connect_timeout=TIEMPO_CONEXION_DB,
        )
        existe = _esquema_existe(conn, schema)
    except ImportError as e:
        res.agregar(
            nivel,
            "db",
            "No está instalada la librería psycopg2 para conectar a la base de datos.",
            "Ejecuta pip install psycopg2-binary y vuelve a ejecutar.",
            repr(e),
        )
        return
    except Exception as e:
        err = traducir_excepcion(e, paso="db", contexto=f"la base de datos {dbname}")
        res.agregar(nivel, "db", err.motivo, err.accion, err.detalle)
        return
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # cerrar nunca debe tapar el resultado de la revisión
                pass
    if not existe:
        res.agregar(
            nivel,
            "db",
            f"El esquema {schema} no existe en la base de datos.",
            "Revisa el parámetro --schema.",
        )
        return
    res.agregar(
        "ok", "db", f"Base de datos {dbname} en {host}: conexión correcta y esquema {schema} disponible."
    )


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------
def prevalidar(
    excel: Path,
    *,
    schema: str,
    simular: bool,
    interactivo: bool = True,
    env: Mapping[str, str] | None = None,
    cargar_credenciales=drive.cargar_credenciales,
    construir_servicio=drive.construir_servicio,
    conectar_db=None,
    log=print,
) -> ResultadoPrevalidacion:
    """
    Revisa Excel, token, carpetas de Drive, correos y base de datos, en ese orden,
    y devuelve todos los hallazgos juntos (nunca lanza por un problema esperado).

    - env: variables a usar; None -> cargar_env() y os.environ.
    - interactivo=False: si hace falta autorizar Google se reporta error en vez de pedir login.
    - simular: correo y base de datos pasan de error a aviso.
    - conectar_db: función tipo psycopg2.connect (para pruebas); None -> psycopg2 real.
    - log: recibe líneas de progreso (print, logging.info, ...); None las descarta.
    Las carpetas de Drive consultadas quedan en .carpetas (id -> dict) para no
    volver a pedirlas al resolver destinos y clonar.
    """
    if log is None:
        log = lambda *_a, **_k: None  # noqa: E731
    if env is None:
        cargar_env()
        env = os.environ
    res = ResultadoPrevalidacion()
    _revisar_excel(res, Path(excel), log)
    _revisar_token(
        res,
        interactivo=interactivo,
        cargar_credenciales=cargar_credenciales,
        construir_servicio=construir_servicio,
        log=log,
    )
    _revisar_drive(res, log)
    _revisar_correo(res, env, simular, log)
    _revisar_db(res, env, schema, simular, conectar_db, log)
    return res
