"""
run_flujo.py — run único de la fábrica de contenidos (CUN).
-----------------------------------------------------------
Un solo comando hace todo a partir de RUTAS.xlsx (cliente | etiqueta | origen |
destino), en un solo proceso y con un solo token (Drive + Sheets + Gmail) de la
cuenta fábrica de contenidos:

  1) Prevalida: Excel, sesión de Google, acceso a las carpetas de cada lote,
     correos de aviso y conexión a la base de datos. Si algo falla, no arranca
     y explica qué corregir.
  2) Por lote: crea (o reutiliza) dentro de la carpeta `destino` del Excel una
     carpeta con el nombre EXACTO del origen y clona ahí. La clonación es
     reanudable (solo copia lo que falta) y compara por nombre canónico, así
     x.JPG del origen equivale a x.png del clon.
  3) Guarda el estado por lote en corridas/<Excel>.estado.json (y un Excel
     legible .estado.xlsx) para retomar sin duplicar, y escribe el log de la
     corrida en corridas/logs/<corrida>_<Excel>.log.

Uso (PowerShell, desde la raíz del repo):

  python run_flujo.py --excel "<RUTA>\\RUTAS.xlsx"
  python run_flujo.py --excel "<RUTA>\\RUTAS.xlsx" --solo-prevalidar
  python run_flujo.py --excel "<RUTA>\\RUTAS.xlsx" --rehacer clonacion
  python run_flujo.py --excel "<RUTA>\\RUTAS.xlsx" --simular --no-interactivo

Opciones: --schema (default fabrica_pruebas; producción: --schema fabrica),
--simular, --forzar-carga,
--solo-prevalidar, --rehacer {destino,clonacion,formato,verificacion,carga,todo}
(repetible) y --no-interactivo. Si hay que autorizar Google de nuevo:
python renovar_token.py.

Códigos de salida: 0 todos los lotes convertidos y verificados; 1 prevalidación
fallida o algún lote fallido; 2 hay lotes con diferencias retenidos por la compuerta.

Estado de la migración: fase 3
------------------------------
HACE el flujo completo: prevalidación, destino, clonación, conversión JPG→PNG,
verificación, compuerta, carga transaccional, inventario y correo único final.
Los scripts antiguos no se modifican.
"""

from __future__ import annotations

import argparse, logging, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flujo_lib import certificados, drive  # noqa: E402
from flujo_lib.clonacion import clonar_arbol  # noqa: E402
from flujo_lib.destino import describir, resolver_destino  # noqa: E402
from flujo_lib.estado import DIR_CORRIDAS, ETIQUETAS_ESTADO, PASOS, EstadoCorrida  # noqa: E402
from flujo_lib.excel import Lote, resolver_excel  # noqa: E402
from flujo_lib.formato import convertir_arbol  # noqa: E402
from flujo_lib.gcp import cargar_lote, escanear_lote  # noqa: E402
from flujo_lib.inventario import generar_inventario, publicar_inventario  # noqa: E402
from flujo_lib.mensajes import ErrorFlujo, traducir_excepcion  # noqa: E402
from flujo_lib.notificar import construir_mensaje, enviar_correo  # noqa: E402
from flujo_lib.prevalidacion import ResultadoPrevalidacion, correos_aviso, prevalidar  # noqa: E402
from flujo_lib.progreso import Reporte  # noqa: E402
from flujo_lib.verificacion import verificar_lote  # noqa: E402

SCHEMA_PRODUCCION = "fabrica"
# Mientras se valida el run único se carga al esquema de pruebas.
# Para producción hay que pedirlo a mano: --schema fabrica.
SCHEMA_DEFECTO = "fabrica_pruebas"
PASOS_REHACER = PASOS + ("todo",)
NOTA_OMITIDO = "No se ejecutó porque falló el paso anterior"
NOTA_CANCELADO = "No se ejecutó: la corrida se canceló"
NOTA_FASES = "Run único completado: Drive, verificación, inventario, Cloud SQL y notificación."
ACCION_REINTENTAR_COPIA = (
    "Vuelve a ejecutar; solo se copiará lo que falta. "
    "Si persiste, revisa permisos de esos archivos."
)
FORMATO_LOG = "%(asctime)s %(levelname)s %(message)s"
_NIVEL_HALLAZGO = {"ok": logging.INFO, "aviso": logging.WARNING, "error": logging.ERROR}
# Librerías de Google muy habladoras: solo sus avisos y errores van al log.
_LOGGERS_RUIDOSOS = ("googleapiclient", "google", "urllib3", "httplib2", "oauthlib", "requests_oauthlib")


