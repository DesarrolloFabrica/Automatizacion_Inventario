"""
Certificados de confianza cuando el antivirus o la red inspeccionan el tráfico.

En los equipos de la CUN, Kaspersky Endpoint Security revisa las conexiones
seguras: reemplaza el certificado de Google por uno suyo, firmado por una
autoridad que Windows sí reconoce pero que Python no trae en su lista. Por eso
las llamadas a Google fallan con «certificate verify failed» aunque el
navegador funcione sin problema.

Aquí se arma un paquete que suma la lista de Python y la del equipo, y se le
indica a cada librería que use ese paquete. La verificación sigue activa en
todo momento: solo se amplía la lista de autoridades reconocidas, nunca se
desactiva la comprobación del certificado.
"""

from __future__ import annotations

import os, ssl
from pathlib import Path

from . import ROOT

RUTA_BUNDLE = ROOT / "certificados_confianza.pem"
# ROOT = autoridades raíz del equipo; CA = autoridades intermedias.
ALMACENES_WINDOWS = ("ROOT", "CA")
# Cada librería que usa el flujo lee una variable distinta.
VARIABLES = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")


def hay_almacen_windows() -> bool:
    """Solo Windows expone el almacén de certificados del sistema a Python."""
    return hasattr(ssl, "enum_certificates")


def certificados_del_equipo() -> list[str]:
    """Certificados que el equipo ya considera de confianza, en formato PEM."""
    if not hay_almacen_windows():
        return []
    pem: list[str] = []
    vistos: set[bytes] = set()
    for almacen in ALMACENES_WINDOWS:
        try:
            certificados = ssl.enum_certificates(almacen)
        except (OSError, PermissionError):
            continue  # un almacén no disponible no debe impedir leer los demás
        for der, codificacion, _confianza in certificados:
            if codificacion != "x509_asn" or der in vistos:
                continue
            vistos.add(der)
            pem.append(ssl.DER_cert_to_PEM_cert(der))
    return pem


def generar(destino: Path = RUTA_BUNDLE) -> tuple[Path, int]:
    """
    Escribe el paquete combinado (lista de Python + lista del equipo).
    Devuelve la ruta y cuántos certificados del equipo se sumaron.
    """
    import certifi

    propios = certificados_del_equipo()
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("\n".join([certifi.contents(), *propios]), encoding="ascii")
    return destino, len(propios)


def aplicar(ruta: Path = RUTA_BUNDLE) -> bool:
    """
    Indica a ssl, requests y httplib2 que usen el paquete.
    Devuelve False si el archivo no existe (y entonces nada se cambia).
    """
    ruta = Path(ruta)
    if not ruta.is_file():
        return False
    for variable in VARIABLES:
        os.environ[variable] = str(ruta)
    try:
        import httplib2

        httplib2.CA_CERTS = str(ruta)
    except ImportError:  # googleapiclient siempre lo trae; por si acaso
        pass
    return True


def asegurar(destino: Path = RUTA_BUNDLE, *, log=lambda _m: None) -> bool:
    """
    Prepara el paquete y lo deja aplicado, antes de la primera llamada a Google.

    Se regenera en cada corrida: es cuestión de milisegundos y evita que un
    cambio de certificados del antivirus deje el flujo caído sin explicación.
    En equipos que no son Windows no hace falta y no hace nada.
    """
    if not hay_almacen_windows():
        return False
    try:
        ruta, cuantos = generar(destino)
    except Exception as e:  # nunca impedir la corrida por esto
        log(f"No se pudieron preparar los certificados del equipo ({e}). Se usará la lista de Python.")
        return False
    if not aplicar(ruta):
        return False
    log(f"Certificados de confianza del equipo listos ({cuantos} del almacén de Windows).")
    return True
