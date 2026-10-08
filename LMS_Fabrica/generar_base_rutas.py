"""
LMS_Fabrica — generar_base_rutas.py
-----------------------------------
Paso 1 de la carga a GCP (no escribe en la base; no envía correo).

Entrada:  RUTAS.xlsx (filas con destino a escanear; cliente/etiqueta/origen).
Salida:   CSV tipo studio_results (ej. lms_base_rutas.csv).

Indexa archivos con código G+dígitos si lo tienen; si no, usa el stem
(p. ej. Moodle 01_Quiz.txt). Lo que se escanea sale solo del Excel
(origen, destino y cliente).

Siguiente script: cargar_base_gcp.py
"""

from __future__ import annotations

import argparse, csv, re, sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from generar_base_lms import (
    BASE_DIR,
    COLUMNAS_SALIDA,
    IdResolver,
    MIME_FOLDER,
    PAQUETES_VALIDOS_NORM,
    ReferenciaGcp,
    autenticar_drive,
    es_carpeta_granulo,
    extraer_codigo,
    extraer_granulo_nombre,
    extraer_id_carpeta,
    listar_hijos,
    norm_text,
    obtener_extension,
    parse_semestre,
    parsear_ruta,
)

EXCEL_DEFAULT = BASE_DIR / "RUTAS.xlsx"
if not EXCEL_DEFAULT.exists():
    EXCEL_DEFAULT = BASE_DIR / "RUTAS.csv"
SALIDA_DEFAULT = BASE_DIR / "lms_base_rutas.csv"
# CSV de referencia opcional junto al script (si existe). Origen/destino salen del Excel.
REF_DEFAULT = BASE_DIR / "studio_results_referencia.csv"

# Cliente y escuela salen del Excel y de la estructura de carpetas en Drive.


_ROMAN_SEMESTRE = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
    "XI": 11,
    "XII": 12,
}


def cargar_metadata_programas(*fuentes: Path) -> dict[str, dict[str, str]]:
    meta: dict[str, dict[str, str]] = {}
    for ruta in fuentes:
        if not ruta.exists():
            continue
        with ruta.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                programa = row.get("programa_nombre", "")
                if not programa or programa in meta:
                    continue
                meta[programa] = {
                    "escuela": row["escuela_nombre"],
                    "cliente": row["cliente_nombre"],
                }
    return meta


# Clasificaciones del Excel. La raíz real siempre es LMS_Carga.
# LMS_correcciones NO es carpeta raíz: se guarda como cliente PRODUCTO + raíz
# LMS_Carga (clasificación operativa). El resto son clientes de GCP tal cual.
# Debe quedar alineado con CLASIFICACIONES de flujo_lib/excel.py.
_CLIENTES_DIRECTOS = ("PRODUCTO", "TANIA", "JARVEY")
_ALIASES_CORRECCIONES = {
    "LMS_CORRECCIONES",
    "LMS_CORRECCION",
    "LMSCORRECIONES",
    "LMSCORRECION",
    "LMSCORRECIO",
    "LMS_CORRECC",
    "CORRECCIONES",
    "CORRECCION",
}


def es_clasificacion_correcciones(texto: str) -> bool:
    t = norm_text(texto)
    if t in _ALIASES_CORRECCIONES:
        return True
    return t.startswith("LMS_CORRECC") or t.startswith("LMSCORREC")


def normalizar_clasificacion(valor: object) -> dict[str, str] | None:
    """
    Traduce el valor de la columna cliente del Excel a cliente+raíz de GCP.
    PRODUCTO/TANIA/JARVEY/LMS_correcciones → raíz LMS_Carga (carpeta raíz real).
    LMS_correcciones (y alias) → cliente PRODUCTO; no inventa raíz LMS_CORRECCIONES.
    """
    texto = norm_text(valor)
    if not texto:
        return None
    if es_clasificacion_correcciones(texto):
        return {
            "clasificacion": "LMS_CORRECCIONES",
            "cliente": "PRODUCTO",
            "raiz": "LMS_Carga",
        }
    if texto in _CLIENTES_DIRECTOS:
        return {"clasificacion": texto, "cliente": texto, "raiz": "LMS_Carga"}
    return None