# ---------------------------------------------------------------------------
# Línea de comandos
# ---------------------------------------------------------------------------
def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_flujo.py",
        description=(
            "Run único de la fábrica de contenidos: prevalida, clona, convierte, "
            "verifica, carga e inventaría cada lote de RUTAS.xlsx (flujo completo)."
        ),
        epilog='Ejemplo: python run_flujo.py --excel "C:\\ruta\\RUTAS.xlsx"',
    )
    parser.add_argument(
        "--excel",
        type=Path,
        default=None,
        help=(
            "Ruta a RUTAS.xlsx. Si se omite se busca en la variable RUTAS_XLSX, "
            "la raíz del repo, CLONACION_CARPETA, LMS_Fabrica y la carpeta actual."
        ),
    )
    parser.add_argument(
        "--schema",
        default=SCHEMA_DEFECTO,
        help=(
            f"Esquema de Cloud SQL donde se cargará (default: {SCHEMA_DEFECTO}, el de pruebas). "
            f"Para producción hay que indicarlo a mano: --schema {SCHEMA_PRODUCCION}."
        ),
    )
    parser.add_argument(
        "--simular",
        action="store_true",
        help=(
            "Hace todo menos escribir en la base de datos y enviar correo "
            "(en la prevalidación, base y correo solo avisan)."
        ),
    )
    parser.add_argument(
        "--forzar-carga",
        action="store_true",
        help=(
            "Carga aunque la verificación tenga diferencias "
            "(por defecto apagado)."
        ),
    )
    parser.add_argument(
        "--solo-prevalidar",
        action="store_true",
        help="Solo revisa Excel, sesión de Google, carpetas, correos y base de datos; no clona nada.",
    )
    parser.add_argument(
        "--rehacer",
        action="append",
        choices=PASOS_REHACER,
        default=None,
        help=(
            "Repite ese paso aunque el estado diga que ya quedó bien "
            "(se puede repetir la opción; todo = todos los pasos)."
        ),
    )
    parser.add_argument(
        "--no-interactivo",
        action="store_true",
        help="Si hace falta autorizar Google, falla con un mensaje en vez de pedir el login.",
    )
    # Solo para pruebas: dónde guardar el estado y los logs (default corridas/ del repo).
    parser.add_argument("--dir-corridas", type=Path, default=DIR_CORRIDAS, help=argparse.SUPPRESS)
    return parser


def pasos_a_rehacer(valores) -> set[str]:
    """Convierte la lista de --rehacer en el conjunto de pasos a repetir ("todo" = todos)."""
    pedidos = set(valores or [])
    return set(PASOS) if "todo" in pedidos else pedidos


def pasos_efectivos_a_rehacer(valores) -> set[str]:
    """Incluye dependientes posteriores para no conservar resultados obsoletos."""
    pedidos = pasos_a_rehacer(valores)
    if not pedidos:
        return set()
    primero = min(PASOS.index(p) for p in pedidos)
    return set(PASOS[primero:])


def argumentos_corrida(args: argparse.Namespace, excel: Path) -> dict:
    """Lo que se guarda en el historial de corridas del estado."""
    return {
        "excel": str(excel),
        "schema": args.schema,
        "simular": bool(args.simular),
        "forzar_carga": bool(args.forzar_carga),
        "solo_prevalidar": bool(args.solo_prevalidar),
        "rehacer": [p for p in PASOS if p in pasos_a_rehacer(args.rehacer)],
        "no_interactivo": bool(args.no_interactivo),
    }


