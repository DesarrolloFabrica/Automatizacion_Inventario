"""
Registro del servicio: que todo lo que pasa se vea en los registros del servidor.

Sin esto el contenedor es mudo. El gestor de corridas engancha su propio
recolector al registro raíz para pintar la consola de la página, y ese
recolector solo guarda en memoria; si el raíz no tiene además una salida a
consola, los mensajes del flujo no llegan a ningún lado y depurar un despliegue
se vuelve adivinar.

En Cloud Run basta con escribir a la salida estándar: el servicio la recoge.
"""

from __future__ import annotations

import logging, sys

NOMBRE = "servidor.salida"
FORMATO = "%(asctime)s %(levelname)s %(name)s %(message)s"
# Librerías de Google muy habladoras: solo sus avisos y errores.
LOGGERS_RUIDOSOS = (
    "googleapiclient", "google", "google_auth_httplib2", "urllib3",
    "httplib2", "oauthlib", "requests_oauthlib", "google.cloud",
)


def configurar(nivel: int = logging.DEBUG, flujo=None) -> logging.Handler:
    """
    Deja el registro raíz escribiendo a la salida estándar.

    Se escucha a nivel depuración a propósito: el detalle técnico de los errores
    se emite ahí, y en el servidor no existe el archivo de corrida donde mirarlo.
    Las librerías de Google, muy habladoras, se bajan a avisos y errores.

    Es idempotente: llamarlo varias veces no duplica los mensajes, así que se
    puede invocar en cada arranque sin llevar la cuenta.
    """
    raiz = logging.getLogger()
    for existente in raiz.handlers:
        if getattr(existente, "name", "") == NOMBRE:
            existente.setLevel(nivel)
            raiz.setLevel(min(raiz.level or nivel, nivel))
            return existente

    salida = logging.StreamHandler(flujo if flujo is not None else sys.stdout)
    salida.name = NOMBRE
    salida.setLevel(nivel)
    salida.setFormatter(logging.Formatter(FORMATO))
    raiz.addHandler(salida)
    raiz.setLevel(nivel)
    for ruidoso in LOGGERS_RUIDOSOS:
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    return salida


def quitar() -> None:
    """Retira la salida a consola (para que las pruebas no ensucien la terminal)."""
    raiz = logging.getLogger()
    for existente in list(raiz.handlers):
        if getattr(existente, "name", "") == NOMBRE:
            raiz.removeHandler(existente)
