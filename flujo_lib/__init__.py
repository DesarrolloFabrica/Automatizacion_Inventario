"""
flujo_lib — librería compartida del run único de la fábrica de contenidos.
--------------------------------------------------------------------------
Módulos: mensajes (errores en lenguaje llano), drive (token único y API de
Google Drive), nombres, excel, destino, clonacion, estado y prevalidacion.

Este __init__ no importa nada pesado: solo expone ROOT (raíz del repo).
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