# ---------------------------------------------------------------------------
# Consola y log
# ---------------------------------------------------------------------------
def _utf8_en_consola() -> None:
    """Acentos y «» se ven bien en PowerShell aunque la consola no esté en UTF-8."""
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            try:
                flujo.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def configurar_logging(ruta_log: Path, stream=None) -> list[logging.Handler]:
    """
    Logger raíz con archivo (utf-8, todo incluido el detalle técnico) y consola
    (solo INFO en adelante). Devuelve los handlers para cerrarlos al terminar.
    """
    ruta_log = Path(ruta_log)
    ruta_log.parent.mkdir(parents=True, exist_ok=True)
    formato = logging.Formatter(FORMATO_LOG)
    archivo = logging.FileHandler(ruta_log, encoding="utf-8")
    archivo.setLevel(logging.DEBUG)
    consola = logging.StreamHandler(stream if stream is not None else sys.stdout)
    consola.setLevel(logging.INFO)
    raiz = logging.getLogger()
    raiz.setLevel(logging.DEBUG)
    for handler in (archivo, consola):
        handler.setFormatter(formato)
        raiz.addHandler(handler)
    for nombre in _LOGGERS_RUIDOSOS:
        logging.getLogger(nombre).setLevel(logging.WARNING)
    return [archivo, consola]


def cerrar_logging(handlers: list[logging.Handler]) -> None:
    """Quita y cierra los handlers de la corrida (libera el archivo de log)."""
    raiz = logging.getLogger()
    for handler in handlers:
        try:
            handler.flush()
        finally:
            raiz.removeHandler(handler)
            handler.close()
    raiz.setLevel(logging.WARNING)  # nivel por defecto de logging


def imprimir_hallazgos(res: ResultadoPrevalidacion) -> None:
    """
    Una línea por hallazgo ("OK ", "AVISO ", "ERROR " + mensaje + acción).

    El detalle técnico va a nivel depuración: en la terminal queda solo en el
    archivo de la corrida, y el servicio web escucha ese nivel para que en los
    registros del servidor sí se vea. Nunca llega a la página ni al correo.
    """
    logging.info("Resultado de la prevalidación:")
    for h in res.hallazgos:
        logging.log(_NIVEL_HALLAZGO.get(h.nivel, logging.INFO), h.texto())
        if h.detalle:
            logging.debug("    detalle técnico (%s): %s", h.area, h.detalle)


def imprimir_resumen(estado: EstadoCorrida, lotes: list[Lote]) -> None:
    """Tabla de texto alineada con todos los pasos por lote."""
    claves = {lote.clave for lote in lotes}
    filas = [r for r in estado.resumen() if r["clave"] in claves]
    cabecera = ("Etiqueta", "Carpeta destino", "Destino", "Clonación", "Formato", "Verificación", "Carga")
    tabla = [
        (
            str(r["etiqueta"]),
            str(r["destino_nombre"] or "-"),
            ETIQUETAS_ESTADO.get(r["pasos"]["destino"], r["pasos"]["destino"]),
            ETIQUETAS_ESTADO.get(r["pasos"]["clonacion"], r["pasos"]["clonacion"]),
            ETIQUETAS_ESTADO.get(r["pasos"]["formato"], r["pasos"]["formato"]),
            ETIQUETAS_ESTADO.get(r["pasos"]["verificacion"], r["pasos"]["verificacion"]),
            ETIQUETAS_ESTADO.get(r["pasos"]["carga"], r["pasos"]["carga"]),
        )
        for r in filas
    ]
    anchos = [max(len(f[i]) for f in [cabecera, *tabla]) for i in range(len(cabecera))]

    def linea(valores) -> str:
        return "  " + "  ".join(v.ljust(a) for v, a in zip(valores, anchos)).rstrip()

    logging.info("Resumen por lote:")
    logging.info(linea(cabecera))
    logging.info(linea(tuple("-" * a for a in anchos)))
    for fila in tabla:
        logging.info(linea(fila))


def imprimir_rutas(estado: EstadoCorrida, ruta_log: Path, ruta_xlsx: Path | None) -> None:
    logging.info("Estado de la corrida (JSON) : %s", estado.ruta_json)
    if ruta_xlsx is not None:
        logging.info("Estado de la corrida (Excel): %s", ruta_xlsx)
    logging.info("Log de esta corrida         : %s", ruta_log)


def exportar_estado(estado: EstadoCorrida) -> Path | None:
    """Exporta el Excel de estado sin tumbar la corrida si está abierto en Excel."""
    try:
        return estado.exportar_excel()
    except ErrorFlujo as e:
        logging.warning("No se pudo actualizar el Excel de estado. %s", e)
        if e.detalle:
            logging.debug("    detalle: %s", e.detalle)
        return None