def _dataframe_rutas(ruta: Path) -> pd.DataFrame:
    if ruta.suffix.lower() == ".csv":
        return pd.read_csv(ruta)
    df = pd.read_excel(ruta, header=0)
    cols = {str(c).strip().lower() for c in df.columns}
    if "destino" in cols or "destino " in {c.strip() for c in cols}:
        return df
    crudo = pd.read_excel(ruta, header=None)
    header_idx = None
    for i, row in crudo.iterrows():
        valores = {str(v).strip().lower() for v in row.tolist() if pd.notna(v)}
        if "destino" in valores or "cliente" in valores:
            header_idx = int(i)
            break
    if header_idx is None:
        return df
    df = pd.read_excel(ruta, header=header_idx)
    return df


def leer_rutas_excel(ruta: Path) -> list[tuple[str, str, dict[str, str] | None]]:
    df = _dataframe_rutas(ruta)
    cols = {str(c).strip().lower(): c for c in df.columns}
    col_url = cols.get("destino") or cols.get("destino ")
    col_clasif = (
        cols.get("cliente")
        or cols.get("tipo")
        or cols.get("clasificacion")
        or cols.get("clasificación")
    )
    reserved = {
        "origen",
        "destino",
        "destino ",
        "cliente",
        "tipo",
        "clasificacion",
        "clasificación",
        "programa_nombre",
        "titulo",
    }
    col_label = cols.get("etiqueta") or cols.get("programa") or cols.get("unnamed: 1")
    if not col_label or str(col_label).strip().lower() in reserved:
        col_label = next(
            (c for c in df.columns if str(c).strip().lower() not in reserved),
            list(df.columns)[0],
        )

    if not col_url:
        raise ValueError(f"No se encontró columna de destino en {ruta}")

    rutas: list[tuple[str, str, dict[str, str] | None]] = []
    for _, row in df.iterrows():
        etiqueta = str(row[col_label]).strip()
        url = str(row[col_url]).strip()
        if not url or url.lower() == "nan":
            continue
        if etiqueta.lower() in {"nan", ""}:
            etiqueta = url
        clasif = None
        if col_clasif:
            crudo = row[col_clasif]
            if pd.notna(crudo) and str(crudo).strip() and str(crudo).strip().lower() != "nan":
                clasif = normalizar_clasificacion(crudo)
                if clasif is None:
                    raise ValueError(
                        f"Clasificación inválida en fila '{etiqueta}': {crudo!r}. "
                        "Usa PRODUCTO, TANIA, JARVEY o LMS_CORRECCIONES."
                    )
        rutas.append((etiqueta, url, clasif))
    return rutas


def limpiar_prefijo_numero(nombre: str) -> str:
    return norm_text(re.sub(r"^\d+\.\s*", "", nombre.strip()))


def _token_semestre_a_numero(token: str) -> str | None:
    token = token.upper()
    if token in _ROMAN_SEMESTRE:
        return str(_ROMAN_SEMESTRE[token])
    if token.isdigit():
        return token
    return None


def extraer_semestre_bloque(bloque: str) -> str | None:
    bloque_norm = norm_text(bloque)
    if bloque_norm in {"PROPEDEUTICO", "PROP", "PRE", "PREPROPEDEUTICO"}:
        return "PROPEDEUTICO"
    m = re.match(r"^SEMESTRE[\s_]*([IVXLC]+|\d+)$", bloque_norm)
    if m:
        numero = _token_semestre_a_numero(m.group(1))
        if numero is not None:
            return numero
    m = re.match(r"^(?:SEMESTRE\s*)?(\d+)$", bloque_norm)
    if m:
        return m.group(1)
    if bloque_norm.isdigit():
        return bloque_norm
    return None


