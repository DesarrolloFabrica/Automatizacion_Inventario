"""
flujo_lib/mensajes.py — errores del flujo en lenguaje de gente del común.
-------------------------------------------------------------------------
ErrorFlujo lleva un motivo ("qué pasó") y una acción ("qué hacer") pensados
para quien opera el flujo; el detalle técnico viaja aparte y solo va al log.
traducir_excepcion convierte cualquier excepción (HttpError de Google, red,
archivos locales, base de datos) en un ErrorFlujo con esas dos frases.
"""

from __future__ import annotations

import http.client, socket, ssl

try:
    from google.auth.exceptions import RefreshError
except ImportError:  # pragma: no cover - google-auth está instalado en producción
    class RefreshError(Exception):  # type: ignore[no-redef]
        """Sustituto local para que el módulo cargue aunque falte google-auth."""


_ACCION_RENOVAR = "Ejecuta python renovar_token.py y vuelve a ejecutar."
_ACCION_RED = "Revisa la conexión a internet y vuelve a ejecutar."


class ErrorFlujo(Exception):
    """Error con explicación para el usuario final; el detalle técnico va aparte."""

    def __init__(
        self,
        motivo: str,
        accion: str = "",
        *,
        paso: str = "",
        detalle: str = "",
        contexto: str = "",
    ):
        super().__init__(motivo)
        self.motivo = motivo
        self.accion = accion
        self.paso = paso
        self.detalle = detalle
        self.contexto = contexto

    def __str__(self) -> str:
        # Solo lo que debe leer una persona del común; el detalle técnico nunca va aquí.
        return f"{self.motivo} {self.accion}".strip()

    def como_dict(self) -> dict:
        """Las cinco piezas del error, listas para guardar en el estado o el log."""
        return {
            "motivo": self.motivo,
            "accion": self.accion,
            "paso": self.paso,
            "detalle": self.detalle,
            "contexto": self.contexto,
        }


def _estado_http(exc: BaseException) -> int | None:
    """Código HTTP de un HttpError (o de cualquier objeto con .resp.status); None si no aplica."""
    estado = getattr(getattr(exc, "resp", None), "status", None)
    if estado is None:
        return None
    try:
        return int(estado)
    except (TypeError, ValueError):
        return None


def _modulo_de(exc: BaseException) -> str:
    return getattr(type(exc), "__module__", "") or ""


def traducir_excepcion(exc: BaseException, *, paso: str, contexto: str = "") -> ErrorFlujo:
    """
    Convierte una excepción cualquiera en ErrorFlujo con motivo y acción claros.

    Si ya es ErrorFlujo se devuelve el mismo objeto (completando paso y contexto
    si venían vacíos). contexto describe la cosa afectada ("la carpeta origen del
    lote «X»", "RUTAS.xlsx"); vacío -> "la carpeta" / "el archivo" según el caso.
    """
    if isinstance(exc, ErrorFlujo):
        if not exc.paso:
            exc.paso = paso
        if not exc.contexto:
            exc.contexto = contexto
        return exc

    detalle = repr(exc)
    carpeta = contexto or "la carpeta"
    archivo = f"el archivo {contexto}" if contexto else "el archivo"

    def _error(motivo: str, accion: str) -> ErrorFlujo:
        return ErrorFlujo(motivo, accion, paso=paso, detalle=detalle, contexto=contexto)

    estado = _estado_http(exc)
    if estado == 404:
        return _error(
            f"No se encontró {carpeta}.",
            "Revisa el enlace en el Excel y que la carpeta no haya sido movida o eliminada.",
        )
    if estado == 403:
        return _error(
            f"La cuenta fábrica de contenidos no tiene permiso sobre {carpeta}.",
            "Pide acceso a esa carpeta y vuelve a ejecutar.",
        )
    if estado == 401 or isinstance(exc, RefreshError):
        return _error("La sesión de Google venció.", _ACCION_RENOVAR)
    if estado in (408, 429) or (estado is not None and 500 <= estado <= 599):
        return _error(
            f"Google Drive no respondió a tiempo al trabajar con {carpeta}.",
            "Vuelve a ejecutar; el flujo retoma donde quedó.",
        )
    # PermissionError y FileNotFoundError son OSError: van antes que la regla de red.
    if isinstance(exc, PermissionError):
        return _error(
            f"{archivo[:1].upper()}{archivo[1:]} está abierto o protegido.",
            "Ciérralo y vuelve a ejecutar.",
        )
    if isinstance(exc, FileNotFoundError):
        return _error(f"No se encontró {archivo}.", "Revisa la ruta indicada.")
    if _modulo_de(exc).startswith("psycopg2"):
        return _error(
            "No se pudo conectar a la base de datos.",
            "Revisa la conexión a internet, que la IP esté autorizada en Cloud SQL "
            "y los datos DB_* del archivo .env.",
        )
    if isinstance(
        exc,
        (ConnectionError, OSError, TimeoutError, socket.error, ssl.SSLError, http.client.HTTPException),
    ) or _modulo_de(exc) in ("socket", "ssl"):
        return _error("No hay conexión con Google o con la red.", _ACCION_RED)
    return _error(
        f"Ocurrió un error inesperado en el paso {paso}.",
        "Revisa el archivo de log de la corrida y comparte el detalle con soporte.",
    )
