"""
flujo_lib/progreso.py — avance numérico de cada paso (para pintar una barra).
-----------------------------------------------------------------------------
Hasta ahora el avance de una corrida solo existía como líneas de log. El
frontend web necesita números: cuántos elementos van, cuántos hay y qué se está
haciendo ahora mismo. Eso es `Avance`. `Reporte` guarda el último avance de cada
par (lote, paso) y se lo pasa a quien esté escuchando (`destino`).

Nada de este módulo habla con Google, con la base de datos ni con el disco.

Uso típico:

    reporte = Reporte(destino=lambda clave, paso, av: print(clave, paso, av.porcentaje()))
    reporte.iniciar("ORIG|RAIZ", "clonacion", total=120, mensaje="Copiando…")
    reporte.paso_a_paso("ORIG|RAIZ", "clonacion", "pieza_01.png")
    reporte.terminar("ORIG|RAIZ", "clonacion", "Clonación terminada")

Los pasos que tardan (clonación, formato, verificación, carga) reciben un
callable `avance(hechos, total, mensaje)`; para conectarlo a un `Reporte` basta:

    avance = lambda hechos, total, msj: reporte.fijar(clave, "clonacion", hechos, total, msj)
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace


@dataclass
class Avance:
    """Cuánto va de un paso: elementos hechos, elementos totales y qué se hace ahora."""

    hechos: int = 0
    total: int = 0
    mensaje: str = ""

    def porcentaje(self) -> int:
        """Entero 0..100. Si el total aún no se conoce (<= 0) devuelve 0."""
        if self.total <= 0:
            return 0
        return max(0, min(100, int(self.hechos * 100 / self.total)))

    def como_dict(self) -> dict:
        """Diccionario listo para JSON (lo que guarda el estado y consume el frontend)."""
        return {
            "hechos": self.hechos,
            "total": self.total,
            "mensaje": self.mensaje,
            "porcentaje": self.porcentaje(),
        }


class Reporte:
    """
    Recibe el avance de un paso y se lo pasa a quien esté escuchando.

    `destino` es un callable(clave_lote, paso, Avance) o None. Un `Reporte()` sin
    destino no falla y sirve de "no hacer nada" (solo recuerda el último avance).
    Guarda un dict interno {(clave, paso): Avance} y avisa en cada cambio.

    Es seguro de usar desde varios hilos: el dict interno se toca siempre bajo un
    `threading.Lock` y al destino se le entrega una copia del avance, fuera del
    lock (así un destino lento no bloquea al resto ni puede provocar un interbloqueo).
    Si el destino lanza una excepción, esta sube al que llamó: un error del
    frontend no se esconde.
    """

    def __init__(self, destino=None):
        self._destino = destino
        self._avances: dict[tuple[str, str], Avance] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Interno
    # ------------------------------------------------------------------
    def _avisar(self, clave: str, paso: str, avance: Avance) -> None:
        if self._destino is not None:
            self._destino(clave, paso, avance)

    def _cambiar(self, clave: str, paso: str, cambio) -> Avance:
        """Aplica `cambio(avance)` bajo el lock y avisa al destino con una copia."""
        with self._lock:
            actual = self._avances.get((clave, paso))
            if actual is None:
                actual = Avance()
                self._avances[(clave, paso)] = actual
            cambio(actual)
            copia = replace(actual)
        self._avisar(clave, paso, copia)
        return copia

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def iniciar(self, clave: str, paso: str, total: int = 0, mensaje: str = "") -> None:
        """Arranca (o reinicia) el paso: hechos a 0 con el total que se conozca."""

        def cambio(avance: Avance) -> None:
            avance.hechos = 0
            avance.total = int(total)
            avance.mensaje = mensaje

        self._cambiar(clave, paso, cambio)

    def paso_a_paso(self, clave: str, paso: str, mensaje: str = "", incremento: int = 1) -> None:
        """Suma `incremento` elementos hechos (por defecto uno) y cambia el mensaje."""

        def cambio(avance: Avance) -> None:
            avance.hechos += int(incremento)
            if mensaje:
                avance.mensaje = mensaje

        self._cambiar(clave, paso, cambio)

    def fijar(self, clave: str, paso: str, hechos: int, total: int, mensaje: str = "") -> None:
        """
        Fija el avance completo de una vez. Es el puente con los pasos que ya
        cuentan por su cuenta (`avance(hechos, total, mensaje)` de clonación,
        formato, verificación y carga).
        """

        def cambio(avance: Avance) -> None:
            avance.hechos = int(hechos)
            avance.total = int(total)
            avance.mensaje = mensaje

        self._cambiar(clave, paso, cambio)

    def fijar_total(self, clave: str, paso: str, total: int) -> None:
        """Cambia el total (se supo tarde cuántos elementos había) sin tocar lo hecho."""

        def cambio(avance: Avance) -> None:
            avance.total = int(total)

        self._cambiar(clave, paso, cambio)

    def terminar(self, clave: str, paso: str, mensaje: str = "") -> None:
        """
        Cierra el paso: lo hecho pasa a ser el total (100 %). Si nunca se supo el
        total, se toma lo que se haya hecho; un paso sin elementos queda en 0 de 0.
        """

        def cambio(avance: Avance) -> None:
            avance.total = max(avance.total, avance.hechos)
            avance.hechos = avance.total
            if mensaje:
                avance.mensaje = mensaje

        self._cambiar(clave, paso, cambio)

    def actual(self, clave: str, paso: str) -> Avance:
        """Copia del último avance de ese paso (uno vacío si aún no hay nada)."""
        with self._lock:
            avance = self._avances.get((clave, paso))
            return replace(avance) if avance is not None else Avance()

    def todos(self) -> dict[str, dict[str, Avance]]:
        """{clave_lote: {paso: Avance}} con copias, para volcar el estado completo."""
        with self._lock:
            salida: dict[str, dict[str, Avance]] = {}
            for (clave, paso), avance in self._avances.items():
                salida.setdefault(clave, {})[paso] = replace(avance)
            return salida
