"""
LMS_Fabrica — notificar_carga_lms.py
------------------------------------
Envía un correo por Gmail cuando termina la carga a Cloud SQL.
Lo llama cargar_base_gcp.py.
"""

from __future__ import annotations

import base64, csv, html, os
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from generar_base_lms import ENV_PATH, autenticar_drive


def cargar_destinatarios() -> list[str]:
    load_dotenv(ENV_PATH)
    crudo = os.getenv("CORREOS_AVISO", "")
    return [c.strip() for c in crudo.split(",") if c.strip()]


def programas_del_csv(csv_path: Path) -> list[str]:
    # Si el CSV no existe, no hay programas que listar.
    if not csv_path.exists():
        return []
    # Abre el CSV y junta los programa_nombre únicos.
    with csv_path.open(encoding="utf-8", newline="") as handle:
        nombres = {row.get("programa_nombre", "").strip() for row in csv.DictReader(handle)}
    # Devuelve la lista ordenada (sin vacíos).
    return sorted(n for n in nombres if n)


def consulta_sql(schema: str, programas: list[str]) -> str:
    """
    Arma el texto SQL que va pegado en el correo.
    El analista lo copia en Cloud SQL Studio para validar SOLO este lote.
    """
    # Si hay programas, el WHERE filtra por esos nombres.
    if programas:
        lista = ",\n    ".join(f"'{p}'" for p in programas)
        where = f"WHERE programa_nombre IN (\n    {lista}\n)"
    else:
        # Sin programas: deja un WHERE que no filtra (caso raro).
        where = "WHERE 1=1"

    # Devuelve la query completa (CTE + JOINs de archivo → gránulo → materia → programa…).
    # {schema} se reemplaza por fabrica_pruebas o fabrica.
    return f"""WITH base_archivos AS (
    SELECT
        a.id              AS archivo_id,
        a.nombre          AS archivo_nombre,
        a.nombre_original AS archivo_nombre_original,
        a.enlace          AS archivo_enlace,
        a.hash_sha256     AS archivo_hash,
        a.fecha_registro  AS archivo_fecha_registro,
        a.activo          AS archivo_activo,

        g.id              AS granulo_id,
        g.codigo          AS granulo_codigo,
        g.nombre          AS granulo_nombre,

        m.id              AS materia_id,
        m.semestre        AS materia_semestre,
        m.nombre          AS materia_nombre,

        p.id              AS programa_id,
        p.nombre          AS programa_nombre,

        CASE
            WHEN p.nombre ILIKE 'DIPLOMADO%'
              OR p.nombre ILIKE '%DIPLOMADO%'
                THEN 'DIPLOMADO'
            WHEN p.nombre ILIKE 'CURSO%'
              OR p.nombre ILIKE '%CURSO%'
                THEN 'CURSO'
            WHEN p.nombre ILIKE 'ESPECIALIZACION%'
              OR p.nombre ILIKE '%ESPECIALIZACION%'
                THEN 'ESPECIALIZACION'
            WHEN p.nombre ILIKE 'TECNICO%'
              OR p.nombre ILIKE '%TECNICO%'
                THEN 'TECNICO'
            WHEN p.nombre ILIKE 'TECNOLOGO%'
              OR p.nombre ILIKE '%TECNOLOGO%'
                THEN 'TECNOLOGO'
            WHEN p.nombre ILIKE 'PROFESIONAL%'
              OR p.nombre ILIKE '%PROFESIONAL%'
                THEN 'PROFESIONAL'
            ELSE 'OTRO'
        END AS tipo_programa,

        e.id              AS escuela_id,
        e.nombre          AS escuela_nombre,

        pq.id             AS paquete_id,
        pq.nombre         AS paquete_nombre,

        r.id              AS raiz_id,
        r.nombre          AS raiz_nombre,

        d.id              AS destinatario_id,
        d.codigo          AS destinatario_codigo,

        pe.id             AS periodo_id,
        pe.codigo         AS periodo_codigo,

        c.id              AS cliente_id,
        c.nombre          AS cliente_nombre,

        x.id              AS extension_id,
        x.tipo            AS extension_tipo

    FROM {schema}.archivo a
    INNER JOIN {schema}.granulo       g  ON g.id  = a.granulo_id
    INNER JOIN {schema}.materia       m  ON m.id  = g.materia_id
    INNER JOIN {schema}.programa      p  ON p.id  = m.programa_id
    INNER JOIN {schema}.escuela       e  ON e.id  = p.escuela_id
    INNER JOIN {schema}.paquete       pq ON pq.id = m.paquete_id
    INNER JOIN {schema}.raiz          r  ON r.id  = a.raiz_id
    INNER JOIN {schema}.destinatario  d  ON d.id  = a.destinatario_id
    INNER JOIN {schema}.periodo       pe ON pe.id = a.periodo_id
    INNER JOIN {schema}.cliente       c  ON c.id  = a.cliente_id
    INNER JOIN {schema}.extension     x  ON x.id  = a.extension_id
)

SELECT *
FROM base_archivos
{where}
ORDER BY
    tipo_programa,
    escuela_nombre,
    programa_nombre,
    materia_semestre,
    materia_nombre,
    granulo_codigo,
    extension_tipo,
    archivo_nombre;
"""


