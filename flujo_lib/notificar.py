"""Construcción y envío del correo único de una corrida."""

from __future__ import annotations

import base64, html, sys
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from googleapiclient.discovery import build

from . import ROOT
from .estado import ETIQUETAS_ESTADO


def _consulta_sql(schema: str, programas: list[str]) -> str:
    carpeta = str(ROOT / "LMS_Fabrica")
    if carpeta not in sys.path:
        sys.path.insert(0, carpeta)
    from notificar_carga_lms import consulta_sql
    return consulta_sql(schema, programas)


def construir_mensaje(*, destinatarios: list[str], resultado: str, schema: str, filas: list[dict], enlace_sheet: str, estado_xlsx: Path, error_general: dict | None = None) -> MIMEMultipart:
    titulos = {"ok": "Proceso completado", "con_pendientes": "Proceso terminado con pendientes", "fallido": "El proceso necesita atención"}
    asunto = f"Fábrica de contenidos ({schema}): {titulos.get(resultado, titulos['fallido'])}"
    mensaje = MIMEMultipart()
    mensaje["To"] = ", ".join(destinatarios)
    mensaje["Subject"] = asunto
    cuerpo = [f"<h2>{html.escape(titulos.get(resultado, titulos['fallido']))}</h2>", "<table border='1' cellpadding='5'><tr><th>Lote</th><th>Destino</th><th>Clonación</th><th>Formato</th><th>Verificación</th><th>Carga</th></tr>"]
    for fila in filas:
        pasos = fila["pasos"]
        valores = [fila["etiqueta"], fila.get("destino_nombre") or "—", *[ETIQUETAS_ESTADO.get(pasos[p], pasos[p]) for p in ("clonacion", "formato", "verificacion", "carga")]]
        cuerpo.append("<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in valores) + "</tr>")
    cuerpo.append("</table>")
    if error_general:
        cuerpo.append(
            f"<p><b>Qué pasó:</b> {html.escape(error_general.get('motivo') or '')}"
            f"<br><b>Qué hacer:</b> {html.escape(error_general.get('accion') or '')}</p>"
        )
    if enlace_sheet:
        cuerpo.append(f'<p><a href="{html.escape(enlace_sheet, quote=True)}">Abrir inventario en Google Sheets</a></p>')
    for fila in filas:
        programas = fila.get("programas") or [fila.get("destino_nombre") or fila["etiqueta"]]
        cuerpo.append(f"<h3>Consulta para {html.escape(fila['etiqueta'])}</h3><pre>{html.escape(_consulta_sql(schema, programas))}</pre>")
        error = fila.get("ultimo_error")
        if error:
            cuerpo.append(f"<p><b>Qué pasó:</b> {html.escape(error.get('motivo') or '')}<br><b>Qué hacer:</b> {html.escape(error.get('accion') or '')}</p>")
    mensaje.attach(MIMEText("".join(cuerpo), "html", "utf-8"))
    contenido = Path(estado_xlsx).read_bytes()
    adjunto = MIMEApplication(contenido, Name=Path(estado_xlsx).name)
    adjunto.add_header("Content-Disposition", "attachment", filename=Path(estado_xlsx).name)
    mensaje.attach(adjunto)
    return mensaje


def enviar_correo(creds, mensaje: MIMEMultipart, *, servicio=None) -> dict:
    gmail = servicio or build("gmail", "v1", credentials=creds, cache_discovery=False)
    crudo = base64.urlsafe_b64encode(mensaje.as_bytes()).decode("ascii")
    return gmail.users().messages().send(userId="me", body={"raw": crudo}).execute()