# ---------------------------------------------------------------------------
# Pasos por lote
# ---------------------------------------------------------------------------
def _carpeta(svc, carpetas: dict[str, dict], folder_id: str, contexto: str) -> dict:
    """Carpeta (id, name) ya consultada en la prevalidación; si falta, se pide a Drive."""
    carpeta = carpetas.get(folder_id)
    if carpeta is None:
        carpeta = drive.obtener_carpeta(svc, folder_id, contexto=contexto)
        carpetas[folder_id] = carpeta
    return carpeta


def _fallar_paso(estado: EstadoCorrida, lote: Lote, paso: str, err: ErrorFlujo) -> None:
    """Marca el paso como fallido y los siguientes como omitidos; lo cuenta en el log."""
    logging.error("Lote «%s»: %s", lote.etiqueta, err)
    if err.detalle:
        logging.debug("    detalle técnico: %s", err.detalle)
    estado.marcar(
        lote.clave, paso, "fallido", motivo=err.motivo, accion=err.accion, detalle=err.detalle
    )
    for siguiente in PASOS[PASOS.index(paso) + 1 :]:
        estado.marcar(lote.clave, siguiente, "omitido", detalle=NOTA_OMITIDO)


def resolver_paso_destino(
    estado: EstadoCorrida, svc, lote: Lote, carpetas: dict[str, dict], rehacer: set[str]
) -> tuple[str, str] | None:
    """
    Paso "destino": carpeta con el nombre del origen dentro de la raíz del Excel.
    Devuelve (destino_id, destino_nombre) o None si falló (ya marcado en el estado).
    """
    clave = lote.clave
    destino_id, destino_nombre = estado.destino_de(clave)
    if estado.completado(clave, "destino") and "destino" not in rehacer and destino_id:
        try:
            drive.obtener_carpeta(
                svc,
                destino_id,
                contexto=f"la carpeta destino guardada del lote «{lote.etiqueta}»",
            )
        except Exception as e:
            logging.warning(
                "Lote «%s»: la carpeta destino guardada ya no está disponible; "
                "se volverá a buscar dentro de la raíz. Detalle: %s",
                lote.etiqueta,
                traducir_excepcion(e, paso="destino").motivo,
            )
        else:
            logging.info(
                "Lote «%s»: carpeta destino ya resuelta en una corrida anterior: «%s» "
                "(usa --rehacer destino para volver a buscarla).",
                lote.etiqueta,
                destino_nombre,
            )
            return destino_id, destino_nombre

    contexto = f"la carpeta destino del lote «{lote.etiqueta}»"
    try:
        origen = _carpeta(svc, carpetas, lote.origen_id, f"la carpeta origen del lote «{lote.etiqueta}»")
        raiz = _carpeta(svc, carpetas, lote.destino_raiz_id, contexto)
        res = resolver_destino(svc, origen, raiz)
    except Exception as e:
        _fallar_paso(estado, lote, "destino", traducir_excepcion(e, paso="destino", contexto=contexto))
        return None
    destino_anterior = destino_id
    estado.set_destino(clave, res.id, res.nombre)
    if destino_anterior and destino_anterior != res.id:
        for paso in PASOS[1:]:
            estado.marcar(
                clave,
                paso,
                "pendiente",
                detalle="Debe repetirse porque cambió la carpeta destino",
            )
    detalle = describir(res)
    if res.avisos:
        detalle += ". Avisos: " + " ".join(res.avisos)
    estado.marcar(clave, "destino", "ok", detalle=detalle)
    logging.info("Lote «%s»: %s.", lote.etiqueta, describir(res))
    for aviso in res.avisos:
        logging.warning("Lote «%s»: %s", lote.etiqueta, aviso)
    return res.id, res.nombre


