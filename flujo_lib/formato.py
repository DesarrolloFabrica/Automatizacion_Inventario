"""
Conversión reanudable de JPG/JPEG a PNG dentro del clon de Drive.

Qué se convierte lo decide el formato real del archivo (mimeType), no el nombre:
un JPEG llamado ".png" se convierte, un PDF llamado ".jpg" no se toca y un PNG
llamado ".jpg" solo se renombra.

Además, todo archivo del clon cuyo nombre no sea su NOMBRE FINAL
(normalizacion.nombre_final_de) se renombra: "G1_x.png" que es PDF pasa a
"G1_x.pdf" y "G1_xpng.png" a "G1_x.png". Así un clon hecho antes de esta regla
también queda bien. El PNG convertido conserva `properties` (ID del origen).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from PIL import Image

from . import drive
from .drive import MIME_FOLDER
from .nombres import es_jpeg_real
from .normalizacion import nombre_final_de


@dataclass
class ResumenFormato:
    convertidos: int = 0
    ya_convertidos: int = 0
    renombrados: int = 0
    errores: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return not self.errores

    def texto(self) -> str:
        partes = [f"{self.convertidos} archivo(s) convertido(s)", f"{self.ya_convertidos} ya convertido(s)"]
        if self.renombrados:
            partes.append(f"{self.renombrados} renombrado(s) a su formato real")
        if self.errores:
            partes.append(f"{len(self.errores)} error(es)")
        return ", ".join(partes) + "."


def _descargar(svc, archivo_id: str) -> bytes:
    peticion = svc.files().get_media(fileId=archivo_id, supportsAllDrives=True)
    buffer = io.BytesIO()
    try:
        descarga = MediaIoBaseDownload(buffer, peticion)
        terminado = False
        while not terminado:
            _, terminado = descarga.next_chunk()
        return buffer.getvalue()
    except (AttributeError, TypeError):
        return peticion.execute(num_retries=0)


def _a_png(contenido: bytes) -> bytes:
    entrada, salida = io.BytesIO(contenido), io.BytesIO()
    with Image.open(entrada) as imagen:
        imagen.save(salida, format="PNG")
    return salida.getvalue()


def _necesita_formato(archivo: dict) -> bool:
    """Hay que convertirlo (JPEG real) o renombrarlo (no tiene su nombre final)."""
    return es_jpeg_real(archivo["name"], archivo.get("mimeType")) or archivo["name"] != nombre_final_de(archivo)


def _contar_jpg(svc, carpeta_id: str, listar) -> int:
    """Cuántos archivos hay que convertir o renombrar en el subárbol (para el total del avance)."""
    total = 0
    for hijo in listar(svc, carpeta_id):
        if hijo.get("mimeType") == MIME_FOLDER:
            total += _contar_jpg(svc, hijo["id"], listar)
        elif _necesita_formato(hijo):
            total += 1
    return total


def convertir_arbol(
    svc, raiz_id: str, *, log=lambda _m: None, listar=drive.listar_hijos, avance=None
) -> ResumenFormato:
    """
    Convierte todos los JPG del subárbol; nunca recibe ni modifica el ID del origen.

    `avance` es un callable(hechos, total, mensaje) opcional: el total son los
    JPG/JPEG que había al empezar y se avanza por cada uno resuelto (convertido,
    ya convertido o con error, para que la barra no se quede a medias). Con
    `avance=None` no se cuenta nada ni cambia el comportamiento.
    """
    resumen = ResumenFormato()
    estado = {"hechos": 0, "total": 0}
    if avance is not None:
        estado["total"] = _contar_jpg(svc, raiz_id, listar)
        avance(0, estado["total"], f"{estado['total']} imagen(es) por convertir")

    def informar(nombre: str) -> None:
        if avance is None:
            return
        estado["hechos"] += 1
        avance(estado["hechos"], estado["total"], nombre)

    def recorrer(carpeta_id: str, ruta: str) -> None:
        hijos = listar(svc, carpeta_id)
        archivos = [h for h in hijos if h.get("mimeType") != MIME_FOLDER]
        for archivo in archivos:
            # Decide el formato real, no el nombre: un PDF llamado .jpg no se
            # convierte (solo se renombra a .pdf) y un JPEG llamado .png sí se convierte.
            if not _necesita_formato(archivo):
                continue
            nombre_nuevo = nombre_final_de(archivo)
            relativa = f"{ruta}/{archivo['name']}" if ruta else archivo["name"]
            # PNG ya convertido en una corrida anterior (mismo nombre final, otro archivo).
            existente = next(
                (h for h in archivos if h["id"] != archivo["id"] and h["name"] == nombre_nuevo
                 and h.get("mimeType") == "image/png" and int(h.get("size") or 0) > 0),
                None,
            )
            try:
                if not es_jpeg_real(archivo["name"], archivo.get("mimeType")):
                    # El contenido ya es el formato bueno: basta con corregir el nombre.
                    svc.files().update(
                        fileId=archivo["id"], body={"name": nombre_nuevo},
                        supportsAllDrives=True, fields="id, name",
                    ).execute(num_retries=0)
                    resumen.renombrados += 1
                    log(f"  [formato] {relativa} renombrado a {nombre_nuevo} (formato real)")
                    continue
                if existente:
                    drive.enviar_a_papelera(svc, archivo["id"])
                    resumen.ya_convertidos += 1
                    log(f"  [formato] {relativa} ya tenía PNG; JPG enviado a la papelera")
                    continue
                contenido = _a_png(_descargar(svc, archivo["id"]))
                media = MediaIoBaseUpload(io.BytesIO(contenido), mimetype="image/png", resumable=True)
                cuerpo = {"name": nombre_nuevo, "parents": [carpeta_id], "mimeType": "image/png"}
                if archivo.get("properties"):
                    # El PNG reemplaza a la copia JPEG: hereda el vínculo con el origen.
                    cuerpo["properties"] = archivo["properties"]
                creado = svc.files().create(
                    body=cuerpo,
                    media_body=media,
                    supportsAllDrives=True,
                    fields="id, name, size",
                ).execute(num_retries=0)
                if not creado.get("id"):
                    raise RuntimeError("Drive no devolvió el ID del PNG creado")
                drive.enviar_a_papelera(svc, archivo["id"])
                resumen.convertidos += 1
                log(f"  [formato] {relativa} convertido a PNG ({nombre_nuevo})")
            except Exception as e:
                resumen.errores.append(f"{relativa}: {e}")
            finally:
                # También cuenta el que falló: la barra no puede quedarse a medias.
                informar(archivo["name"])
        for carpeta in hijos:
            if carpeta.get("mimeType") == MIME_FOLDER:
                subruta = f"{ruta}/{carpeta['name']}" if ruta else carpeta["name"]
                recorrer(carpeta["id"], subruta)

    recorrer(raiz_id, "")
    return resumen
