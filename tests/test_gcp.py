from __future__ import annotations

import itertools, types, unittest
from unittest import mock

from flujo_lib import gcp


FILA = {
    "raiz_nombre": "LMS_Carga", "destinatario_codigo": "MEN", "periodo_codigo": "Q2",
    "cliente_nombre": "PRODUCTO", "escuela_nombre": "ESCUELA", "programa_nombre": "PROGRAMA",
    "materia_semestre": "1", "paquete_nombre": "NOTEBOOK", "materia_nombre": "MATERIA",
    "granulo_codigo": "G1", "granulo_nombre": "G1", "archivo_nombre": "G1.pdf",
    "archivo_nombre_original": "G1.pdf", "archivo_enlace": "https://drive/file/d/1/view",
    "archivo_hash": "", "archivo_fecha_registro": "2026-01-01T00:00:00+00:00",
    "archivo_activo": "true", "extension_tipo": "pdf",
}


class Cursor:
    """Cursor mínimo. `previos` simula lo ya cargado: {programa: cuántos archivos}."""

    def __init__(self, previos: dict[str, int] | None = None):
        self.sql = []
        self.borrados = []
        self.rowcount = 0
        self._previos = dict(previos or {})
        self._filas = []

    def __enter__(self): return self
    def __exit__(self, *_a): return False

    def execute(self, sql, params=()):
        self.sql.append((sql, params))
        self._filas = []
        texto = " ".join(str(sql).split()).upper()
        if texto.startswith("DELETE FROM"):
            programas = self._programas(params)
            self.borrados.append(programas)
            self.rowcount = sum(self._previos.pop(p, 0) for p in programas)
        elif "GROUP BY P.NOMBRE" in texto:
            programas = self._programas(params)
            self._filas = [(p, "PRODUCTO", self._previos[p]) for p in programas if p in self._previos]

    @staticmethod
    def _programas(params) -> tuple:
        """La lista de programas que va como único parámetro de esas dos consultas."""
        primero = (params or (None,))[0]
        return tuple(primero) if isinstance(primero, (list, tuple)) else ()

    def fetchall(self):
        return list(self._filas)


class Conexion:
    def __init__(self, previos: dict[str, int] | None = None):
        self.cursor_obj, self.cerrada, self.confirmada, self.revertida = Cursor(previos), False, False, False
    def __enter__(self): return self
    def __exit__(self, tipo, *_a):
        self.confirmada = tipo is None
        self.revertida = tipo is not None
        return False
    def cursor(self): return self.cursor_obj
    def close(self): self.cerrada = True


def heredado(fallar=False):
    contador = itertools.count(1)  # sin tope: un lote puede traer cientos de filas
    def crear(*_a, **_k):
        if fallar: raise RuntimeError("fallo simulado")
        return next(contador)
    return types.SimpleNamespace(
        SCHEMA="fabrica", buscar_archivo_id=lambda *_a: None, get_or_create=crear,
        get_or_create_materia=crear, get_or_create_granulo=crear,
        resolver_extension_id=crear, next_id=crear, trunc=lambda x: x,
    )


class TestGcp(unittest.TestCase):
    def test_simular_no_conecta(self):
        conectar = mock.Mock(side_effect=AssertionError("no debe conectar"))
        stats = gcp.cargar_lote([FILA], simular=True, conectar=conectar)
        self.assertEqual(stats["simulados"], 1)
        conectar.assert_not_called()

    def test_un_lote_una_transaccion_y_cierre(self):
        conexion = Conexion()
        with mock.patch.object(gcp, "_heredados", return_value=(heredado(), None)):
            stats = gcp.cargar_lote([FILA], conectar=lambda: conexion)
        self.assertEqual(stats["insertados"], 1)
        self.assertTrue(conexion.confirmada)
        self.assertTrue(conexion.cerrada)
        self.assertTrue(any("INSERT INTO fabrica_pruebas.archivo" in sql for sql, _ in conexion.cursor_obj.sql))

    def test_fallo_revierte_solo_la_transaccion(self):
        conexion = Conexion()
        with mock.patch.object(gcp, "_heredados", return_value=(heredado(fallar=True), None)):
            with self.assertRaises(RuntimeError):
                gcp.cargar_lote([FILA], conectar=lambda: conexion)
        self.assertTrue(conexion.revertida)
        self.assertTrue(conexion.cerrada)

    def test_schema_invalido(self):
        with self.assertRaises(ValueError):
            gcp.cargar_lote([], schema="fabrica;drop", simular=True)


