"""
De qué cliente es un lote, leído de dónde vive la carpeta en Google Drive.

El material de la fábrica cuelga de una carpeta que dice a quién pertenece:

    ... / Q2 / PRODUCTO / ESCUELA_CIENCIAS_SOCIALES... / DERECHO_POR_CICLOS... / ...
              └── cliente      └── escuela                └── programa (el origen)

Subiendo por las carpetas padre del origen se encuentra esa carpeta, así que el
operador ya no tiene que escribir el cliente en el Excel. La columna `cliente`
sigue existiendo: si está llena manda sobre lo detectado, y si las dos no
coinciden el flujo avisa en vez de decidir en silencio.

De paso se obtiene la escuela, que hasta ahora se adivinaba con un CSV de
metadatos o quedaba vacía en Cloud SQL.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import drive
from .excel import CLASIFICACIONES, norm_text, normalizar_clasificacion

# Tope de saltos hacia arriba: evita recorrer una jerarquía sin fin si algo raro pasa.
MAX_NIVELES = 12


@dataclass(frozen=True)
class ClasificacionDetectada:
    """Lo que se pudo deducir de la ubicación de la carpeta en Drive."""

    clasificacion: str  # PRODUCTO | TANIA | LMS_CORRECCIONES
    cliente: str  # lo que se guarda en Cloud SQL (PRODUCTO o TANIA)
    raiz: str  # siempre LMS_Carga
    carpeta: str  # nombre de la carpeta donde se encontró
    escuela: str  # carpeta justo debajo del cliente ("" si no parece una escuela)
    ruta: tuple[str, ...]  # nombres de arriba hacia abajo, para el log

    def texto(self) -> str:
        detalle = f", escuela «{self.escuela}»" if self.escuela else ""
        return f"{self.clasificacion} según la carpeta «{self.carpeta}» de Drive{detalle}"


def es_carpeta_escuela(nombre: str) -> bool:
    """Las escuelas se nombran ESCUELA_ALGO en Drive."""
    return norm_text(nombre).startswith("ESCUELA")


def ascendencia(
    svc,
    folder_id: str,
    *,
    max_niveles: int = MAX_NIVELES,
    ejecutar=drive.ejecutar,
    inicial: dict | None = None,
) -> list[dict]:
    """
    La carpeta y sus padres, de la más honda a la más alta.
    Se detiene al llegar arriba, al repetir una carpeta o al tope de niveles.

    `inicial` evita una consulta cuando la carpeta ya se leyó antes (la
    prevalidación pasa la que guardó al revisar el acceso).
    """
    cadena: list[dict] = []
    vistos: set[str] = set()
    actual = folder_id
    siguiente = inicial if inicial is not None and "parents" in inicial else None
    for _ in range(max_niveles):
        if not actual or actual in vistos:
            break
        vistos.add(actual)
        info = siguiente or ejecutar(
            svc.files().get(fileId=actual, fields="id, name, parents", supportsAllDrives=True)
        )
        siguiente = None
        cadena.append(info)
        padres = info.get("parents") or []
        if not padres:
            break
        actual = padres[0]
    return cadena


def detectar(
    svc,
    origen_id: str,
    *,
    max_niveles: int = MAX_NIVELES,
    ejecutar=drive.ejecutar,
    ascender=None,
    inicial: dict | None = None,
) -> ClasificacionDetectada | None:
    """
    Busca, de la carpeta hacia arriba, la primera que se llame como un cliente.
    Devuelve None si ninguna coincide (entonces hace falta la columna del Excel).
    """
    cadena = (ascender or ascendencia)(
        svc, origen_id, max_niveles=max_niveles, ejecutar=ejecutar, inicial=inicial
    )
    for nivel, info in enumerate(cadena):
        clasificacion = normalizar_clasificacion(info.get("name"))
        if clasificacion is None:
            continue
        cliente, raiz = CLASIFICACIONES[clasificacion]
        # La carpeta inmediatamente por debajo del cliente suele ser la escuela.
        debajo = cadena[nivel - 1] if nivel > 0 else None
        escuela = (
            norm_text(debajo.get("name"))
            if debajo and es_carpeta_escuela(debajo.get("name", ""))
            else ""
        )
        return ClasificacionDetectada(
            clasificacion=clasificacion,
            cliente=cliente,
            raiz=raiz,
            carpeta=info.get("name", ""),
            escuela=escuela,
            ruta=tuple(x.get("name", "") for x in reversed(cadena)),
        )
    return None
