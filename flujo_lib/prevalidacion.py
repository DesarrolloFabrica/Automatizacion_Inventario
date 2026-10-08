"""
flujo_lib/prevalidacion.py — revisión previa al run único (antes de tocar Drive).
---------------------------------------------------------------------------------
Antes de clonar nada comprueba, en este orden, que todo lo que el flujo va a
necesitar esté listo: el Excel de rutas, el token único de la cuenta fábrica de
contenidos, el acceso a cada carpeta de origen y destino de los lotes, a qué
cliente pertenece cada lote, los correos de aviso y la conexión a la base de
datos (Cloud SQL) con el esquema indicado.

Nada se lanza como excepción: cada comprobación termina en un Hallazgo (ok,
aviso o error) para mostrarlos todos de una vez y que la persona corrija el
Excel o el .env en una sola pasada. Con --simular, correo y base de datos solo
generan avisos (no impiden arrancar).

psycopg2 se importa solo al conectar, para que este módulo cargue aunque falte.
"""

from __future__ import annotations

import os, re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from dotenv import load_dotenv

from . import ROOT, drive
from .clasificacion import detectar as detectar_clasificacion
from .excel import CLASIFICACIONES, TEXTO_CLIENTES_VALIDOS, Lote, leer_lotes
from .mensajes import traducir_excepcion

NIVELES = ("ok", "aviso", "error")
AREAS = ("excel", "token", "drive", "cliente", "correo", "db")
VARIABLES_DB = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
PUERTO_DB_DEFECTO = 5432
TIEMPO_CONEXION_DB = 30  # segundos, igual que LMS_Fabrica/cargar_base_gcp.py
CONSULTA_ESQUEMA = "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s"
# Columnas que la base limpia (fabrica1) necesita en archivo para guardar el origen.
COLUMNAS_ORIGEN = ("origen_id", "fecha_origen")
CONSULTA_COLUMNAS_ORIGEN = (
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_schema = %s AND table_name = 'archivo' "
    "AND column_name IN ('origen_id', 'fecha_origen')"
)

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


