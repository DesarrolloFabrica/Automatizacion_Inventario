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

ESQUEMA LIMPIO (fabrica1, recarga desde cero, 2026-10-02)
Todo esquema que no sea `fabrica` ni `fabrica_pruebas` se trata como limpio:
  - No hay reemplazo por programa: nada se borra. Un archivo cuyo ID de origen
    ya está cargado se omite (no se duplica).
  - Cada fila lleva `origen_id` (ID del archivo en el Drive ORIGEN) y
    `fecha_origen` (cuándo se subió ese archivo al Drive origen). Las columnas
    se crean con LMS_Fabrica/migraciones/fabrica1_001_origen_archivo.sql.
  - Los IDs salen de las secuencias y el programa se busca por escuela + nombre.
  - Antes de conectar se revisa que todas las filas estén completas; si alguna
    no lo está no se carga nada y se dice cuál.
"""

from __future__ import annotations

import os, re, sys
from datetime import datetime, timezone

from . import ROOT, drive
from .drive import MIME_FOLDER
from .mensajes import ErrorFlujo

_SCHEMA_RE = re.compile(r"[a-z_][a-z0-9_]*")
# Esquemas con datos de antes de la recarga: se cargan como siempre.
ESQUEMAS_LEGADOS = frozenset({"fabrica", "fabrica_pruebas"})
# Fecha de Drive que se guarda como fecha_origen: cuándo se subió (creó) el
# archivo en el Drive origen. La lista de Drive muestra la de modificación
# (modifiedTime); si se prefiere esa, basta con cambiar este valor.
CAMPO_FECHA_ORIGEN = "createdTime"
MIGRACION_ORIGEN = "LMS_Fabrica/migraciones/fabrica1_001_origen_archivo.sql"
LIMITE_CODIGO_GRANULO = 200  # granulo.codigo se amplía para conservar el nombre original
LIMITE_EXTENSION = 10  # extension.tipo sigue siendo varchar(10)
# Tablas de fabrica1 con secuencia propia (recurso_moodle la usa el trigger).
TABLAS_CON_SECUENCIA = ("archivo", "cliente", "destinatario", "escuela", "extension", "granulo",
                        "materia", "paquete", "periodo", "programa", "raiz", "recurso_moodle")
# Las únicas escuelas de la base limpia, todas con el mismo patrón ESCUELA_DE_…
# (definidas por Camilo el 2026-10-05). En Drive la misma escuela aparece con y
# sin "DE_" (ESCUELA_SALUD_Y_BIENESTAR, …_JURIDICAS_Y_DE_GOBIERNO, DISEÑO con Ñ):
# todas esas variantes van a su nombre oficial. Una escuela que no sea ninguna
# de estas no se carga: hay que agregarla aquí a propósito.
ESCUELAS_OFICIALES = (
    "ESCUELA_DE_CIENCIAS_SOCIALES_JURIDICAS_Y_GOBIERNO",
    "ESCUELA_DE_DISENO_Y_COMUNICACION",
    "ESCUELA_DE_INGENIERIA",
    "ESCUELA_DE_SALUD_Y_BIENESTAR",
    "ESCUELA_DE_TRANSFORMACION_EMPRESARIAL",
)


# Carpetas del Drive origen que NO se cargan en la base limpia (el origen no se
# toca: se bloquean aquí). ID de la carpeta -> por qué.
ORIGENES_EXCLUIDOS = {
    "1plXOgDKXBTxIKn5EV11c-av0OT7qKmGt": (
        "PRODUCTO / ESCUELA_TRANSFORMACION_EMPRESARIAL / ESPECIALIZACION_EN_GERENCIA_PUBLICA está "
        "repetida; la válida es PRODUCTO / ESCUELA_CIENCIAS_SOCIALES_JURIDICAS_Y_GOBIERNO / "
        "ESPECIALIZACION_GERENCIA_PUBLICA (1TenYdfOM8WQzYOgPTd-SY0osvJvTYefn), validado por Camilo el 2026-10-05."
    ),
}


def _clave_escuela(nombre: str) -> str:
    """Nombre sin "ESCUELA_" ni los "DE_": ESCUELA_SALUD_Y_BIENESTAR -> SALUD_Y_BIENESTAR."""
    clave = re.sub(r"^ESCUELA_", "", (nombre or "").strip().upper())
    return re.sub(r"(^|_)DE_", r"\1", clave)


_ESCUELA_POR_CLAVE = {_clave_escuela(e): e for e in ESCUELAS_OFICIALES}


def escuela_oficial(nombre: str) -> str:
    """Nombre oficial de la escuela (ya normalizado con norm_text); si no es una de las oficiales, igual."""
    return _ESCUELA_POR_CLAVE.get(_clave_escuela(nombre), nombre)


def es_esquema_limpio(schema: str) -> bool:
    """True para la base nueva (fabrica1): sin reemplazo, con IDs de secuencia y columnas de origen."""
    return schema not in ESQUEMAS_LEGADOS


def inventario_origen(svc, origen_id: str, *, listar=None) -> dict[str, dict]:
    """ID de cada archivo del origen -> su metadata (nombre original, fechas…). Solo lee."""
    listar = listar or drive.listar_hijos
    salida: dict[str, dict] = {}
    pendientes = [origen_id]
    while pendientes:
        for hijo in listar(svc, pendientes.pop()):
            if hijo.get("mimeType") == MIME_FOLDER:
                pendientes.append(hijo["id"])
            else:
                salida[hijo["id"]] = hijo
    return salida


def _heredados():
    carpeta = str(ROOT / "LMS_Fabrica")
    if carpeta not in sys.path:
        sys.path.insert(0, carpeta)
    import cargar_base_gcp, generar_base_rutas
    return cargar_base_gcp, generar_base_rutas


def escanear_lote(
    svc, destino_id: str, lote, destino_nombre: str, *, avance=None, con_origen: bool = False,
) -> list[dict]:
    """
    Devuelve filas desnormalizadas indexables del clon resuelto.

    `avance` es un callable(hechos, total, mensaje) opcional: primero avisa que se
    está escaneando (total desconocido) y luego avanza por cada registro que se
    convierte en fila. Con `avance=None` no cambia nada.

    Con `con_origen` (esquema limpio) se inventaría también el origen del lote y
    cada fila lleva `origen_id`, `fecha_origen` y, como nombre original, el que
    tiene el archivo en el origen ("G1_imagenpng"), no el ya normalizado del clon.
    """
    _, rutas = _heredados()
    meta = {"cliente": lote.cliente_gcp, "raiz": lote.raiz_gcp}
    escuela = getattr(lote, "escuela_gcp", "")
    if escuela:  # detectada en Drive; evita que quede vacía o adivinada en Cloud SQL
        meta["escuela"] = escuela
    if avance is not None:
        avance(0, 0, "Escaneando el clon")
    registros = rutas.escanear_ruta_drive(svc, destino_id, meta)
    origenes = inventario_origen(svc, lote.origen_id) if con_origen else {}
    ahora = datetime.now(timezone.utc).isoformat()
    total = len(registros)
    if avance is not None:
        avance(0, total, f"{total} archivo(s) encontrados en el clon")
    filas = []
    for reg in registros:
        if avance is not None:
            avance(len(filas) + 1, total, str(reg.get("archivo_nombre") or ""))
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
            "extension_tipo": reg.get("extension") or "",
        })
        if con_origen:
            filas[-1]["escuela_nombre"] = escuela_oficial(filas[-1]["escuela_nombre"])
            origen = origenes.get(reg.get("origen_id") or "") or {}
            filas[-1]["origen_id"] = reg.get("origen_id") or ""
            filas[-1]["fecha_origen"] = origen.get(CAMPO_FECHA_ORIGEN) or ""
            if origen.get("name"):
                filas[-1]["archivo_nombre_original"] = origen["name"]
    return filas


def problemas_filas_limpias(filas: list[dict]) -> list[str]:
    """Lo que impide cargar una fila en el esquema limpio (vacío = todo bien)."""
    problemas: list[str] = []
    vistos: dict[str, str] = {}
    for fila in filas:
        nombre = fila.get("archivo_nombre") or "(sin nombre)"
        ruta = "/".join(
            str(fila.get(c) or "") for c in ("programa_nombre", "materia_nombre") if fila.get(c)
        )
        etiqueta = f"{ruta}/{nombre}" if ruta else nombre
        origen = str(fila.get("origen_id") or "")
        if not origen:
            problemas.append(f"{etiqueta}: no se sabe de qué archivo del origen salió.")
        elif origen in vistos:
            problemas.append(f"{etiqueta}: el mismo archivo de origen ya viene como {vistos[origen]}.")
        else:
            vistos[origen] = etiqueta
        if origen and not fila.get("fecha_origen"):
            problemas.append(f"{etiqueta}: no se encontró la fecha de subida en el origen.")
        for campo, que in (("escuela_nombre", "escuela"), ("programa_nombre", "programa"),
                           ("materia_nombre", "materia"), ("granulo_codigo", "código de gránulo")):
            if not str(fila.get(campo) or "").strip():
                problemas.append(f"{etiqueta}: falta {que}.")
        escuela = str(fila.get("escuela_nombre") or "").strip()
        if escuela and escuela_oficial(escuela) not in ESCUELAS_OFICIALES:
            problemas.append(
                f"{etiqueta}: la escuela «{escuela}» no es una de las oficiales "
                f"({', '.join(ESCUELAS_OFICIALES)})."
            )
        if len(str(fila.get("granulo_codigo") or "")) > LIMITE_CODIGO_GRANULO:
            problemas.append(
                f"{etiqueta}: el código de gránulo «{fila['granulo_codigo']}» pasa de "
                f"{LIMITE_CODIGO_GRANULO} caracteres."
            )
        if len(str(fila.get("extension_tipo") or "")) > LIMITE_EXTENSION:
            problemas.append(
                f"{etiqueta}: la extensión «{fila['extension_tipo']}» pasa de {LIMITE_EXTENSION} caracteres."
            )
    return problemas


def _realinear_secuencias(cur, schema: str) -> None:
    """
    Deja cada secuencia justo después del MAX(id) real de su tabla.

    Las secuencias de PostgreSQL no retroceden con un ROLLBACK: si una carga
    falló a medias, los números que gastó quedarían como huecos. Realinear al
    empezar mantiene los IDs seguidos (1, 2, 3…). El LOCK de archivo hace que
    dos cargas a la vez se esperen en vez de pisarse.
    """
    cur.execute(f"LOCK TABLE {schema}.archivo IN SHARE ROW EXCLUSIVE MODE")
    for tabla in TABLAS_CON_SECUENCIA:
        cur.execute(
            f"SELECT setval(pg_get_serial_sequence('{schema}.{tabla}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {schema}.{tabla}), 0) + 1, false)"
        )


def _exigir_columnas_origen(cur, schema: str) -> None:
    """La tabla archivo del esquema limpio debe tener origen_id y fecha_origen."""
    cur.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema = %s AND table_name = 'archivo'
             AND column_name IN ('origen_id', 'fecha_origen')""",
        (schema,),
    )
    presentes = {str(r[0]) for r in cur.fetchall() or []}
    faltan = sorted({"origen_id", "fecha_origen"} - presentes)
    if faltan:
        raise ErrorFlujo(
            f"La tabla archivo de {schema} no tiene las columnas {', '.join(faltan)}.",
            f"Ejecuta {MIGRACION_ORIGEN} en la base y vuelve a ejecutar.",
            paso="carga",
        )


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
    """
    Conexión a Cloud SQL con los datos del entorno.

    El cifrado se exige por defecto (`DB_SSLMODE=require`) porque el servicio
    puede conectarse por la IP pública de la instancia, y entonces el tráfico
    sale a internet. Por el socket de Cloud SQL el cifrado sobra pero no
    estorba: psycopg2 lo ignora en conexiones locales por socket.
    """
    import psycopg2

    parametros = {
        "host": env.get("DB_HOST"),
        "port": int(env.get("DB_PORT", "5432")),
        "dbname": env.get("DB_NAME"),
        "user": env.get("DB_USER"),
        "password": env.get("DB_PASSWORD"),
        "connect_timeout": int(env.get("DB_TIMEOUT", "30")),
    }
    sslmode = (env.get("DB_SSLMODE") or "require").strip()
    # Por socket Unix no hay TLS que negociar; exigirlo haría fallar la conexión.
    if sslmode and not str(parametros["host"] or "").startswith("/"):
        parametros["sslmode"] = sslmode
    return psycopg2.connect(**parametros)


