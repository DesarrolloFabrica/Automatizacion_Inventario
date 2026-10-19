"""Reemplazo puntual de contenido conservando el ID y el enlace del destino."""

import hashlib
import re
import tempfile
from urllib.parse import parse_qs, urlparse

from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from . import drive, gcp
from .mensajes import ErrorFlujo


def fallo(motivo):
    return ErrorFlujo(motivo, "Revisa las carpetas y el nombre completo, incluida la extensión.", paso="archivo_unico")


def validar_nombre(nombre):
    nombre = nombre.strip()
    if not re.fullmatch(r"[^/\\\x00-\x1f]+\.[^./\\\s]+", nombre) or nombre.startswith("."):
        raise fallo("Indica el nombre completo con extensión, por ejemplo soyunarchivo.mp3.")
    return nombre


def localizar(svc, carpeta, nombre):
    drive.obtener_carpeta(svc, carpeta)
    candidatos = [a for a in drive.listar_hijos(svc, carpeta) if a["name"] == nombre]
    if len(candidatos) != 1:
        raise fallo(f"Se encontraron {len(candidatos)} elementos llamados «{nombre}». Debe existir exactamente uno en la carpeta indicada.")
    item = candidatos[0]
    if item.get("mimeType", "").startswith("application/vnd.google-apps."):
        raise fallo("Este módulo reemplaza archivos como PDF, MP3 y MP4; no carpetas, accesos directos ni documentos nativos de Google.")
    return item


def _id_enlace(enlace):
    url = urlparse(enlace)
    partes = url.path.split("/")
    if "d" in partes:
        pos = partes.index("d") + 1
        return partes[pos] if pos < len(partes) else ""
    return parse_qs(url.query).get("id", [""])[0]


def reemplazar(svc, *, origen, destino, nombre, nombre_destino="", schema, simular=True, conectar=None):
    """Solo hijos directos. Valida Drive y la fila SQL antes de escribir contenido.

    Drive y SQL no comparten transacción: un error SQL posterior se informa como
    actualización parcial; repetir la operación sincroniza la misma fila e ID.
    """
    nombre = validar_nombre(nombre)
    nombre_destino = validar_nombre(nombre_destino) if nombre_destino.strip() else nombre
    origen_id, destino_id = drive.extraer_id_carpeta(origen), drive.extraer_id_carpeta(destino)
    if not origen_id or not destino_id or origen_id == destino_id:
        raise fallo("Indica dos carpetas válidas y diferentes de Drive.")
    fuente = localizar(svc, origen_id, nombre)
    receptor = localizar(svc, destino_id, nombre_destino)
    vinculo = (receptor.get("properties") or {}).get("origen_id")
    if vinculo and vinculo != fuente["id"]:
        raise fallo("El archivo de destino está vinculado a otro archivo de origen. Corrige el contenido del archivo original conservando su ID.")
    if fuente["id"] == receptor["id"]:
        raise fallo("El origen y el destino apuntan al mismo archivo.")
    if nombre.rsplit(".", 1)[1].lower() != nombre_destino.rsplit(".", 1)[1].lower():
        raise fallo("El archivo de origen y el de destino deben tener la misma extensión.")
    if fuente.get("mimeType") != receptor.get("mimeType"):
        raise fallo("El formato de origen no coincide con el del destino. No se puede cambiar el tipo de archivo mediante este módulo.")
    if not gcp._SCHEMA_RE.fullmatch(schema):
        raise fallo("El esquema de base de datos no es válido.")
    resultado = {"simulado": simular, "nombre": receptor["name"], "archivo_id": receptor["id"],
                 "enlace": f"https://drive.google.com/file/d/{receptor['id']}/view"}
    conn = (conectar or gcp.conectar_desde_env)()
    actualizado = False
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT id, enlace FROM {schema}.archivo WHERE enlace LIKE %s FOR UPDATE", (f"%{receptor['id']}%",))
                filas = [f for f in cur.fetchall() if _id_enlace(f[1]) == receptor["id"]]
                if len(filas) != 1:
                    raise fallo("El destino debe corresponder a exactamente un archivo ya cargado en la base de datos.")
                if simular:
                    return resultado
                # Archivo temporal en disco para no cargar un video completo en RAM.
                with tempfile.TemporaryFile() as contenido:
                    descarga = MediaIoBaseDownload(contenido, svc.files().get_media(fileId=fuente["id"], supportsAllDrives=True))
                    terminado = False
                    while not terminado:
                        _, terminado = descarga.next_chunk(num_retries=3)
                    contenido.seek(0)
                    md5 = hashlib.md5()
                    sha256 = hashlib.sha256()
                    while bloque := contenido.read(1024 * 1024):
                        md5.update(bloque)
                        sha256.update(bloque)
                    if fuente.get("md5Checksum") and fuente["md5Checksum"] != md5.hexdigest():
                        raise fallo("El origen cambió durante la descarga; vuelve a intentar.")
                    contenido.seek(0)
                    carga = MediaIoBaseUpload(contenido, mimetype=fuente["mimeType"], resumable=True)
                    req = svc.files().update(fileId=receptor["id"], media_body=carga,
                                             body={}, supportsAllDrives=True, fields="id,md5Checksum")
                    respuesta = None
                    while respuesta is None:
                        _, respuesta = req.next_chunk(num_retries=3)
                    actualizado = True
                    if respuesta.get("md5Checksum") != md5.hexdigest():
                        raise fallo("Drive recibió el archivo pero no se pudo verificar su contenido. Repite el reemplazo.")
                cur.execute(f"UPDATE {schema}.archivo SET hash_sha256 = %s WHERE id = %s", (sha256.hexdigest(), filas[0][0]))
        return resultado
    except Exception as exc:
        if actualizado:
            raise ErrorFlujo("El contenido de Drive fue actualizado, pero la operación no terminó de verificarse o sincronizarse con la base.",
                             "Repite el reemplazo del mismo archivo para completar la sincronización.", paso="archivo_unico") from exc
        raise
    finally:
        conn.close()