def es_carpeta_escuela(nombre: str) -> bool:
    return norm_text(nombre).startswith("ESCUELA_")


def es_carpeta_correcciones(nombre: str) -> bool:
    return norm_text(nombre) in {"LMS_CORRECCIONES", "LMS_CORRECCION"}


def parsear_ruta_programa(
    partes: list[str],
    programa_hint: str,
    meta_prog: dict[str, str],
) -> dict[str, str] | None:
    """
    Estructuras soportadas bajo carpeta de programa (o escuela):
    - programa / semestre / paquete / materia / [granulo] / archivo
    - escuela / programa / semestre N / materia / [subcarpeta] / archivo
    """
    if len(partes) < 3:
        return None

    programa_norm = norm_text(programa_hint)
    escuela = meta_prog.get("escuela", "")
    cliente = meta_prog.get("cliente", "TANIA")
    raiz_nombre = meta_prog.get("raiz") or "LMS_Carga"

    # Detectar programa y escuela en la ruta
    programa = programa_norm
    idx = 0
    if es_carpeta_correcciones(partes[0]):
        # La carpeta puede llamarse LMS_CORRECCIONES en Drive, pero la raíz
        # canónica en GCP es LMS_Carga (no crear una raíz distinta).
        if not meta_prog.get("raiz"):
            raiz_nombre = "LMS_Carga"
        cliente = meta_prog.get("cliente", "PRODUCTO")
        if len(partes) > 1 and es_carpeta_escuela(partes[1]):
            escuela = norm_text(partes[1])
            if len(partes) > 2:
                programa = norm_text(partes[2])
                idx = 3
        elif len(partes) > 1:
            programa = norm_text(partes[1])
            idx = 2
    elif es_carpeta_escuela(partes[0]):
        escuela = norm_text(partes[0])
        if len(partes) > 1:
            programa = norm_text(partes[1])
            idx = 2
    elif norm_text(partes[0]) == programa_norm:
        idx = 1
    else:
        for i, parte in enumerate(partes[:-1]):
            pn = norm_text(parte)
            if pn == programa_norm or pn.startswith("ESPECIALIZACION_") or pn.startswith("INGENIERIA_"):
                programa = pn
                idx = i + 1
                break

    rel = partes[idx:-1]
    if not rel:
        return None

    semestre = "1"
    paquete = "NOTEBOOK"
    ridx = 0

    if ridx < len(rel):
        sem_bloque = extraer_semestre_bloque(rel[ridx])
        if sem_bloque is not None:
            semestre = sem_bloque
            ridx += 1

    if ridx < len(rel) and norm_text(rel[ridx]) in PAQUETES_VALIDOS_NORM:
        paquete = norm_text(rel[ridx])
        ridx += 1
    elif programa.startswith("INGENIERIA_"):
        paquete = "MODELO_NOTEBOOK"

    if ridx >= len(rel):
        return None

    materia = limpiar_prefijo_numero(rel[ridx])
    ridx += 1

    granulo_carpeta = ""
    for parte in rel[ridx:]:
        if es_carpeta_granulo(parte):
            granulo_carpeta = norm_text(parte)
            break

    return {
        "raiz": raiz_nombre,
        "destinatario": "MEN",
        "periodo": "Q2",
        "cliente": cliente,
        "escuela": escuela,
        "programa": programa,
        "semestre": str(parse_semestre(semestre)),
        "paquete": paquete,
        "materia": materia,
        "granulo_carpeta": granulo_carpeta,
        "granulo_modo_alt": False,
    }