def resumen_lote(csv_path: Path) -> dict:
    """
    Lee el CSV una vez y saca datos para el cuerpo del correo:
    cuántas filas, programas, clientes, raíces y un enlace de ejemplo.
    """
    programas: list[str] = []
    clientes: set[str] = set()
    raices: set[str] = set()
    n = 0
    ejemplo_enlace = ""

    if csv_path.exists():
        with csv_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                # Cuenta cada fila del CSV.
                n += 1
                # Guarda nombres de programa (luego se unifican).
                p = (row.get("programa_nombre") or "").strip()
                if p:
                    programas.append(p)
                # Clientes y raíces únicos del lote.
                c = (row.get("cliente_nombre") or "").strip()
                if c:
                    clientes.add(c)
                r = (row.get("raiz_nombre") or "").strip()
                if r:
                    raices.add(r)
                # Primer enlace Drive que encuentre (para el mail).
                if not ejemplo_enlace:
                    ejemplo_enlace = (row.get("archivo_enlace") or "").strip()

    unicos = sorted(set(programas))
    return {
        "n": n,
        "programas": unicos,
        "clientes": sorted(clientes),
        "raices": sorted(raices),
        "ejemplo_enlace": ejemplo_enlace,
    }


# Links fijos a la consola GCP (para que el analista abra Studio en un clic).
GCP_STUDIO = (
    "https://console.cloud.google.com/sql/instances/planner-postgres/studio"
    "?project=it-fab-contenido-edu-1"
)
GCP_INSTANCIA = (
    "https://console.cloud.google.com/sql/instances/planner-postgres/overview"
    "?project=it-fab-contenido-edu-1"
)


