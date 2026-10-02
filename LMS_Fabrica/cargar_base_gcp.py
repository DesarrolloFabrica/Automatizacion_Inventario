"""
LMS_Fabrica — cargar_base_gcp.py
--------------------------------
Paso 2 de la carga a GCP.

Lee el CSV generado por generar_base_rutas.py e inserta (o actualiza clasificación)
en Cloud SQL. Al terminar envía un correo Gmail con resumen + query SQL del lote
(salvo --sin-correo).

Regla anti-duplicados: si el enlace del archivo ya existe, no vuelve a insertarlo.
--actualizar (solo en fabrica_pruebas): cambia cliente_id y raiz_id de existentes.
"""

from __future__ import annotations

import argparse, csv, os, re, sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

from generar_base_lms import (
    ENV_PATH,
    EXTENSION_MAP,
    SCHEMA,
    extraer_id_archivo,
    normalizar_extension,
)

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CSV = BASE_DIR / "lms_base_final.csv"
MAX_VARCHAR = 200


def trunc(value: str, limit: int = MAX_VARCHAR) -> str:
    """Corta textos largos al límite de columnas VARCHAR de la base."""
    text = (value or "").strip()
    return text[:limit] if len(text) > limit else text


def next_id(cur, table: str) -> int:
    """Obtiene el siguiente id numérico (MAX+1) para tablas sin secuencia usada aquí."""
    cur.execute(f"SELECT COALESCE(MAX(id), 0) + 1 FROM {SCHEMA}.{table}")
    return int(cur.fetchone()[0])


def resolver_extension_id(cur, tipo: str, cache: dict) -> int:
    """Misma regla que IdResolver: mapa fijo → lookup → create (sin duplicar formato)."""
    # Sin extensión se guarda vacía (extension.tipo = ''), como los ya cargados;
    # "sin_extension" no cabe en la columna (varchar(10)).
    tipo_norm = normalizar_extension(tipo)
    cache_key = ("extension", "tipo", tipo_norm)
    if cache_key in cache:
        return cache[cache_key]
    if tipo_norm in EXTENSION_MAP:
        cache[cache_key] = EXTENSION_MAP[tipo_norm]
        return cache[cache_key]
    cur.execute(
        f"SELECT id FROM {SCHEMA}.extension WHERE tipo = %s OR UPPER(tipo) = %s",
        (tipo_norm, tipo_norm.upper()),
    )
    row = cur.fetchone()
    if row:
        cache[cache_key] = int(row[0])
        return cache[cache_key]
    return get_or_create(cur, "extension", "tipo", tipo_norm, cache)


def get_or_create(
    cur,
    table: str,
    key_col: str,
    key_val: str,
    cache: dict,
    extra: dict[str, object] | None = None,
) -> int:
    """
    Busca una fila por clave (ej. cliente.nombre). Si no existe, la crea.
    Usa cache en memoria para no repetir SELECTs en la misma corrida.
    """
    cache_key = (table, key_col, key_val, tuple(sorted((extra or {}).items())))
    if cache_key in cache:
        return cache[cache_key]

    cur.execute(
        f"SELECT id FROM {SCHEMA}.{table} WHERE {key_col} = %s",
        (key_val,),
    )
    row = cur.fetchone()
    if row:
        cache[cache_key] = int(row[0])
        return cache[cache_key]

    new_id = next_id(cur, table)
    cols = ["id", key_col]
    vals: list[object] = [new_id, key_val]
    if extra:
        for col, val in extra.items():
            cols.append(col)
            vals.append(val)
    placeholders = ", ".join(["%s"] * len(cols))
    cur.execute(
        f"INSERT INTO {SCHEMA}.{table} ({', '.join(cols)}) VALUES ({placeholders})",
        vals,
    )
    cache[cache_key] = new_id
    return new_id