def _revisar_cliente(res: ResultadoPrevalidacion, log, detectar=None) -> None:
    """
    A qué cliente pertenece cada lote y de qué escuela es.

    Manda la columna `cliente` del Excel. Si viene vacía se mira de qué carpeta
    cuelga el ORIGEN en Drive (ver CLASIFICACIONES en excel.py), y cuando el
    Excel y Drive no coinciden se usa el Excel pero se avisa.

    La escuela sale de la misma búsqueda. Cuando el origen no la da —material
    que vive fuera del árbol LMS_Carga, p. ej. en una carpeta de trabajo— se
    busca en la RAÍZ DE DESTINO, que sí cuelga de CLIENTE / ESCUELA_…. Es solo
    respaldo: lo que diga el origen siempre gana.
    """
    if res.svc is None or not res.lotes:
        return
    detectar = detectar or detectar_clasificacion
    log("Revisando a qué cliente pertenece cada lote...")
    resueltos: list[Lote] = []
    cache: dict[str, object] = {}  # misma carpeta en varios lotes: una sola búsqueda

    def buscar(folder_id: str, papel: str, lote: Lote):
        """Clasificación de esa carpeta, o None. Un fallo se reporta y no corta."""
        if folder_id in cache:
            return cache[folder_id]
        try:
            hallado = detectar(res.svc, folder_id, inicial=res.carpetas.get(folder_id))
        except Exception as e:
            err = traducir_excepcion(
                e, paso="cliente", contexto=f"la carpeta {papel} del lote «{lote.etiqueta}»"
            )
            nivel = "aviso" if lote.clasificacion else "error"
            res.agregar(nivel, "cliente", err.motivo, err.accion, err.detalle)
            return None
        cache[folder_id] = hallado
        return hallado

    for lote in res.lotes:
        if lote.origen_id not in res.carpetas:
            resueltos.append(lote)  # el acceso al origen ya falló: no insistir
            continue
        hallado = buscar(lote.origen_id, "origen", lote)

        # Respaldo por el destino: solo si el origen no resolvió el cliente o
        # dejó la escuela vacía, y si la raíz de destino se pudo leer.
        respaldo = None
        if lote.destino_raiz_id in res.carpetas and (hallado is None or not hallado.escuela):
            respaldo = buscar(lote.destino_raiz_id, "destino", lote)

        if hallado is None and respaldo is None and not lote.clasificacion:
            res.agregar(
                "error",
                "cliente",
                f"No se pudo saber a qué cliente pertenece el lote «{lote.etiqueta}».",
                f"Escribe {TEXTO_CLIENTES_VALIDOS} en la columna cliente del "
                "Excel, o mueve la carpeta dentro de la carpeta del cliente en Drive.",
            )
            resueltos.append(lote)
            continue

        escuela = lote.escuela_gcp or getattr(hallado, "escuela", "")
        # Del destino se hereda el cliente solo si no lo dijeron ni el Excel ni
        # el origen; la escuela, siempre que no la haya dado ya el origen.
        cliente_del_destino = respaldo is not None and hallado is None and not lote.clasificacion
        escuela_del_destino = respaldo is not None and not escuela and bool(respaldo.escuela)
        if escuela_del_destino:
            escuela = respaldo.escuela

        if cliente_del_destino:
            nota = f" (escuela «{escuela}»)" if escuela else ""
            res.agregar(
                "aviso",
                "cliente",
                f"Lote «{lote.etiqueta}»: el origen no cuelga de ninguna carpeta de cliente; "
                f"se toma {respaldo.clasificacion} de la raíz de destino «{respaldo.carpeta}»{nota}.",
                "Si no es correcto, escribe el cliente en el Excel o mueve la carpeta en Drive.",
                detalle=" / ".join(respaldo.ruta),
            )
        elif escuela_del_destino:
            res.agregar(
                "aviso",
                "cliente",
                f"Lote «{lote.etiqueta}»: el origen no dice de qué escuela es; se toma "
                f"«{escuela}», que es de donde cuelga la raíz de destino.",
                "Si no es correcto, mueve la carpeta de destino bajo la escuela que corresponde.",
                detalle=" / ".join(respaldo.ruta),
            )

        if not lote.clasificacion:  # el Excel no lo dijo: mandan las carpetas de Drive
            fuente = hallado or respaldo
            cliente, raiz = CLASIFICACIONES[fuente.clasificacion]
            resueltos.append(
                replace(
                    lote,
                    clasificacion=fuente.clasificacion,
                    cliente_gcp=cliente,
                    raiz_gcp=raiz,
                    escuela_gcp=escuela,
                )
            )
            if fuente is hallado:
                res.agregar(
                    "ok",
                    "cliente",
                    f"Lote «{lote.etiqueta}»: cliente {hallado.clasificacion} detectado "
                    f"en la carpeta «{hallado.carpeta}» de Drive"
                    + (f" (escuela «{escuela}»)." if escuela else "."),
                    detalle=" / ".join(hallado.ruta),
                )
            continue

        # El Excel trae valor: manda, pero se compara con lo que dice Drive.
        if hallado is not None and hallado.clasificacion != lote.clasificacion:
            res.agregar(
                "aviso",
                "cliente",
                f"Lote «{lote.etiqueta}»: el Excel dice {lote.clasificacion} pero en Drive "
                f"la carpeta cuelga de «{hallado.carpeta}» ({hallado.clasificacion}). "
                f"Se usará {lote.clasificacion}, que es lo que dice el Excel.",
                "Si no es correcto, corrige la columna cliente del Excel y vuelve a ejecutar.",
                detalle=" / ".join(hallado.ruta),
            )
        else:
            res.agregar(
                "ok",
                "cliente",
                f"Lote «{lote.etiqueta}»: cliente {lote.clasificacion} según el Excel"
                + (" (coincide con Drive)." if hallado is not None else "."),
            )
        resueltos.append(replace(lote, escuela_gcp=escuela))
    res.lotes[:] = resueltos


