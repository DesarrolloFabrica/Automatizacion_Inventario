"""
flujo_lib/destino.py — carpeta destino automática por lote.
-----------------------------------------------------------
En el Excel, `destino` es la carpeta RAÍZ. El flujo crea (o reutiliza) dentro
una carpeta con el nombre EXACTO del origen y clona ahí. Salvaguarda: si la
raíz ya se llama igual que el origen (Excel antiguo), se usa directamente.

Nunca borra ni mueve nada: si hay carpetas repetidas usa la primera y avisa.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import drive
from .mensajes import ErrorFlujo


class DestinoNoEncontrado(ErrorFlujo):
    """La subcarpeta con el nombre del origen no existe y no se pidió crearla."""


@dataclass(frozen=True)
class DestinoResuelto:
    """Carpeta donde se clona el lote y cómo se llegó a ella."""

    id: str
    nombre: str
    raiz_id: str
    raiz_nombre: str
    creada: bool
    directa: bool
    avisos: tuple[str, ...]


def nombres_equivalentes(a: str, b: str) -> bool:
    """Iguales salvo mayúsculas y espacios (bordes e internos). NO quita acentos."""
    return " ".join(str(a).split()).casefold() == " ".join(str(b).split()).casefold()


def _nombre(carpeta: dict, que: str) -> str:
    nombre = str(carpeta.get("name") or "").strip()
    if not nombre:
        raise ValueError(f"La carpeta {que} no tiene nombre (id={carpeta.get('id')!r}).")
    return nombre


def resolver_destino(
    svc,
    origen: dict,
    raiz: dict,
    *,
    crear: bool = True,
    listar=drive.listar_hijos,
    crear_carpeta=drive.crear_carpeta,
) -> DestinoResuelto:
    """
    Decide la carpeta destino real de un lote.

    origen y raiz son dicts con "id" y "name" (los de drive.obtener_carpeta).
      - origen.id == raiz.id -> ValueError (el Excel ya lo impide; doble seguro).
      - La raíz se llama como el origen -> se usa directamente (directa=True).
      - Dentro de la raíz hay una subcarpeta con el nombre exacto -> se reutiliza
        (si hay varias, la primera y se avisa; nunca se borra ninguna).
      - Hay una equivalente (solo cambian mayúsculas/espacios) -> se reutiliza y se avisa.
      - No hay ninguna: crear=True -> crear_carpeta(); crear=False -> DestinoNoEncontrado.
    """
    origen_id, raiz_id = str(origen.get("id") or ""), str(raiz.get("id") or "")
    if origen_id == raiz_id:
        raise ValueError(f"La carpeta origen y la raíz destino son la misma ({origen_id!r}).")
    nombre_origen = _nombre(origen, "origen")
    nombre_raiz = _nombre(raiz, "raíz destino")

    if nombres_equivalentes(nombre_origen, nombre_raiz):
        return DestinoResuelto(
            id=raiz_id,
            nombre=nombre_raiz,
            raiz_id=raiz_id,
            raiz_nombre=nombre_raiz,
            creada=False,
            directa=True,
            avisos=(
                "La carpeta destino ya se llama como el origen; "
                "se usa directamente sin crear subcarpeta.",
            ),
        )

    subcarpetas = listar(svc, raiz_id, solo_carpetas=True)
    avisos: list[str] = []
    exactas = [c for c in subcarpetas if c.get("name") == nombre_origen]
    candidatas = exactas or [
        c for c in subcarpetas if nombres_equivalentes(c.get("name") or "", nombre_origen)
    ]
    if candidatas:
        elegida = candidatas[0]
        if not exactas:
            avisos.append(
                f"Dentro de «{nombre_raiz}» se reutiliza la carpeta «{elegida['name']}», "
                f"que se llama casi igual que el origen «{nombre_origen}» "
                "(solo cambian mayúsculas o espacios)."
            )
        if len(candidatas) > 1:
            avisos.append(
                f"Dentro de «{nombre_raiz}» hay {len(candidatas)} carpetas llamadas "
                f"«{elegida['name']}»; se usa la primera y no se borra ninguna."
            )
        return DestinoResuelto(
            id=elegida["id"],
            nombre=elegida["name"],
            raiz_id=raiz_id,
            raiz_nombre=nombre_raiz,
            creada=False,
            directa=False,
            avisos=tuple(avisos),
        )

    if not crear:
        raise DestinoNoEncontrado(
            f"Dentro de «{nombre_raiz}» no existe la carpeta «{nombre_origen}».",
            "Ejecuta primero la clonación del lote.",
            paso="destino",
            contexto=f"la carpeta «{nombre_origen}» dentro de «{nombre_raiz}»",
        )
    carpeta, creada = crear_carpeta(svc, nombre_origen, raiz_id)
    return DestinoResuelto(
        id=carpeta["id"],
        nombre=carpeta.get("name") or nombre_origen,
        raiz_id=raiz_id,
        raiz_nombre=nombre_raiz,
        creada=bool(creada),
        directa=False,
        avisos=tuple(avisos),
    )


def describir(res: DestinoResuelto) -> str:
    """Frase corta para el log y el estado de la corrida."""
    if res.directa:
        return f"Carpeta destino directa «{res.nombre}» (ya se llama como el origen)"
    que = "creada" if res.creada else "reutilizada"
    return f"Carpeta destino {que} «{res.nombre}» dentro de «{res.raiz_nombre}»"