def clonar_lote(
    estado: EstadoCorrida, svc, lote: Lote, destino_id: str, destino_nombre: str,
    rehacer: set[str], reporte=None,
) -> bool:
    """Paso "clonacion": copia el origen dentro de la carpeta destino. True si quedó completo."""
    clave = lote.clave
    if estado.completado(clave, "clonacion") and "clonacion" not in rehacer:
        logging.info(
            "Lote «%s»: ya clonado en una corrida anterior "
            "(usa --rehacer clonacion para volver a sincronizar).",
            lote.etiqueta,
        )
        return True

    logging.info("Lote «%s»: clonando el origen dentro de «%s»...", lote.etiqueta, destino_nombre)
    estado.marcar(clave, "clonacion", "en_curso", detalle=f"Clonando en «{destino_nombre}» ({destino_id})")
    try:
        resumen = clonar_arbol(
            svc, lote.origen_id, destino_id, log=logging.info,
            avance=_avance_de(reporte, lote, "clonacion"),
        )
    except Exception as e:
        err = traducir_excepcion(e, paso="clonacion", contexto=f"el lote «{lote.etiqueta}»")
        _fallar_paso(estado, lote, "clonacion", err)
        return False
    if not resumen.ok():
        err = ErrorFlujo(
            f"No se pudieron copiar {resumen.archivos_fallidos} archivo(s) del lote «{lote.etiqueta}».",
            ACCION_REINTENTAR_COPIA,
            paso="clonacion",
            detalle="\n".join(resumen.fallidos),
            contexto=f"el lote «{lote.etiqueta}»",
        )
        _fallar_paso(estado, lote, "clonacion", err)
        return False
    estado.marcar(clave, "clonacion", "ok", detalle=resumen.texto())
    logging.info("Lote «%s»: clonación completa. %s", lote.etiqueta, resumen.texto())
    return True


def convertir_lote(estado: EstadoCorrida, svc, lote: Lote, destino_id: str, rehacer: set[str], reporte=None) -> bool:
    clave = lote.clave
    if estado.completado(clave, "formato") and "formato" not in rehacer:
        logging.info("Lote «%s»: formato ya completado en una corrida anterior.", lote.etiqueta)
        return True
    estado.marcar(clave, "formato", "en_curso", detalle="Convirtiendo JPG/JPEG a PNG en el clon")
    try:
        resumen = convertir_arbol(
            svc, destino_id, log=logging.info,
            avance=_avance_de(reporte, lote, "formato"),
        )
    except Exception as e:
        _fallar_paso(estado, lote, "formato", traducir_excepcion(e, paso="formato", contexto=f"el lote «{lote.etiqueta}»"))
        return False
    if not resumen.ok():
        _fallar_paso(estado, lote, "formato", ErrorFlujo(
            f"No se pudieron convertir {len(resumen.errores)} archivo(s) del lote «{lote.etiqueta}».",
            "Vuelve a ejecutar. Si persiste, revisa que los JPG sean imágenes válidas.",
            paso="formato", detalle="\n".join(resumen.errores),
        ))
        return False
    estado.marcar(clave, "formato", "ok", detalle=resumen.texto())
    return True


def _avance_de(reporte, lote: Lote, paso: str):
    """
    Puente entre los pasos y el reporte de avance del frontend.
    Sin reporte devuelve None y los pasos se comportan exactamente como siempre.
    """
    if reporte is None:
        return None
    return lambda hechos, total, mensaje="": reporte.fijar(lote.clave, paso, hechos, total, mensaje)


def _meta_gcp(lote: Lote) -> dict[str, str]:
    """Cliente, raíz y escuela del lote tal como se guardan en Cloud SQL."""
    meta = {"cliente": lote.cliente_gcp, "raiz": lote.raiz_gcp}
    if getattr(lote, "escuela_gcp", ""):
        meta["escuela"] = lote.escuela_gcp
    return meta


def verificar_paso(estado: EstadoCorrida, svc, lote: Lote, destino_id: str, rehacer: set[str], reporte=None) -> bool:
    clave = lote.clave
    if estado.completado(clave, "verificacion") and "verificacion" not in rehacer:
        return True
    try:
        resultado = verificar_lote(
            svc, lote.origen_id, destino_id,
            programa=estado.destino_de(clave)[1] or lote.etiqueta,
            meta=_meta_gcp(lote),
            avance=_avance_de(reporte, lote, "verificacion"),
        )
    except Exception as e:
        _fallar_paso(
            estado, lote, "verificacion",
            traducir_excepcion(e, paso="verificacion", contexto=f"el lote «{lote.etiqueta}»"),
        )
        return False
    detalle = "\n".join(resultado.hallazgos) if resultado.hallazgos else resultado.texto()
    estado.marcar(clave, "verificacion", resultado.estado, detalle=detalle)
    if resultado.estado == "con_diferencias":
        logging.warning("Lote «%s»: %s Revisa el detalle antes de cargar.", lote.etiqueta, resultado.texto())
        return False
    logging.info("Lote «%s»: verificación correcta.", lote.etiqueta)
    return True