class TestReemplazo(unittest.TestCase):
    """
    Regla: la base debe quedar igual a Drive. Al recargar un programa se borra
    lo que hubiera de ese programa y se inserta lo actual, sin duplicar.
    """

    def cargar(self, filas, previos=None, **kw):
        conexion = Conexion(previos)
        with mock.patch.object(gcp, "_heredados", return_value=(heredado(), None)):
            stats = gcp.cargar_lote(filas, conectar=lambda: conexion, **kw)
        return stats, conexion

    def test_escenario_120_archivos_que_pasan_a_150(self):
        """El caso real: un programa con 120 cargados que ahora trae 150."""
        filas = [{**FILA, "archivo_enlace": f"https://drive/file/d/{i}/view"} for i in range(150)]
        stats, conexion = self.cargar(filas, previos={"PROGRAMA": 120})

        self.assertEqual(stats["eliminados"], 120)
        self.assertEqual(stats["insertados"], 150)
        self.assertEqual(stats["existentes"], 0)
        self.assertEqual(conexion.cursor_obj.borrados, [("PROGRAMA",)])
        self.assertTrue(conexion.confirmada)

    def test_informa_que_habia_antes(self):
        stats, _ = self.cargar([FILA], previos={"PROGRAMA": 7})
        self.assertEqual(stats["reemplazo"], [("PROGRAMA", "PRODUCTO", 7)])
        self.assertEqual(stats["programas"], ["PROGRAMA"])

    def test_programa_nuevo_no_borra_nada(self):
        stats, conexion = self.cargar([FILA], previos={"OTRO_PROGRAMA": 90})
        self.assertEqual(stats["eliminados"], 0)
        self.assertEqual(stats["insertados"], 1)
        self.assertEqual(conexion.cursor_obj.borrados, [("PROGRAMA",)])  # se pidió, no había nada

    def test_solo_toca_los_programas_del_lote(self):
        """Los otros 380 archivos de la base no se tocan."""
        stats, conexion = self.cargar([FILA], previos={"PROGRAMA": 120, "OTRO": 380})
        self.assertEqual(stats["eliminados"], 120)
        self.assertEqual(conexion.cursor_obj._previos, {"OTRO": 380})

    def test_sin_filas_no_borra_nada(self):
        """Un escaneo vacío jamás puede dejar un programa sin datos."""
        stats, conexion = self.cargar([], previos={"PROGRAMA": 120})
        self.assertEqual(stats["eliminados"], 0)
        self.assertEqual(conexion.cursor_obj.borrados, [])

    def test_se_puede_desactivar_el_reemplazo(self):
        stats, conexion = self.cargar([FILA], previos={"PROGRAMA": 120}, reemplazar=False)
        self.assertEqual(stats["eliminados"], 0)
        self.assertEqual(conexion.cursor_obj.borrados, [])
        self.assertEqual(stats["insertados"], 1)

    def test_el_borrado_y_la_carga_van_en_la_misma_transaccion(self):
        """Si la inserción falla, el borrado se revierte: no se pierde nada."""
        conexion = Conexion({"PROGRAMA": 120})
        with mock.patch.object(gcp, "_heredados", return_value=(heredado(fallar=True), None)):
            with self.assertRaises(RuntimeError):
                gcp.cargar_lote([FILA], conectar=lambda: conexion)
        self.assertTrue(conexion.revertida)
        self.assertFalse(conexion.confirmada)
        self.assertTrue(conexion.cerrada)

    def test_simular_no_borra_ni_conecta(self):
        conectar = mock.Mock(side_effect=AssertionError("no debe conectar"))
        stats = gcp.cargar_lote([FILA], simular=True, conectar=conectar)
        self.assertEqual(stats["eliminados"], 0)
        conectar.assert_not_called()

    def test_programas_de_sin_repetir_y_sin_vacios(self):
        filas = [FILA, FILA, {**FILA, "programa_nombre": "OTRO"}, {**FILA, "programa_nombre": ""}]
        self.assertEqual(gcp.programas_de(filas), ["PROGRAMA", "OTRO"])


if __name__ == "__main__": unittest.main()
