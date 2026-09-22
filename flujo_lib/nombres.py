"""
flujo_lib/nombres.py — nombre canónico de archivos para comparar origen y clon.
-------------------------------------------------------------------------------
Tras convertir en el clon, el origen tiene "x.JPG" y el clon "x.png". Toda
comparación origen↔clon se hace por nombre canónico: base intacta + extensión
en minúscula, con jpg/jpeg → png. Así una reejecución de la clonación no manda
el PNG a la papelera ni vuelve a copiar el JPG.

Sin dependencias: solo texto.
"""

from __future__ import annotations

_EXTENSIONES_JPG = ("jpg", "jpeg")


def _partir(nombre: str) -> tuple[str, str]:
    """(base, extensión en minúscula). Sin punto útil -> (nombre, "")."""
    cabeza, punto, cola = nombre.rpartition(".")
    # ".env" (sin base) y "archivo." (sin cola) se tratan como sin extensión.
    if not punto or not cabeza or not cola:
        return nombre, ""
    return cabeza, cola.lower()


def extension(nombre: str) -> str:
    """"Foto.JPG" -> "jpg"; "a.b.jpeg" -> "jpeg"; sin punto (o ".env") -> ""."""
    return _partir(nombre)[1]


def base(nombre: str) -> str:
    """"Foto.JPG" -> "Foto"; "archivo" -> "archivo"; ".env" -> ".env"."""
    return _partir(nombre)[0]


def es_jpg(nombre: str) -> bool:
    """True si la extensión es jpg o jpeg, sin importar mayúsculas."""
    return extension(nombre) in _EXTENSIONES_JPG


def nombre_png(nombre: str) -> str:
    """"pieza_bogota_01.JPG" -> "pieza_bogota_01.png"; si no es jpg, el mismo nombre."""
    if not es_jpg(nombre):
        return nombre
    return f"{base(nombre)}.png"


def nombre_canonico(nombre: str) -> str:
    """
    Base intacta + "." + extensión en minúscula; jpg/jpeg -> png.
    Sin extensión devuelve el nombre tal cual.
    "x.JPG", "x.jpeg" y "x.png" comparten el canónico "x.png".
    """
    cabeza, ext = _partir(nombre)
    if not ext:
        return nombre
    if ext in _EXTENSIONES_JPG:
        ext = "png"
    return f"{cabeza}.{ext}"