def _html_aviso(
    schema: str,
    stats: dict,
    sql: str,
    lote: dict,
) -> str:
    """Construye el HTML del correo (textos, conteos, links y query)."""
    # Fecha legible del envío.
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    # La query va escapada para mostrarse como texto, no como HTML activo.
    sql_esc = html.escape(sql)
    # Lista HTML de programas del lote.
    progs = "".join(f"<li><code>{html.escape(p)}</code></li>" for p in lote.get("programas") or [])
    clientes = ", ".join(lote.get("clientes") or []) or "—"
    raices = ", ".join(lote.get("raices") or []) or "—"
    link_archivo = lote.get("ejemplo_enlace") or ""
    linea_drive = ""
    if link_archivo:
        linea_drive = (
            f'<p>Ejemplo de archivo en Drive: '
            f'<a href="{html.escape(link_archivo, quote=True)}">abrir en Drive</a></p>'
        )

    # Plantilla del cuerpo del correo.
    return f"""
    <html><body style="font-family:Calibri,Arial,sans-serif;color:#222;line-height:1.45">
      <p style="font-size:16px;color:#1e7e34"><b>Carga LMS a GCP: proceso exitoso.</b></p>
      <p>Fecha: {fecha}</p>

      <p><b>1. Qué se hizo (flujo)</b><br/>
      Excel de rutas (cliente + origen + destino) → se recorre Google Drive →
      se arma el CSV de archivos G → se carga en Cloud SQL.</p>

      <p><b>2. Dónde quedó</b><br/>
      Proyecto <code>it-fab-contenido-edu-1</code> · instancia <b>planner-postgres</b> ·
      base <b>planner_db</b> · esquema <b>{html.escape(schema)}</b>
      {"(PRUEBA; no es producción fabrica)" if schema != "fabrica" else "(PRODUCCIÓN)"}.</p>
      <p>
        <a href="{html.escape(GCP_INSTANCIA, quote=True)}">Abrir instancia SQL</a>
        &nbsp;·&nbsp;
        <a href="{html.escape(GCP_STUDIO, quote=True)}">Abrir Cloud SQL Studio</a>
      </p>
      {linea_drive}

      <p><b>3. Resultado de este lote</b><br/>
      Filas CSV: {stats.get("total_csv", lote.get("n", 0))}.
      Insertados: {stats.get("archivo_insertados", 0)}.
      Ya existían: {stats.get("archivo_existentes", 0)}.
      Actualizados (cliente/raíz): {stats.get("archivo_actualizados", 0)}.</p>
      <p>Cliente(s): <code>{html.escape(clientes)}</code><br/>
      Raíz(ces): <code>{html.escape(raices)}</code><br/>
      (Cliente = PRODUCTO, TANIA o JARVEY. LMS_correcciones usa cliente PRODUCTO y raíz LMS_Carga.)</p>
      <p>Programas de este lote (<code>programa_nombre</code> en GCP):</p>
      <ul>{progs or "<li>(ninguno)</li>"}</ul>

      <p><b>4. Cómo validar</b><br/>
      Entra a Studio (link de arriba) → base <b>planner_db</b> → pega la consulta de abajo → Ejecutar.
      La consulta une archivo + programa + materia + gránulo + cliente + raíz y filtra
      <b>solo estos programas</b>.</p>

      <pre style="background:#f4f4f4;padding:12px;overflow:auto;font-size:12px">{sql_esc}</pre>
    </body></html>
    """


def enviar_aviso_carga(
    schema: str,
    stats: dict,
    csv_path: Path,
    destinatarios: list[str] | None = None,
) -> None:
    """
    Función principal de este archivo.
    1) Destinatarios
    2) Resumen + SQL
    3) Arma el mensaje
    4) Autentica Google
    5) Envía por Gmail API
    """
    # Usa la lista que le pasen, o lee CORREOS_AVISO del .env.
    dest = destinatarios if destinatarios is not None else cargar_destinatarios()
    if not dest:
        print("Aviso: no hay CORREOS_AVISO en .env. No se envió correo.", flush=True)
        return

    # Datos del lote + query para el cuerpo.
    lote = resumen_lote(csv_path)
    sql = consulta_sql(schema, lote["programas"])

    # Asunto del correo (incluye el esquema: prueba o producción).
    asunto = f"Carga LMS GCP ({schema}): proceso exitoso"

    # Crea el mensaje MIME (To, Subject, cuerpo HTML).
    msg = MIMEMultipart()
    msg["To"] = ", ".join(dest)
    msg["Subject"] = asunto
    msg.attach(MIMEText(_html_aviso(schema, stats, sql, lote), "html", "utf-8"))

    # Autentica con el mismo OAuth de Drive/Gmail del proyecto.
    creds: Credentials = autenticar_drive()

    # Gmail API exige el mensaje en base64 URL-safe.
    crudo = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    # Cliente de la API de Gmail.
    gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)

    # Envía el correo con la cuenta del token ("me" = el usuario autenticado).
    gmail.users().messages().send(userId="me", body={"raw": crudo}).execute()

    print(f"Correo de carga LMS enviado a: {', '.join(dest)}", flush=True)