def _revisar_escuela(res: ResultadoPrevalidacion, schema: str) -> None:
    """
    La base limpia (fabrica1) no acepta archivos sin escuela: se avisa antes de
    clonar para no descubrirlo al final. Es aviso y no error porque en una raíz
    LMS_CORRECCIONES la escuela sale de cada subcarpeta, no del lote.
    """
    from .gcp import ESCUELAS_OFICIALES, ORIGENES_EXCLUIDOS, es_esquema_limpio, escuela_oficial

    if not es_esquema_limpio(schema):
        return
    for lote in res.lotes:
        if lote.origen_id in ORIGENES_EXCLUIDOS:
            res.agregar(
                "error",
                "drive",
                f"Lote «{lote.etiqueta}»: esa carpeta origen no se carga en {schema}. "
                f"{ORIGENES_EXCLUIDOS[lote.origen_id]}",
                "Quita ese lote del Excel o usa la carpeta válida.",
            )
        escuela = getattr(lote, "escuela_gcp", "")
        if escuela and escuela_oficial(escuela) not in ESCUELAS_OFICIALES:
            res.agregar(
                "error",
                "cliente",
                f"Lote «{lote.etiqueta}»: la escuela «{escuela}» no es una de las oficiales de {schema}.",
                "Revisa la carpeta de la escuela en Drive o agrega la escuela a "
                "ESCUELAS_OFICIALES en flujo_lib/gcp.py.",
            )
        elif not escuela:
            res.agregar(
                "aviso",
                "cliente",
                f"Lote «{lote.etiqueta}»: no se encontró la carpeta ESCUELA_… por encima del "
                f"origen. {schema} exige escuela y la carga de ese lote fallará si no aparece.",
                "Revisa que el origen esté dentro de CLIENTE / ESCUELA_… / PROGRAMA en Drive.",
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


def _columnas_origen_faltantes(conn, schema: str) -> list[str]:
    """Cuáles de origen_id / fecha_origen no existen todavía en archivo."""
    cur = conn.cursor()
    try:
        cur.execute(CONSULTA_COLUMNAS_ORIGEN, (schema,))
        presentes = {str(fila[0]) for fila in cur.fetchall() or []}
    finally:
        cur.close()
    return [c for c in COLUMNAS_ORIGEN if c not in presentes]


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
        from .gcp import MIGRACION_ORIGEN, es_esquema_limpio

        columnas_faltantes = (
            _columnas_origen_faltantes(conn, schema) if existe and es_esquema_limpio(schema) else []
        )
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
    if columnas_faltantes:
        res.agregar(
            nivel,
            "db",
            f"La tabla archivo de {schema} no tiene las columnas {', '.join(columnas_faltantes)} "
            "(ID y fecha de subida del archivo de origen).",
            f"Ejecuta {MIGRACION_ORIGEN} en la base y vuelve a ejecutar.",
        )
        return
    res.agregar(
        "ok", "db", f"Base de datos {dbname} en {host}: conexión correcta y esquema {schema} disponible."
    )


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------
def prevalidar(
    excel: Path | None = None,
    *,
    lotes: list[Lote] | None = None,
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
    Revisa Excel, token, carpetas de Drive, cliente, correos y base de datos, en ese orden,
    y devuelve todos los hallazgos juntos (nunca lanza por un problema esperado).

    - lotes: si se pasan, se usan tal cual y no se lee ningún Excel (lo hace la web).
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
    if lotes is None:
        _revisar_excel(res, Path(excel), log)
    else:
        # La web arma el lote desde el formulario: no hay Excel que revisar.
        res.lotes.extend(lotes)
        res.agregar("ok", "excel", f"{len(lotes)} lote(s) por procesar.")
    _revisar_token(
        res,
        interactivo=interactivo,
        cargar_credenciales=cargar_credenciales,
        construir_servicio=construir_servicio,
        log=log,
    )
    _revisar_drive(res, log)
    _revisar_cliente(res, log)
    _revisar_escuela(res, schema)
    _revisar_correo(res, env, simular, log)
    _revisar_db(res, env, schema, simular, conectar_db, log)
    return res
