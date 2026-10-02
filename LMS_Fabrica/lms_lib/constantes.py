"""
Constantes compartidas del flujo LMS (sin lógica de ejecución).
Usar vía: from lms_lib.constantes import CLIENTES_VALIDOS, EXTENSION_MAP, ...
"""

from __future__ import annotations

import os, re
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
TOKEN_PATH = BASE_DIR / "token.json"
CREDENTIALS_PATH = BASE_DIR / "credenciales.json"
_ENV_CANDIDATES = [
    BASE_DIR / ".env",
    BASE_DIR.parent / "CARGA_LMS_GCP_INV" / ".env",
]
ENV_PATH = next((p for p in _ENV_CANDIDATES if p.exists()), _ENV_CANDIDATES[0])
load_dotenv(ENV_PATH)

CLIENTES_VALIDOS = {"PRODUCTO", "TANIA", "LMS_CORRECCIONES"}
PAQUETES_VALIDOS = {"MODELO_NOTEBOOK", "NOTEBOOK", "MODELO_NOTEBOOK "}
RAIZ_DISPLAY = {"LMS_CARGA": "LMS_Carga"}
SCHEMA = os.getenv("LMS_SCHEMA", "fabrica_pruebas")

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.send",
]
MIME_FOLDER = "application/vnd.google-apps.folder"
DRIVE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
PATRON_ARCHIVO = re.compile(r"^G(\d+)(?:[_\s].*)?$", re.IGNORECASE)
PATRON_CODIGO = re.compile(r"^G(\d+)", re.IGNORECASE)
PATRON_GRANULO_NOMBRE = re.compile(r"^(G\d+)_(.+)$", re.IGNORECASE)

COLUMNAS_SALIDA = [
    "archivo_id",
    "archivo_nombre",
    "archivo_nombre_original",
    "archivo_enlace",
    "archivo_hash",
    "archivo_fecha_registro",
    "archivo_activo",
    "granulo_id",
    "granulo_codigo",
    "granulo_nombre",
    "materia_id",
    "materia_semestre",
    "materia_nombre",
    "programa_id",
    "programa_nombre",
    "escuela_id",
    "escuela_nombre",
    "paquete_id",
    "paquete_nombre",
    "raiz_id",
    "raiz_nombre",
    "destinatario_id",
    "destinatario_codigo",
    "periodo_id",
    "periodo_codigo",
    "cliente_id",
    "cliente_nombre",
    "extension_id",
    "extension_tipo",
]

EXTENSION_MAP = {
    "mp3": 1,
    "mp4": 2,
    "pdf": 3,
    "png": 5,
    "gif": 665,
    "zip": 671,
}

# Formato real según el tipo que Drive reporta (mimeType), no según el nombre.
# Por cada tipo: (extensión que se carga, extensiones de nombre que son el mismo
# formato). Si el nombre ya trae una de las aceptadas se respeta (jpeg, ai, quiz…);
# si trae otra cosa manda el tipo real: "G1_x.png" que en realidad es PDF → pdf.
MIME_EXTENSION = {
    "application/pdf": ("pdf", {"pdf", "ai"}),
    "image/png": ("png", {"png"}),
    "image/jpeg": ("jpg", {"jpg", "jpeg"}),
    "image/gif": ("gif", {"gif"}),
    "audio/mpeg": ("mp3", {"mp3"}),
    "audio/wav": ("wav", {"wav"}),
    "audio/x-wav": ("wav", {"wav"}),
    "audio/mp4": ("m4a", {"m4a"}),
    "audio/x-m4a": ("m4a", {"m4a"}),
    "video/mp4": ("mp4", {"mp4"}),
    "application/zip": ("zip", {"zip"}),
    "application/x-zip-compressed": ("zip", {"zip"}),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ("docx", {"docx"}),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ("pptx", {"pptx"}),
    "application/vnd.ms-powerpoint.presentation.macroEnabled.12": ("pptm", {"pptm"}),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ("xlsx", {"xlsx"}),
    "text/plain": ("txt", {"txt", "quiz", "ini", "csv"}),
    "text/xml": ("xml", {"xml"}),
    "application/xml": ("xml", {"xml"}),
}
# Documentos nativos de Google (Docs, Sheets, Slides…): no tienen archivo ni
# extensión; se cargan con extensión vacía, igual que los ya existentes en GCP.
MIME_GOOGLE_PREFIJO = "application/vnd.google-apps."
