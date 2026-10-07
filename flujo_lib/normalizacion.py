"""
flujo_lib/normalizacion.py — nombre y extensión finales de un archivo de Drive.
-------------------------------------------------------------------------------
Una sola regla para clonar, convertir, verificar y cargar: la extensión sale
del formato REAL (mimeType, vía generar_base_lms.obtener_extension) y el nombre
se arma con la base limpia + esa extensión (nombres.nombre_final). Los JPEG
quedan como PNG porque el paso de formato los convierte.

Así "G1_imagenpng" que es PNG queda "G1_imagen.png" y "G1_imagen.png" que en
realidad es PDF queda "G1_imagen.pdf", tanto en el clon como en la base.
"""

from __future__ import annotations

import sys

from . import ROOT
from .nombres import es_jpeg_real, nombre_final

# Propiedad de Drive que cada copia guarda con el ID de su archivo de origen.
# Se usa `properties` (visible para cualquier cliente OAuth) y no `appProperties`
# (privada del cliente que la escribió) para que un token nuevo la siga viendo.
PROPIEDAD_ORIGEN = "origen_id"


def _lms_en_path() -> None:
    carpeta = str(ROOT / "LMS_Fabrica")
    if carpeta not in sys.path:
        sys.path.insert(0, carpeta)


def extension_real(item: dict) -> str:
    """Formato real del archivo (mimeType), la misma regla con la que se carga a GCP."""
    _lms_en_path()
    from generar_base_lms import obtener_extension
    return obtener_extension(item)


def extension_final(item: dict) -> str:
    """Extensión con la que queda en el clon: la real, salvo JPEG -> png (se convierte)."""
    if es_jpeg_real(item.get("name", ""), item.get("mimeType")):
        return "png"
    # Docs y Sheets se identifican así en la base, pero su nombre nativo en
    # Drive no debe recibir un sufijo artificial .docs/.sheets.
    if str(item.get("mimeType") or "").startswith("application/vnd.google-apps."):
        return ""
    return extension_real(item)


def nombre_final_de(item: dict) -> str:
    """Nombre normalizado del archivo según su formato real (ver nombres.nombre_final)."""
    return nombre_final(item.get("name", ""), extension_final(item))


def origen_de(item: dict) -> str:
    """ID del archivo de origen guardado en la copia ("" si no lo tiene)."""
    return str((item.get("properties") or {}).get(PROPIEDAD_ORIGEN) or "")
