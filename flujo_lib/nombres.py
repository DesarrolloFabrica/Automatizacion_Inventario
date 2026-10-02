"""
flujo_lib/nombres.py — nombre canónico de archivos para comparar origen y clon.
-------------------------------------------------------------------------------
Tras convertir en el clon, el origen tiene "x.JPG" y el clon "x.png". Toda
comparación origen↔clon se hace por nombre canónico: base intacta + extensión
en minúscula, con jpg/jpeg → png. Así una reejecución de la clonación no manda
el PNG a la papelera ni vuelve a copiar el JPG.

Si un archivo es JPEG o no se decide por su formato real (mimeType de Drive),
no por el nombre: ver es_jpeg_real / requiere_formato.

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


# Tipos que no dicen qué es el archivo: ahí solo queda fiarse del nombre.
MIME_GENERICOS = frozenset({"", "application/octet-stream"})


def es_jpeg_real(nombre: str, mime: str | None) -> bool:
    """
    True si el archivo ES un JPEG (formato real, no el nombre).
    "foto.png" con contenido JPEG -> True; "doc.jpg" que es PDF -> False.
    Si Drive no sabe el tipo (genérico), decide el nombre.
    """
    mime = mime or ""
    if mime == "image/jpeg":
        return True
    return mime in MIME_GENERICOS and es_jpg(nombre)


def es_png_con_nombre_jpg(nombre: str, mime: str | None) -> bool:
    """Ya es PNG pero se llama .jpg: no hay que convertirlo, solo renombrarlo."""
    return (mime or "") == "image/png" and es_jpg(nombre)


def requiere_formato(nombre: str, mime: str | None) -> bool:
    """El paso de formato debe tocarlo: JPEG real (convertir) o PNG con nombre .jpg (renombrar)."""
    return es_jpeg_real(nombre, mime) or es_png_con_nombre_jpg(nombre, mime)


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
