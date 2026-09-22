"""
Escaneo del clon y carga transaccional de un lote en Cloud SQL.

Regla de carga: **reemplazo por programa**. El material se corrige y se vuelve
a clonar, así que la base debe quedar igual a Drive y nunca con dos versiones
del mismo programa. Antes de insertar se borran todas las filas de `archivo`
de los programas del lote y se cargan las actuales, todo en la misma
transacción: si algo falla no se pierde nada.

Ejemplo: si hay 500 archivos y 120 son de un programa que ahora trae 150, se
borran esos 120 y se insertan los 150 → quedan 530.

Solo se borran filas de `archivo`. Las dimensiones (escuela, programa, materia,
gránulo) se conservan y se reutilizan, así los identificadores se mantienen
estables entre cargas.
"""

from __future__ import annotations

import os, re, sys
from datetime import datetime, timezone

from . import ROOT

_SCHEMA_RE = re.compile(r"[a-z_][a-z0-9_]*")


def _heredados():
    carpeta = str(ROOT / "LMS_Fabrica")
    if carpeta not in sys.path:
        sys.path.insert(0, carpeta)
    import cargar_base_gcp, generar_base_rutas
    return cargar_base_gcp, generar_base_rutas


def escanear_lote(svc, destino_id: str, lote, destino_nombre: str) -> list[dict]:
    """Devuelve filas desnormalizadas indexables del clon resuelto."""
    _, rutas = _heredados()
    meta = {"cliente": lote.cliente_gcp, "raiz": lote.raiz_gcp}
    escuela = getattr(lote, "escuela_gcp", "")
    if escuela:  # detectada en Drive; evita que quede vacía o adivinada en Cloud SQL
        meta["escuela"] = escuela
    registros = rutas.escanear_ruta_drive(svc, destino_id, meta)
    ahora = datetime.now(timezone.utc).isoformat()
    filas = []
    for reg in registros:
        filas.append({
            "raiz_nombre": reg.get("raiz") or lote.raiz_gcp,
            "destinatario_codigo": reg.get("destinatario") or "MEN",
            "periodo_codigo": reg.get("periodo") or "Q2",
            "cliente_nombre": reg.get("cliente") or lote.cliente_gcp,
            "escuela_nombre": reg.get("escuela") or "",
            "programa_nombre": reg.get("programa") or destino_nombre,
            "materia_semestre": reg.get("semestre") or "1",
            "paquete_nombre": reg.get("paquete") or "NOTEBOOK",
            "materia_nombre": reg.get("materia") or "",
            "granulo_codigo": reg.get("codigo") or "",
            "granulo_nombre": reg.get("granulo_nombre") or reg.get("codigo") or "",
            "archivo_nombre": reg.get("archivo_nombre") or "",
            "archivo_nombre_original": reg.get("archivo_nombre_original") or reg.get("archivo_nombre") or "",
            "archivo_enlace": reg.get("archivo_enlace") or "",
            "archivo_hash": reg.get("archivo_hash") or "",
            "archivo_fecha_registro": reg.get("archivo_fecha_registro") or ahora,
            "archivo_activo": reg.get("archivo_activo", "true"),
            "extension_tipo": reg.get("extension") or "sin_extension",
        })
    return filas


def programas_de(filas: list[dict]) -> list[str]:
    """Nombres de programa presentes en las filas a cargar, sin repetir."""
    vistos: dict[str, None] = {}
    for fila in filas:
        nombre = str(fila.get("programa_nombre") or "").strip()
        if nombre:
            vistos.setdefault(nombre, None)
    return list(vistos)


def _detalle_previo(cur, schema: str, programas: list[str]) -> list[tuple[str, str, int]]:
    """Qué hay hoy en la base de esos programas: (programa, cliente, cuántos)."""
    cur.execute(
        f"""
        SELECT p.nombre, c.nombre, COUNT(*)
        FROM {schema}.archivo a
        JOIN {schema}.granulo g ON g.id = a.granulo_id
        JOIN {schema}.materia m ON m.id = g.materia_id
        JOIN {schema}.programa p ON p.id = m.programa_id
        JOIN {schema}.cliente c ON c.id = a.cliente_id
        WHERE p.nombre = ANY(%s)
        GROUP BY p.nombre, c.nombre
        ORDER BY p.nombre, c.nombre
        """,
        (programas,),
    )
    return [(str(a), str(b), int(n)) for a, b, n in cur.fetchall() or []]


def _borrar_programas(cur, schema: str, programas: list[str]) -> int:
    """
    Borra las filas de `archivo` de esos programas (todas, sin mirar cliente).

    Se borra por programa a secas y no por programa+cliente a propósito: si un
    programa cambió de cliente, filtrar por cliente dejaría vivas las filas
    viejas y volveríamos a tener duplicados, que es justo lo que se evita.
    """
    cur.execute(
        f"""
        DELETE FROM {schema}.archivo a
        USING {schema}.granulo g, {schema}.materia m, {schema}.programa p
        WHERE a.granulo_id = g.id
          AND g.materia_id = m.id
          AND m.programa_id = p.id
          AND p.nombre = ANY(%s)
        """,
        (programas,),
    )
    return max(cur.rowcount or 0, 0)


