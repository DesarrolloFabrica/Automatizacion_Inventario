"""
flujo_lib/almacen.py — dónde se guardan los archivos de la corrida.
--------------------------------------------------------------------
Hoy el estado vive en archivos locales (`corridas/`). En Cloud Run el disco se
borra al terminar el contenedor, así que el mismo estado tiene que poder ir a
Google Cloud Storage sin cambiar el resto del código: quien guarda no sabe (ni
le importa) si detrás hay una carpeta o un bucket.

    almacen = crear_almacen("corridas")                  # AlmacenLocal
    almacen = crear_almacen("gs://fabrica-cun/corridas")  # AlmacenGCS

Los nombres son relativos al almacén ("RUTAS.estado.json"); pueden llevar "/"
para agrupar ("logs/20260922.log"). Nada de este módulo habla con Drive ni con
la base de datos, y `AlmacenGCS` solo importa google.cloud.storage cuando de
verdad se usa (no al importar el módulo).
"""

from __future__ import annotations

import os, tempfile
from pathlib import Path
from typing import Protocol

_TIPOS = {
    ".json": "application/json",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".log": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
}


def _tipo_de(nombre: str) -> str:
    return _TIPOS.get(Path(nombre).suffix.lower(), "application/octet-stream")


def _es_no_encontrado(error: BaseException) -> bool:
    """
    True si el error de Cloud Storage significa "ese objeto no existe".

    Se mira el nombre de la clase y el código HTTP en vez de importar
    google.api_core.exceptions: así el cliente falso de las pruebas puede lanzar
    su propio NotFound sin arrastrar la librería de Google.
    """
    if type(error).__name__ in {"NotFound", "NoSuchKey"}:
        return True
    return 404 in {getattr(error, "code", None), getattr(error, "status_code", None)}


class Almacen(Protocol):
    """Contrato mínimo para guardar y leer archivos de la corrida."""

    def leer(self, nombre: str) -> bytes | None:
        """Contenido del archivo, o None si no existe."""
        ...

    def escribir(self, nombre: str, datos: bytes) -> None:
        """Crea o reemplaza el archivo con esos bytes."""
        ...

    def listar(self, prefijo: str = "") -> list[str]:
        """Nombres que empiezan por `prefijo`, ordenados."""
        ...

    def borrar(self, nombre: str) -> None:
        """Borra el archivo; si no existe, no hace nada."""
        ...

    def ruta_visible(self, nombre: str) -> str:
        """Dónde quedó el archivo, para mostrárselo a quien opera."""
        ...


class AlmacenLocal(Almacen):
    """
    Carpeta del disco. La escritura es atómica (archivo temporal en la misma
    carpeta + os.replace), igual que el estado de siempre: si la corrida se corta
    a la mitad, nunca queda un archivo escrito a medias.
    """

    def __init__(self, carpeta: Path):
        self.carpeta = Path(carpeta)

    def _ruta(self, nombre: str) -> Path:
        return self.carpeta / nombre

    def leer(self, nombre: str) -> bytes | None:
        ruta = self._ruta(nombre)
        try:
            return ruta.read_bytes()
        except FileNotFoundError:
            return None
        except IsADirectoryError:
            return None

    def escribir(self, nombre: str, datos: bytes) -> None:
        ruta = self._ruta(nombre)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        fd, temporal = tempfile.mkstemp(prefix=ruta.name + ".", suffix=".tmp", dir=str(ruta.parent))
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(datos)
            os.replace(temporal, ruta)
        except BaseException:
            try:
                os.unlink(temporal)
            except OSError:
                pass
            raise

    def listar(self, prefijo: str = "") -> list[str]:
        if not self.carpeta.is_dir():
            return []
        nombres = []
        for ruta in self.carpeta.rglob("*"):
            if not ruta.is_file() or ruta.name.endswith(".tmp"):
                continue
            nombre = ruta.relative_to(self.carpeta).as_posix()
            if nombre.startswith(prefijo):
                nombres.append(nombre)
        return sorted(nombres)

    def borrar(self, nombre: str) -> None:
        try:
            self._ruta(nombre).unlink()
        except FileNotFoundError:
            pass

    def ruta_visible(self, nombre: str) -> str:
        return str(self._ruta(nombre))


class AlmacenGCS(Almacen):
    """
    Bucket de Google Cloud Storage, opcionalmente bajo un prefijo
    ("corridas/2026"). El cliente se inyecta para poder probarlo sin red; si no
    se inyecta, se crea uno de google.cloud.storage con las credenciales del
    entorno (en Cloud Run, la cuenta de servicio del servicio).
    """

    def __init__(self, bucket: str, prefijo: str = "", cliente=None):
        self.bucket = str(bucket).strip().strip("/")
        self.prefijo = str(prefijo or "").strip("/")
        self._cliente = cliente

    @property
    def cliente(self):
        """Cliente de Cloud Storage; se crea la primera vez que se necesita."""
        if self._cliente is None:
            from google.cloud import storage

            self._cliente = storage.Client()
        return self._cliente

    def _ruta(self, nombre: str) -> str:
        nombre = str(nombre).lstrip("/")
        return f"{self.prefijo}/{nombre}" if self.prefijo else nombre

    def _blob(self, nombre: str):
        return self.cliente.bucket(self.bucket).blob(self._ruta(nombre))

    def leer(self, nombre: str) -> bytes | None:
        try:
            return self._blob(nombre).download_as_bytes()
        except Exception as e:
            if _es_no_encontrado(e):
                return None
            raise

    def escribir(self, nombre: str, datos: bytes) -> None:
        self._blob(nombre).upload_from_string(datos, content_type=_tipo_de(nombre))

    def listar(self, prefijo: str = "") -> list[str]:
        completo = self._ruta(prefijo) if prefijo else self.prefijo
        blobs = self.cliente.list_blobs(self.bucket, prefix=completo)
        recorte = len(self.prefijo) + 1 if self.prefijo else 0
        return sorted(str(b.name)[recorte:] for b in blobs)

    def borrar(self, nombre: str) -> None:
        try:
            self._blob(nombre).delete()
        except Exception as e:
            if not _es_no_encontrado(e):
                raise

    def ruta_visible(self, nombre: str) -> str:
        return f"gs://{self.bucket}/{self._ruta(nombre)}"


def crear_almacen(destino: str | Path, *, cliente_gcs=None) -> Almacen:
    """
    Almacén según el destino: "gs://bucket/prefijo" → AlmacenGCS; cualquier otra
    cosa (una ruta) → AlmacenLocal. `cliente_gcs` solo se usa en el caso gs://.
    """
    texto = str(destino).strip()
    if texto.lower().startswith("gs://"):
        resto = texto[5:].strip("/")
        bucket, _, prefijo = resto.partition("/")
        if not bucket:
            raise ValueError(f"Destino de Cloud Storage sin bucket: {destino!r}")
        return AlmacenGCS(bucket, prefijo, cliente=cliente_gcs)
    return AlmacenLocal(Path(texto))