def cargar_lote(
    filas: list[dict],
    *,
    schema: str = "fabrica1",
    simular: bool = False,
    reemplazar: bool = True,
    conectar=conectar_desde_env,
    log=lambda _m: None,
    avance=None,
) -> dict:
    """
    Carga un lote en una sola transacción, reemplazando lo que ya hubiera.

    Con `reemplazar` (lo normal) se borran primero todas las filas de `archivo`
    de los programas del lote y luego se insertan las actuales. Si el escaneo no
    encontró nada, no se borra nada: nunca se vacía un programa por error.

    En un esquema limpio (fabrica1) `reemplazar` no aplica: ver el docstring del
    módulo. Si alguna fila está incompleta se lanza ErrorFlujo sin conectar.

    `avance` es un callable(hechos, total, mensaje) opcional con total = len(filas)
    que avanza por cada fila procesada. Con `avance=None` no cambia nada.
    """
    if not _SCHEMA_RE.fullmatch(schema or ""):
        raise ValueError(f"Esquema no válido: {schema}")
    limpio = es_esquema_limpio(schema)
    if limpio:
        reemplazar = False
        problemas = problemas_filas_limpias(filas)
        if problemas:
            raise ErrorFlujo(
                f"No se cargó nada: {len(problemas)} problema(s) en los datos del lote. "
                f"Primero: {problemas[0]}",
                "Corrige esos archivos en el origen o en el clon y vuelve a ejecutar la carga.",
                paso="carga",
                detalle="\n".join(problemas),
            )
    programas = programas_de(filas)
    hechos = 0

    def avanzar(fila: dict) -> None:
        """Una fila más procesada (insertada, ya existente o sin enlace)."""
        nonlocal hechos
        hechos += 1
        if avance is not None:
            avance(hechos, len(filas), str(fila.get("archivo_nombre") or ""))

    stats: dict = {
        "total": len(filas),
        "insertados": 0,
        "existentes": 0,
        "simulados": 0,
        "eliminados": 0,
        "programas": programas,
        "reemplazo": [],
    }
    if avance is not None:
        avance(0, len(filas), f"{len(filas)} archivo(s) por cargar")
    if simular:
        stats["simulados"] = len([f for f in filas if f.get("archivo_enlace")])
        if avance is not None:
            avance(len(filas), len(filas), "Simulación: no se escribió en la base")
        return stats
    carga, _ = _heredados()
    carga.SCHEMA = schema
    carga.USAR_SECUENCIAS = limpio
    conexion = conectar()
    cache = {}
    try:
        with conexion:
            with conexion.cursor() as cur:
                if limpio:
                    _exigir_columnas_origen(cur, schema)
                    _realinear_secuencias(cur, schema)
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
                        avanzar(fila)
                        continue
                    if carga.buscar_archivo_id(cur, enlace) is not None or (
                        limpio and _origen_cargado(cur, schema, fila["origen_id"])
                    ):
                        stats["existentes"] += 1
                        avanzar(fila)
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
                    if limpio:
                        _insertar_archivo_limpio(
                            cur, schema, carga, fila, enlace,
                            (granulo_id, raiz_id, destinatario_id, periodo_id, cliente_id, extension_id),
                        )
                        stats["insertados"] += 1
                        avanzar(fila)
                        continue
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
                    avanzar(fila)
        return stats
    finally:
        conexion.close()


def _origen_cargado(cur, schema: str, origen_id: str) -> bool:
    """¿Ese archivo del Drive origen ya está en la base limpia?"""
    cur.execute(f"SELECT id FROM {schema}.archivo WHERE origen_id = %s", (origen_id,))
    return cur.fetchone() is not None


def _insertar_archivo_limpio(cur, schema: str, carga, fila: dict, enlace: str, ids: tuple) -> int:
    """INSERT de archivo en el esquema limpio: id de la secuencia y columnas de origen."""
    cur.execute(
        f"""INSERT INTO {schema}.archivo (
        granulo_id, raiz_id, destinatario_id, periodo_id,
        cliente_id, extension_id, nombre, nombre_original,
        enlace, hash_sha256, fecha_registro, activo, origen_id, fecha_origen
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (*ids, carga.trunc(fila["archivo_nombre"]),
         carga.trunc(fila["archivo_nombre_original"]), enlace,
         fila.get("archivo_hash") or None, fila["archivo_fecha_registro"],
         str(fila.get("archivo_activo", "true")).lower() in {"true", "1", "t"},
         fila["origen_id"], fila["fecha_origen"]),
    )
    return int(cur.fetchone()[0])