def conectar_desde_env(env=os.environ):
    import psycopg2
    return psycopg2.connect(
        host=env.get("DB_HOST"), port=int(env.get("DB_PORT", "5432")),
        dbname=env.get("DB_NAME"), user=env.get("DB_USER"),
        password=env.get("DB_PASSWORD"), connect_timeout=30,
    )


def cargar_lote(
    filas: list[dict],
    *,
    schema: str = "fabrica_pruebas",
    simular: bool = False,
    reemplazar: bool = True,
    conectar=conectar_desde_env,
    log=lambda _m: None,
) -> dict:
    """
    Carga un lote en una sola transacción, reemplazando lo que ya hubiera.

    Con `reemplazar` (lo normal) se borran primero todas las filas de `archivo`
    de los programas del lote y luego se insertan las actuales. Si el escaneo no
    encontró nada, no se borra nada: nunca se vacía un programa por error.
    """
    if not _SCHEMA_RE.fullmatch(schema or ""):
        raise ValueError(f"Esquema no válido: {schema}")
    programas = programas_de(filas)
    stats: dict = {
        "total": len(filas),
        "insertados": 0,
        "existentes": 0,
        "simulados": 0,
        "eliminados": 0,
        "programas": programas,
        "reemplazo": [],
    }
    if simular:
        stats["simulados"] = len([f for f in filas if f.get("archivo_enlace")])
        return stats
    carga, _ = _heredados()
    carga.SCHEMA = schema
    conexion = conectar()
    cache = {}
    try:
        with conexion:
            with conexion.cursor() as cur:
                # Reemplazo: fuera lo viejo del programa antes de meter lo nuevo.
                # Si no hay filas que cargar no se borra nada (un escaneo vacío no
                # puede dejar el programa sin datos).
                if reemplazar and programas:
                    previo = _detalle_previo(cur, schema, programas)
                    stats["reemplazo"] = previo
                    for programa, cliente, cuantos in previo:
                        log(f"  [reemplazo] {programa} ({cliente}): {cuantos} archivo(s) se reemplazan")
                    stats["eliminados"] = _borrar_programas(cur, schema, programas)
                for fila in filas:
                    enlace = str(fila.get("archivo_enlace") or "").strip()
                    if not enlace:
                        continue
                    if carga.buscar_archivo_id(cur, enlace) is not None:
                        stats["existentes"] += 1
                        continue
                    escuela_id = carga.get_or_create(cur, "escuela", "nombre", fila["escuela_nombre"], cache)
                    programa_id = carga.get_or_create(cur, "programa", "nombre", fila["programa_nombre"], cache, extra={"escuela_id": escuela_id})
                    paquete_id = carga.get_or_create(cur, "paquete", "nombre", fila["paquete_nombre"], cache)
                    materia_id = carga.get_or_create_materia(cur, programa_id, paquete_id, int(fila["materia_semestre"] or 1), fila["materia_nombre"], cache)
                    granulo_id = carga.get_or_create_granulo(cur, materia_id, fila["granulo_codigo"], fila["granulo_nombre"], cache)
                    raiz_id = carga.get_or_create(cur, "raiz", "nombre", fila["raiz_nombre"], cache)
                    destinatario_id = carga.get_or_create(cur, "destinatario", "codigo", fila["destinatario_codigo"], cache)
                    periodo_id = carga.get_or_create(cur, "periodo", "codigo", fila["periodo_codigo"], cache)
                    cliente_id = carga.get_or_create(cur, "cliente", "nombre", fila["cliente_nombre"], cache)
                    extension_id = carga.resolver_extension_id(cur, fila["extension_tipo"], cache)
                    archivo_id = carga.next_id(cur, "archivo")
                    cur.execute(
                        f"""INSERT INTO {schema}.archivo (
                        id, granulo_id, raiz_id, destinatario_id, periodo_id,
                        cliente_id, extension_id, nombre, nombre_original,
                        enlace, hash_sha256, fecha_registro, activo
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (archivo_id, granulo_id, raiz_id, destinatario_id, periodo_id,
                         cliente_id, extension_id, carga.trunc(fila["archivo_nombre"]),
                         carga.trunc(fila["archivo_nombre_original"]), enlace,
                         fila.get("archivo_hash") or None, fila["archivo_fecha_registro"],
                         str(fila.get("archivo_activo", "true")).lower() in {"true", "1", "t"}),
                    )
                    stats["insertados"] += 1
        return stats
    finally:
        conexion.close()