def escanear_carpeta_programa(
    servicio,
    folder_id: str,
    meta_prog: dict[str, str],
    ruta_prefix: list[str] | None = None,
) -> list[dict]:
    """
    Recorre en anchura (BFS) la carpeta del programa.
    Agrega archivos con código extraíble (G+dígitos o stem vía extraer_codigo).
    """
    raiz = (
        servicio.files()
        .get(fileId=folder_id, fields="id,name", supportsAllDrives=True)
        .execute()
    )
    programa = raiz.get("name", "")
    print(f"  Programa: {programa}", flush=True)

    resultados: list[dict] = []
    ruta_inicial = [* (ruta_prefix or []), programa]
    cola: deque[tuple[str, list[str]]] = deque([(folder_id, ruta_inicial)])

    while cola:
        actual_id, actual_ruta = cola.popleft()
        try:
            hijos = listar_hijos(servicio, actual_id)
        except HttpError as err:
            print(f"  Aviso: no se pudo leer carpeta ({err})", file=sys.stderr, flush=True)
            continue

        for hijo in hijos:
            nombre = hijo.get("name", "")
            if hijo.get("mimeType") == MIME_FOLDER:
                cola.append((hijo["id"], [*actual_ruta, nombre]))
                continue

            codigo = extraer_codigo(nombre)
            if not codigo:
                continue

            partes = [*actual_ruta, nombre]
            meta = parsear_ruta_programa(partes, programa, meta_prog)
            if not meta:
                meta = parsear_ruta(partes)
            if not meta:
                continue

            ext = obtener_extension(hijo)
            if meta.get("granulo_carpeta"):
                granulo_nombre = meta["granulo_carpeta"]
            else:
                granulo_nombre = extraer_granulo_nombre(
                    nombre, codigo, meta["materia"], meta.get("granulo_carpeta", "")
                )

            resultados.append(
                {
                    **meta,
                    "codigo": codigo,
                    "granulo_nombre": granulo_nombre,
                    "archivo_nombre": nombre,
                    "archivo_nombre_original": nombre,
                    "archivo_enlace": hijo.get("webViewLink", ""),
                    "extension": ext,
                    # ID del archivo de origen (lo guarda la clonación en la copia).
                    "origen_id": str((hijo.get("properties") or {}).get("origen_id") or ""),
                }
            )

    return resultados


def escanear_ruta_drive(
    servicio,
    folder_id: str,
    meta_prog: dict[str, str],
) -> list[dict]:
    raiz = (
        servicio.files()
        .get(fileId=folder_id, fields="id,name", supportsAllDrives=True)
        .execute()
    )
    nombre_raiz = raiz.get("name", "")

    if not es_carpeta_correcciones(nombre_raiz):
        return escanear_carpeta_programa(servicio, folder_id, meta_prog)

    print(f"  Raíz correcciones: {nombre_raiz}", flush=True)
    resultados: list[dict] = []
    meta_base = {
        **meta_prog,
        "cliente": meta_prog.get("cliente", "PRODUCTO"),
    }

    for escuela in listar_hijos(servicio, folder_id):
        if escuela.get("mimeType") != MIME_FOLDER or not es_carpeta_escuela(escuela.get("name", "")):
            continue
        escuela_nombre = norm_text(escuela["name"])
        print(f"  Escuela: {escuela_nombre}", flush=True)
        for programa in listar_hijos(servicio, escuela["id"]):
            if programa.get("mimeType") != MIME_FOLDER:
                continue
            meta_local = {**meta_base, "escuela": escuela_nombre}
            resultados.extend(
                escanear_carpeta_programa(
                    servicio,
                    programa["id"],
                    meta_local,
                    ruta_prefix=["LMS_CORRECCIONES", escuela_nombre],
                )
            )

    return resultados