def cargar_paso(estado: EstadoCorrida, svc, lote: Lote, destino_id: str, destino_nombre: str, *, schema: str, simular: bool, rehacer: set[str], reporte=None) -> bool:
    clave = lote.clave
    if estado.completado(clave, "carga") and "carga" not in rehacer:
        logging.info("Lote «%s»: carga ya completada en una corrida anterior.", lote.etiqueta)
        return True
    estado.marcar(clave, "carga", "en_curso", detalle="Preparando archivos indexables")
    try:
        filas = escanear_lote(
            svc, destino_id, lote, destino_nombre,
            avance=_avance_de(reporte, lote, "carga"),
        )
        stats = cargar_lote(
            filas, schema=schema, simular=simular, log=logging.info,
            avance=_avance_de(reporte, lote, "carga"),
        )
    except Exception as e:
        _fallar_paso(estado, lote, "carga", traducir_excepcion(e, paso="carga", contexto=f"el lote «{lote.etiqueta}»"))
        return False
    programas = sorted({f.get("programa_nombre", "") for f in filas if f.get("programa_nombre")})
    eliminados = stats.get("eliminados", 0)
    if simular:
        detalle = f"Simulación: {stats.get('simulados', 0)} archivo(s) listo(s); no se escribió en Cloud SQL."
    else:
        reemplazo = f"{eliminados} reemplazado(s), " if eliminados else ""
        detalle = (
            f"{reemplazo}{stats.get('insertados', 0)} insertado(s), "
            f"{stats.get('existentes', 0)} ya existente(s)."
        )
    estado.marcar(clave, "carga", "ok", detalle=detalle, estadisticas=stats, programas=programas)
    logging.info("Lote «%s»: %s", lote.etiqueta, detalle)
    return True


def procesar_lote(
    estado: EstadoCorrida, svc, lote: Lote, carpetas: dict[str, dict], rehacer: set[str],
    forzar_carga: bool = False, schema: str = SCHEMA_DEFECTO, simular: bool = False,
    reporte=None, cancelado=None,
) -> bool:
    """
    Destino → clonación → formato → verificación → compuerta.

    `cancelado` es un callable que dice si alguien pidió parar. Se mira entre
    pasos, no dentro de ellos: un paso en marcha termina lo que está haciendo
    (parar una copia a medias dejaría Drive en un estado raro) y no se empieza
    el siguiente. Lo hecho queda en el estado, así que se puede retomar.
    """
    def _parar() -> bool:
        if not (cancelado and cancelado()):
            return False
        for paso in PASOS:
            if estado.paso(lote.clave, paso).get("estado") in {"pendiente", "en_curso"}:
                estado.marcar(lote.clave, paso, "omitido", detalle=NOTA_CANCELADO)
        logging.warning("Lote «%s»: cancelado a petición de quien lanzó la corrida.", lote.etiqueta)
        return True

    estado.registrar_lote(lote)
    logging.info("=" * 60)
    logging.info("Lote «%s» (fila %s, cliente %s)", lote.etiqueta, lote.fila, lote.cliente_excel)
    destino = resolver_paso_destino(estado, svc, lote, carpetas, rehacer)
    if destino is None:
        return False
    if _parar():
        return False
    if not clonar_lote(estado, svc, lote, destino[0], destino[1], rehacer, reporte):
        return False
    if _parar():
        return False
    if not convertir_lote(estado, svc, lote, destino[0], rehacer, reporte):
        return False
    if _parar():
        return False
    verificado = verificar_paso(estado, svc, lote, destino[0], rehacer, reporte)
    if not verificado and not forzar_carga:
        estado.marcar(lote.clave, "carga", "omitido", detalle="Retenido por la compuerta: la verificación encontró diferencias")
        return False
    if _parar():
        return False
    return cargar_paso(
        estado, svc, lote, destino[0], destino[1], schema=schema,
        simular=simular, rehacer=rehacer, reporte=reporte,
    ) and verificado


