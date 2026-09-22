"""Conversión reanudable de JPG/JPEG a PNG dentro del clon de Drive."""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from PIL import Image

from . import drive
from .drive import MIME_FOLDER
from .nombres import es_jpg, nombre_png


@dataclass
class ResumenFormato:
    convertidos: int = 0
    ya_convertidos: int = 0
    errores: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return not self.errores

    def texto(self) -> str:
        partes = [f"{self.convertidos} archivo(s) convertido(s)", f"{self.ya_convertidos} ya convertido(s)"]
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


def convertir_arbol(svc, raiz_id: str, *, log=lambda _m: None, listar=drive.listar_hijos) -> ResumenFormato:
    """Convierte todos los JPG del subárbol; nunca recibe ni modifica el ID del origen."""
    resumen = ResumenFormato()

    def recorrer(carpeta_id: str, ruta: str) -> None:
        hijos = listar(svc, carpeta_id)
        archivos = [h for h in hijos if h.get("mimeType") != MIME_FOLDER]
        por_nombre = {h["name"]: h for h in archivos}
        for archivo in archivos:
            if not es_jpg(archivo["name"]):
                continue
            nombre_nuevo = nombre_png(archivo["name"])
            relativa = f"{ruta}/{archivo['name']}" if ruta else archivo["name"]
            existente = por_nombre.get(nombre_nuevo)
            try:
                if existente and int(existente.get("size") or 0) > 0:
                    drive.enviar_a_papelera(svc, archivo["id"])
                    resumen.ya_convertidos += 1
                    log(f"  [formato] {relativa} ya tenía PNG; JPG enviado a la papelera")
                    continue
                contenido = _a_png(_descargar(svc, archivo["id"]))
                media = MediaIoBaseUpload(io.BytesIO(contenido), mimetype="image/png", resumable=True)
                creado = svc.files().create(
                    body={"name": nombre_nuevo, "parents": [carpeta_id], "mimeType": "image/png"},
                    media_body=media,
                    supportsAllDrives=True,
                    fields="id, name, size",
                ).execute(num_retries=0)
                if not creado.get("id"):
                    raise RuntimeError("Drive no devolvió el ID del PNG creado")
                drive.enviar_a_papelera(svc, archivo["id"])
                resumen.convertidos += 1
                log(f"  [formato] {relativa} → {nombre_nuevo}")
            except Exception as e:
                resumen.errores.append(f"{relativa}: {e}")
        for carpeta in hijos:
            if carpeta.get("mimeType") == MIME_FOLDER:
                subruta = f"{ruta}/{carpeta['name']}" if ruta else carpeta["name"]
                recorrer(carpeta["id"], subruta)

    recorrer(raiz_id, "")
    return resumen