def get_or_create_materia(
    cur,
    programa_id: int,
    paquete_id: int,
    semestre: int,
    nombre: str,
    cache: dict,
) -> int:
    """Igual que get_or_create, pero la clave de materia es compuesta (programa+paquete+semestre+nombre)."""
    cache_key = ("materia", programa_id, paquete_id, semestre, nombre)
    if cache_key in cache:
        return cache[cache_key]

    cur.execute(
        f"""
        SELECT id FROM {SCHEMA}.materia
        WHERE programa_id = %s AND paquete_id = %s AND semestre = %s AND nombre = %s
        """,
        (programa_id, paquete_id, semestre, nombre),
    )
    row = cur.fetchone()
    if row:
        cache[cache_key] = int(row[0])
        return cache[cache_key]

    new_id = next_id(cur, "materia")
    cur.execute(
        f"""
        INSERT INTO {SCHEMA}.materia (id, programa_id, paquete_id, semestre, nombre)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (new_id, programa_id, paquete_id, semestre, nombre),
    )
    cache[cache_key] = new_id
    return new_id


def get_or_create_granulo(
    cur,
    materia_id: int,
    codigo: str,
    nombre: str,
    cache: dict,
) -> int:
    """Busca/crea el gránulo por materia_id + codigo (ej. G1001)."""
    cache_key = ("granulo", materia_id, codigo)
    if cache_key in cache:
        return cache[cache_key]

    cur.execute(
        f"SELECT id FROM {SCHEMA}.granulo WHERE materia_id = %s AND codigo = %s",
        (materia_id, codigo),
    )
    row = cur.fetchone()
    if row:
        cache[cache_key] = int(row[0])
        return cache[cache_key]

    new_id = next_id(cur, "granulo")
    cur.execute(
        f"""
        INSERT INTO {SCHEMA}.granulo (id, materia_id, codigo, nombre)
        VALUES (%s, %s, %s, %s)
        """,
        (new_id, materia_id, codigo, nombre or codigo),
    )
    cache[cache_key] = new_id
    return new_id


def buscar_archivo_id(cur, enlace: str) -> int | None:
    cur.execute(f"SELECT id FROM {SCHEMA}.archivo WHERE enlace = %s", (enlace,))
    row = cur.fetchone()
    if row:
        return int(row[0])
    file_id = extraer_id_archivo(enlace)
    if not file_id:
        return None
    cur.execute(
        f"SELECT id FROM {SCHEMA}.archivo WHERE enlace LIKE %s",
        (f"%/file/d/{file_id}/%",),
    )
    row = cur.fetchone()
    return int(row[0]) if row else None


def enlace_existe(cur, enlace: str) -> bool:
    return buscar_archivo_id(cur, enlace) is not None


def actualizar_cliente_raiz(cur, archivo_id: int, cliente_id: int, raiz_id: int) -> bool:
    cur.execute(
        f"SELECT cliente_id, raiz_id FROM {SCHEMA}.archivo WHERE id = %s",
        (archivo_id,),
    )
    actual = cur.fetchone()
    if actual and int(actual[0]) == cliente_id and int(actual[1]) == raiz_id:
        return False
    cur.execute(
        f"""
        UPDATE {SCHEMA}.archivo
        SET cliente_id = %s, raiz_id = %s
        WHERE id = %s
        """,
        (cliente_id, raiz_id, archivo_id),
    )
    return True


def cargar_csv(
    csv_path: Path,
    schema: str = SCHEMA,
    actualizar_existentes: bool = False,
) -> dict[str, int]:
    """
    Recorre cada fila del CSV:
      - sin enlace → salta
      - enlace ya en DB → cuenta existente; con --actualizar solo toca cliente/raíz
      - enlace nuevo → get_or_create de dimensiones + INSERT del archivo
    Devuelve estadísticas de la corrida (para el correo).
    """
    global SCHEMA
    SCHEMA = schema

    env_path = ENV_PATH if ENV_PATH.exists() else BASE_DIR / ".env"
    if not env_path.exists():
        raise FileNotFoundError(f"No se encontró {ENV_PATH} ni {BASE_DIR / '.env'}")

    load_dotenv(env_path)
    password = os.getenv("DB_PASSWORD", "")
    if not password:
        raise ValueError("DB_PASSWORD no configurada en .env")

    with csv_path.open(encoding="utf-8", newline="") as handle:
        filas = list(csv.DictReader(handle))

    stats = {
        "total_csv": len(filas),
        "archivo_insertados": 0,
        "archivo_existentes": 0,
        "archivo_actualizados": 0,
        "archivo_sin_cambio": 0,
        "dimensiones_nuevas": 0,
    }
    cache: dict = {}
    now = datetime.now(timezone.utc)
    dims_before = len(cache)

    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=password,
        connect_timeout=30,
    )

    etiqueta = "PRODUCCIÓN" if SCHEMA == "fabrica" else "PRUEBA TEMPORAL"
    print(f"Esquema destino: {SCHEMA} ({etiqueta})")
    if actualizar_existentes:
        print("Modo: actualizar cliente y raiz de archivos que ya existen.")

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {SCHEMA}.archivo")
                before = int(cur.fetchone()[0])
                print(f"Archivos en GCP antes: {before}")

                for idx, row in enumerate(filas, start=1):
                    if idx % 500 == 0 or idx == len(filas):
                        print(f"  Procesando {idx}/{len(filas)}...")

                    enlace = row.get("archivo_enlace", "").strip()
                    if not enlace:
                        continue
                    existente_id = buscar_archivo_id(cur, enlace)
                    if existente_id is not None:
                        stats["archivo_existentes"] += 1
                        if actualizar_existentes:
                            raiz_id = get_or_create(
                                cur, "raiz", "nombre", row["raiz_nombre"], cache
                            )
                            cliente_id = get_or_create(
                                cur, "cliente", "nombre", row["cliente_nombre"], cache
                            )
                            if actualizar_cliente_raiz(
                                cur, existente_id, cliente_id, raiz_id
                            ):
                                stats["archivo_actualizados"] += 1
                            else:
                                stats["archivo_sin_cambio"] += 1
                        continue

                    escuela_id = get_or_create(
                        cur, "escuela", "nombre", row["escuela_nombre"], cache
                    )
                    programa_id = get_or_create(
                        cur,
                        "programa",
                        "nombre",
                        row["programa_nombre"],
                        cache,
                        extra={"escuela_id": escuela_id},
                    )
                    paquete_id = get_or_create(
                        cur, "paquete", "nombre", row["paquete_nombre"], cache
                    )
                    materia_id = get_or_create_materia(
                        cur,
                        programa_id,
                        paquete_id,
                        int(row["materia_semestre"] or 1),
                        row["materia_nombre"],
                        cache,
                    )
                    granulo_id = get_or_create_granulo(
                        cur,
                        materia_id,
                        row["granulo_codigo"],
                        row.get("granulo_nombre", "") or row["granulo_codigo"],
                        cache,
                    )
                    raiz_id = get_or_create(
                        cur, "raiz", "nombre", row["raiz_nombre"], cache
                    )
                    destinatario_id = get_or_create(
                        cur, "destinatario", "codigo", row["destinatario_codigo"], cache
                    )
                    periodo_id = get_or_create(
                        cur, "periodo", "codigo", row["periodo_codigo"], cache
                    )
                    cliente_id = get_or_create(
                        cur, "cliente", "nombre", row["cliente_nombre"], cache
                    )
                    extension_id = resolver_extension_id(
                        cur, row["extension_tipo"], cache
                    )

                    archivo_id = next_id(cur, "archivo")
                    fecha = row.get("archivo_fecha_registro") or now.isoformat()
                    cur.execute(
                        f"""
                        INSERT INTO {SCHEMA}.archivo (
                            id, granulo_id, raiz_id, destinatario_id, periodo_id,
                            cliente_id, extension_id, nombre, nombre_original,
                            enlace, hash_sha256, fecha_registro, activo
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        (
                            archivo_id,
                            granulo_id,
                            raiz_id,
                            destinatario_id,
                            periodo_id,
                            cliente_id,
                            extension_id,
                            trunc(row["archivo_nombre"]),
                            trunc(row["archivo_nombre_original"]),
                            enlace,
                            row.get("archivo_hash") or None,
                            fecha,
                            str(row.get("archivo_activo", "true")).lower() in {"true", "1", "t"},
                        ),
                    )
                    stats["archivo_insertados"] += 1

                cur.execute(f"SELECT COUNT(*) FROM {SCHEMA}.archivo")
                after = int(cur.fetchone()[0])
                stats["archivo_gcp_despues"] = after
                stats["archivo_delta"] = after - before
                stats["dimensiones_nuevas"] = len(cache) - dims_before

        return stats
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Carga lms_base_final.csv en Cloud SQL.")
    parser.add_argument(
        "-i",
        "--input",
        default=str(DEFAULT_CSV),
        help="CSV desnormalizado (studio_results).",
    )
    parser.add_argument(
        "--schema",
        default=SCHEMA,
        help="Esquema destino. Default: fabrica_pruebas (pruebas temporales).",
    )
    parser.add_argument(
        "--actualizar",
        action="store_true",
        help="Actualiza cliente y raiz de archivos ya existentes. No permitido en fabrica.",
    )
    parser.add_argument(
        "--sin-correo",
        action="store_true",
        help="No enviar el aviso por Gmail (por defecto sí se envía, como en clonación Drive).",
    )
    args = parser.parse_args()

    csv_path = Path(args.input)
    if not csv_path.exists():
        print(f"Error: no existe {csv_path}", file=sys.stderr)
        return 1
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", args.schema):
        print(f"Error: esquema no válido: {args.schema}", file=sys.stderr)
        return 1
    if args.actualizar and args.schema == "fabrica":
        print(
            "Error: --actualizar no se permite en el esquema de producción fabrica.",
            file=sys.stderr,
        )
        return 1

    try:
        print(f"Cargando {csv_path} ...")
        stats = cargar_csv(
            csv_path,
            schema=args.schema,
            actualizar_existentes=args.actualizar,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print("\nCarga completada:")
    for key, value in stats.items():
        print(f"  {key}: {value}")

    if not args.sin_correo:
        try:
            from notificar_carga_lms import enviar_aviso_carga

            enviar_aviso_carga(args.schema, stats, csv_path)
        except Exception as exc:
            print(
                "No se pudo enviar el correo. Active Gmail API, borre token.json y vuelva a autorizar. "
                f"Detalle: {exc}",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
