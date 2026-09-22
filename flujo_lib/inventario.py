"""Adaptador del inventario heredado con comparación por nombre canónico."""

from __future__ import annotations

import sys
from pathlib import Path

from . import ROOT, drive
from .drive import MIME_FOLDER
from .nombres import nombre_canonico


def _cargar_heredados():
    carpeta = str(ROOT / "CLONACION_CARPETA")
    if carpeta not in sys.path:
        sys.path.insert(0, carpeta)
    from publicar_inventario_sheets import publicar_xlsx_en_sheets
    from reporte_inventario_clon import generar_reporte_excel
    return generar_reporte_excel, publicar_xlsx_en_sheets


def generar_inventario(svc, trabajos: list[dict], salida: Path) -> Path:
    generar, _ = _cargar_heredados()
    def listar_canonico(servicio, carpeta_id):
        hijos = drive.listar_hijos(servicio, carpeta_id)
        return [dict(h, name=h["name"] if h.get("mimeType") == MIME_FOLDER else nombre_canonico(h["name"])) for h in hijos]
    resultado = generar(svc, listar_canonico, trabajos, Path(salida))
    return Path(resultado[0] if isinstance(resultado, tuple) else resultado)


def publicar_inventario(creds, xlsx: Path, correos: list[str]) -> str:
    _, publicar = _cargar_heredados()
    return publicar(creds, Path(xlsx), ROOT, correos)
