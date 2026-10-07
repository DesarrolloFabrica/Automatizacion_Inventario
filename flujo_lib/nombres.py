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


# Extensiones que, si quedaron pegadas al final del nombre, son restos de una
# extensión y no parte del título ("G1_imagenpng" -> "G1_imagen"). Lista cerrada
# a propósito: "ai", "doc" o "mov" aparecen al final de palabras normales.
_PEGADAS = ("jpeg", "docx", "pptx", "pptm", "xlsx", "pdf", "png", "jpg", "gif",
            "mp3", "mp4", "m4a", "wav", "zip", "txt")
# Extensiones que se quitan si van tras un punto ("x.png.pdf" -> "x").
_CONOCIDAS = frozenset(_PEGADAS) | {"ai", "csv", "doc", "ini", "mov", "ppt", "quiz",
                                    "svg", "webm", "webp", "xls", "xml"}
_SEPARADORES = "_- ."


def _limpiar_base(nombre: str, extension_real: str = "") -> str:
    """
    Quita del final las extensiones viejas, con o sin punto, hasta que no quede ninguna.
    La extensión real también se quita tras un punto aunque no esté en _CONOCIDAS
    ("x.rar" con formato rar -> "x"); sin eso "x.rar" daría "x.rar.rar" y la copia
    "x.rar.rar.rar", y la limpieza de duplicados mandaría la copia a la papelera.
    """
    quitar = _CONOCIDAS | {extension_real.lower()} if extension_real else _CONOCIDAS
    base = nombre.strip()
    while True:
        cabeza, punto, cola = base.rpartition(".")
        if punto and cabeza.strip(_SEPARADORES) and cola.lower() in quitar:
            base = cabeza.rstrip(_SEPARADORES)
            continue
        minus = base.lower()
        pegada = next((e for e in _PEGADAS if minus.endswith(e)), None)
        if pegada and base[: -len(pegada)].strip(_SEPARADORES):
            base = base[: -len(pegada)].rstrip(_SEPARADORES)
            continue
        return base


def nombre_final(nombre: str, extension_real: str) -> str:
    """
    Nombre con el que el archivo queda en el clon y en la base: base limpia + "."
    + la extensión de su formato REAL (la decide el mimeType, no el nombre).

    "G1_imagenpng" (png)      -> "G1_imagen.png"
    "G1_imagenpng.png" (png)  -> "G1_imagen.png"
    "G1_imagen.png" (pdf)     -> "G1_imagen.pdf"
    "G1_imagen.png.pdf" (pdf) -> "G1_imagen.pdf"
    Sin extensión real (documento de Google o tipo desconocido) no se toca.
    Es idempotente: aplicarla al resultado devuelve el mismo nombre.
    """
    if not extension_real:
        return nombre
    base = _limpiar_base(nombre, extension_real) or nombre.strip()
    return f"{base}.{extension_real.lower()}"


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