def generar(
    excel: Path,
    salida: Path,
    referencia: Path | None = None,
    solo_lectura_db: bool = True,
) -> int:
    """
    Orquestación: lee Excel → por cada destino escanea Drive → arma filas CSV.
    Devuelve 0 si ok, 1 si no hubo archivos indexables.
    """
    rutas = leer_rutas_excel(excel)
    print(f"Rutas en Excel: {len(rutas)}", flush=True)

    meta_programas = cargar_metadata_programas(
        REF_DEFAULT,
        BASE_DIR / "lms_base_final.csv",
    )

    servicio = build("drive", "v3", credentials=autenticar_drive(), cache_discovery=False)
    registros: list[dict] = []

    for etiqueta, url, clasif_excel in rutas:
        print(f"\nEscaneando [{etiqueta}]...", flush=True)
        folder_id = extraer_id_carpeta(url)
        raiz = (
            servicio.files()
            .get(fileId=folder_id, fields="name", supportsAllDrives=True)
            .execute()
        )
        programa = raiz["name"]
        meta_prog = dict(
            meta_programas.get(norm_text(programa), meta_programas.get(programa, {}))
        )
        if not meta_prog and programa.startswith("ESCUELA_"):
            meta_prog = {"escuela": norm_text(programa), "cliente": "PRODUCTO"}
        if not meta_prog and norm_text(programa).startswith("INGENIERIA_"):
            meta_prog = {
                "escuela": "ESCUELA_DE_INGENIERIA",
                "cliente": "PRODUCTO",
            }
        if es_carpeta_correcciones(programa) and not clasif_excel:
            meta_prog = {
                **meta_prog,
                "cliente": "PRODUCTO",
                "raiz": "LMS_Carga",
            }
        if clasif_excel:
            meta_prog["cliente"] = clasif_excel["cliente"]
            meta_prog["raiz"] = clasif_excel["raiz"]
        if not meta_prog.get("cliente"):
            print(
                f"  Aviso: sin clasificación en Excel ni metadata para {programa}; se usará TANIA.",
                flush=True,
            )
            meta_prog["cliente"] = "TANIA"
        if not meta_prog.get("raiz"):
            meta_prog["raiz"] = "LMS_Carga"
        origen = "Excel" if clasif_excel else "metadata"
        print(
            f"  Clasificacion: {clasif_excel['clasificacion'] if clasif_excel else origen} "
            f"-> cliente={meta_prog.get('cliente')} raiz={meta_prog.get('raiz')} ({origen}) "
            f"escuela={meta_prog.get('escuela')}",
            flush=True,
        )
        encontrados = escanear_ruta_drive(servicio, folder_id, meta_prog)
        print(f"  -> {len(encontrados)} archivos", flush=True)
        registros.extend(encontrados)

    print(f"\nTotal archivos: {len(registros)}", flush=True)
    if not registros:
        print("No se encontraron archivos indexables.")
        return 1

    ref = ReferenciaGcp(referencia)
    resolver = IdResolver(readonly=solo_lectura_db)
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    filas: list[dict[str, str]] = []
    desde_ref = 0

    for idx, reg in enumerate(registros, start=1):
        if idx % 500 == 0 or idx == len(registros):
            print(f"  Resolviendo IDs {idx}/{len(registros)}...", flush=True)
        fila_ref = ref.buscar(reg["archivo_enlace"])
        if fila_ref:
            filas.append(fila_ref)
            desde_ref += 1
        else:
            filas.append(resolver.construir_fila(reg, ahora))

    resolver.close()

    salida.parent.mkdir(parents=True, exist_ok=True)
    with salida.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNAS_SALIDA, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(filas)

    print(f"\nBase generada: {salida}")
    print(f"Total filas: {len(filas)}")
    print(f"Desde referencia GCP: {desde_ref}")
    print(f"Nuevas filas: {len(filas) - desde_ref}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Genera base LMS desde rutas en Excel.")
    parser.add_argument(
        "--excel",
        default=str(EXCEL_DEFAULT),
        help="Excel/CSV con destino y clasificación: PRODUCTO, TANIA, JARVEY o LMS_CORRECCIONES.",
    )
    parser.add_argument("-o", "--salida", default=str(SALIDA_DEFAULT))
    parser.add_argument("--referencia", default=str(REF_DEFAULT))
    parser.add_argument("--escribir-db", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        ref = Path(args.referencia) if args.referencia else None
        return generar(
            Path(args.excel),
            Path(args.salida),
            referencia=ref,
            solo_lectura_db=not args.escribir_db,
        )
    except (ValueError, FileNotFoundError, HttpError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