# ---------------------------------------------------------------------------
# Corrida completa
# ---------------------------------------------------------------------------
def _cerrar_sin_fallar(estado: EstadoCorrida, resultado: str) -> None:
    try:
        estado.cerrar_corrida(resultado)
    except Exception as e:  # el estado no debe tapar el error real que se está reportando
        logging.debug("No se pudo cerrar la corrida en el estado: %r", e)


def ejecutar_corrida(
    args: argparse.Namespace, excel: Path, estado: EstadoCorrida, ruta_log: Path,
    *, lotes: list[Lote] | None = None, reporte=None, cargar_credenciales=None,
    cancelado=None,
) -> int:
    """Prevalidación → lotes → cierre. Devuelve el código de salida."""
    rehacer = pasos_efectivos_a_rehacer(args.rehacer)
    # Antes de la primera llamada a Google: reconocer los certificados del
    # equipo (antivirus o red que inspeccionan el tráfico seguro).
    certificados.asegurar(log=logging.info)
    logging.info("Run único de la fábrica de contenidos")
    logging.info("  Excel   : %s", excel)
    logging.info(
        "  Esquema : %s%s",
        args.schema,
        " (simulación: no se escribe en la base ni se envía correo)" if args.simular else "",
    )
    logging.info("  Corrida : %s", estado.corrida_id)
    if rehacer:
        logging.info("  Rehacer : %s", ", ".join(p for p in PASOS if p in rehacer))

    try:
        # drive.* se toma aquí (no como default) para poder sustituirlo en pruebas.
        res = prevalidar(
            excel,
            lotes=lotes,
            schema=args.schema,
            simular=args.simular,
            interactivo=not args.no_interactivo,
            cargar_credenciales=cargar_credenciales or drive.cargar_credenciales,
            construir_servicio=drive.construir_servicio,
            log=logging.info,
        )
        imprimir_hallazgos(res)
        # Se guardan en el estado para que la web pueda mostrarlos y para dejar
        # constancia de con qué condiciones arrancó la corrida.
        estado.datos["prevalidacion"] = [
            {"nivel": h.nivel, "area": h.area, "mensaje": h.mensaje, "accion": h.accion}
            for h in res.hallazgos
        ]
        estado.guardar()
        if not res.ok:
            estado.cerrar_corrida("fallido_prevalidacion")
            ruta_xlsx = exportar_estado(estado)
            logging.error("El flujo no arrancó. Corrige lo anterior y vuelve a ejecutar.")
            if not args.simular and ruta_xlsx is not None and res.creds is not None:
                try:
                    primero = res.errores()[0]
                    mensaje = construir_mensaje(
                        destinatarios=correos_aviso(), resultado="fallido",
                        schema=args.schema, filas=[], enlace_sheet="",
                        estado_xlsx=ruta_xlsx,
                        contenido=estado.bytes_excel(),
                        error_general={"motivo": primero.mensaje, "accion": primero.accion},
                    )
                    enviar_correo(res.creds, mensaje)
                    logging.info("Correo final de fallo enviado.")
                except Exception as e:
                    logging.warning("No se pudo enviar el correo final de fallo. Revisa la sesión de Google y vuelve a ejecutar.")
                    logging.debug("    detalle: %r", e)
            imprimir_rutas(estado, ruta_log, ruta_xlsx)
            return 1
        if args.solo_prevalidar:
            estado.cerrar_corrida("prevalidacion_ok")
            logging.info("Prevalidación correcta. No se clonó nada (--solo-prevalidar).")
            imprimir_rutas(estado, ruta_log, None)
            return 0

        logging.info("Prevalidación correcta. Procesando %d lote(s)...", len(res.lotes))
        exitos = sum(1 for lote in res.lotes if procesar_lote(
            estado, res.svc, lote, res.carpetas, rehacer, args.forzar_carga,
            args.schema, args.simular, reporte, cancelado,
        ))
        trabajos = []
        for lote in res.lotes:
            destino_id, destino_nombre = estado.destino_de(lote.clave)
            if destino_id:
                trabajos.append({
                    "etiqueta": lote.etiqueta, "origen_id": lote.origen_id,
                    "destino_id": destino_id, "origen_nombre": destino_nombre,
                    "destino_nombre": destino_nombre,
                })
        if trabajos:
            ruta_inventario = Path(args.dir_corridas) / f"{excel.stem}.inventario.xlsx"
            generar_inventario(res.svc, trabajos, ruta_inventario)
            enlace = publicar_inventario(res.creds, ruta_inventario, correos_aviso())
            estado.datos["inventario"] = {"xlsx": str(ruta_inventario), "sheet": enlace}
            estado.guardar()
        filas_actuales = {l.clave: estado.datos["lotes"][l.clave] for l in res.lotes}
        hay_fallos = any(any(p.get("estado") == "fallido" for p in d["pasos"].values()) for d in filas_actuales.values())
        hay_diferencias = any(d["pasos"]["verificacion"].get("estado") == "con_diferencias" for d in filas_actuales.values())
        resultado_final = "fallido" if hay_fallos else "con_pendientes" if hay_diferencias else "ok"
        estado.cerrar_corrida(resultado_final)
    except KeyboardInterrupt:
        logging.error("Corrida interrumpida por el usuario. Vuelve a ejecutar para retomar donde quedó.")
        _cerrar_sin_fallar(estado, "interrumpido")
        exportar_estado(estado)
        return 1
    except Exception as e:
        err = traducir_excepcion(e, paso="flujo")
        logging.error("%s", err)
        logging.debug("    detalle técnico: %s", err.detalle)
        _cerrar_sin_fallar(estado, "fallido")
        exportar_estado(estado)
        imprimir_rutas(estado, ruta_log, None)
        return 1

    ruta_xlsx = exportar_estado(estado)
    correo_ok = True
    if not args.simular and ruta_xlsx is not None:
        try:
            filas_correo = []
            for fila in estado.resumen():
                if fila["clave"] not in {l.clave for l in res.lotes}:
                    continue
                paso_carga = estado.paso(fila["clave"], "carga")
                filas_correo.append({**fila, "programas": paso_carga.get("programas") or []})
            mensaje = construir_mensaje(
                destinatarios=correos_aviso(), resultado=resultado_final,
                schema=args.schema, filas=filas_correo,
                enlace_sheet=(estado.datos.get("inventario") or {}).get("sheet", ""),
                estado_xlsx=ruta_xlsx,
                contenido=estado.bytes_excel(),
            )
            enviar_correo(res.creds, mensaje)
            logging.info("Correo final enviado a: %s", ", ".join(correos_aviso()))
        except Exception as e:
            correo_ok = False
            err = traducir_excepcion(e, paso="correo", contexto="el correo final")
            logging.error("No se pudo enviar el correo final. %s", err)
            logging.debug("    detalle técnico: %s", err.detalle)
            estado.cerrar_corrida("fallido_notificacion")
    logging.info("=" * 60)
    imprimir_resumen(estado, res.lotes)
    if hay_fallos:
        logging.error(
            "Corrida terminada con fallos: %d de %d lote(s) verificados. "
            "Revisa la columna «Qué hacer» del Excel de estado y vuelve a ejecutar.",
            exitos,
            len(res.lotes),
        )
    elif hay_diferencias:
        logging.warning("Corrida terminada con pendientes: %d lote(s) tienen diferencias y quedaron retenidos.", len(res.lotes) - exitos)
    else:
        logging.info("Corrida terminada: %d lote(s) convertidos y verificados.", exitos)
    imprimir_rutas(estado, ruta_log, ruta_xlsx)
    logging.info(NOTA_FASES)
    return 1 if hay_fallos or not correo_ok else 2 if hay_diferencias else 0


def main(argv: list[str] | None = None) -> int:
    _utf8_en_consola()
    args = construir_parser().parse_args(argv)
    try:
        excel = resolver_excel(args.excel)
    except ErrorFlujo as e:
        print(str(e), file=sys.stderr, flush=True)
        return 1

    dir_corridas = Path(args.dir_corridas)
    try:
        estado = EstadoCorrida.abrir(excel, dir_corridas)
        corrida_id = estado.iniciar_corrida(argumentos_corrida(args, excel))
    except Exception as e:
        err = traducir_excepcion(e, paso="estado", contexto=f"{excel.stem}.estado.json")
        print(str(err), file=sys.stderr, flush=True)
        return 1

    ruta_log = dir_corridas / "logs" / f"{corrida_id}_{excel.stem}.log"
    handlers = configurar_logging(ruta_log)
    try:
        return ejecutar_corrida(args, excel, estado, ruta_log)
    finally:
        cerrar_logging(handlers)


if __name__ == "__main__":
    raise SystemExit(main())
