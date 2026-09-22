"""Pruebas de flujo_lib.mensajes: cada regla de traducir_excepcion y ErrorFlujo."""

from __future__ import annotations

import unittest

import httplib2
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from flujo_lib.mensajes import ErrorFlujo, traducir_excepcion


def _http(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), b"error")


class ErrorPsycopgFalso(Exception):
    """Imita una excepción de psycopg2 sin importar el paquete."""

    __module__ = "psycopg2.errors"


class TestErrorFlujo(unittest.TestCase):
    def test_str_sin_detalle(self):
        e = ErrorFlujo("No se pudo.", "Haz esto.", paso="x", detalle="Traceback secreto", contexto="c")
        self.assertEqual(str(e), "No se pudo. Haz esto.")
        self.assertNotIn("secreto", str(e))

    def test_str_sin_accion(self):
        self.assertEqual(str(ErrorFlujo("Solo motivo.")), "Solo motivo.")

    def test_como_dict(self):
        e = ErrorFlujo("m", "a", paso="p", detalle="d", contexto="c")
        self.assertEqual(
            e.como_dict(), {"motivo": "m", "accion": "a", "paso": "p", "detalle": "d", "contexto": "c"}
        )

    def test_es_excepcion(self):
        with self.assertRaises(ErrorFlujo):
            raise ErrorFlujo("m", "a")


class TestTraducirExcepcion(unittest.TestCase):
    CTX = "la carpeta origen del lote «Bogotá»"

    def test_error_flujo_se_devuelve_tal_cual(self):
        original = ErrorFlujo("m", "a")
        resultado = traducir_excepcion(original, paso="drive", contexto="ctx")
        self.assertIs(resultado, original)
        self.assertEqual(resultado.paso, "drive")
        self.assertEqual(resultado.contexto, "ctx")

    def test_error_flujo_no_pisa_paso_ni_contexto(self):
        original = ErrorFlujo("m", "a", paso="excel", contexto="RUTAS.xlsx")
        resultado = traducir_excepcion(original, paso="drive", contexto="otro")
        self.assertEqual((resultado.paso, resultado.contexto), ("excel", "RUTAS.xlsx"))

    def test_404(self):
        e = traducir_excepcion(_http(404), paso="drive", contexto=self.CTX)
        self.assertEqual(e.motivo, f"No se encontró {self.CTX}.")
        self.assertIn("Revisa el enlace en el Excel", e.accion)
        self.assertEqual(e.paso, "drive")
        self.assertIn("404", e.detalle)

    def test_404_sin_contexto(self):
        e = traducir_excepcion(_http(404), paso="drive")
        self.assertEqual(e.motivo, "No se encontró la carpeta.")

    def test_403(self):
        e = traducir_excepcion(_http(403), paso="drive", contexto=self.CTX)
        self.assertEqual(e.motivo, f"La cuenta fábrica de contenidos no tiene permiso sobre {self.CTX}.")
        self.assertEqual(e.accion, "Pide acceso a esa carpeta y vuelve a ejecutar.")

    def test_401(self):
        e = traducir_excepcion(_http(401), paso="drive")
        self.assertEqual(e.motivo, "La sesión de Google venció.")
        self.assertIn("renovar_token.py", e.accion)

    def test_refresh_error(self):
        e = traducir_excepcion(RefreshError("invalid_grant"), paso="token")
        self.assertEqual(e.motivo, "La sesión de Google venció.")
        self.assertIn("renovar_token.py", e.accion)

    def test_429_y_500_y_408(self):
        for status in (408, 429, 500, 503):
            with self.subTest(status=status):
                e = traducir_excepcion(_http(status), paso="clonacion", contexto=self.CTX)
                self.assertEqual(
                    e.motivo, f"Google Drive no respondió a tiempo al trabajar con {self.CTX}."
                )
                self.assertIn("retoma donde quedó", e.accion)

    def test_permission_error(self):
        e = traducir_excepcion(PermissionError(13, "denegado"), paso="excel", contexto="RUTAS.xlsx")
        self.assertEqual(e.motivo, "El archivo RUTAS.xlsx está abierto o protegido.")
        self.assertEqual(e.accion, "Ciérralo y vuelve a ejecutar.")

    def test_permission_error_sin_contexto(self):
        e = traducir_excepcion(PermissionError(), paso="excel")
        self.assertEqual(e.motivo, "El archivo está abierto o protegido.")

    def test_file_not_found(self):
        e = traducir_excepcion(FileNotFoundError(2, "no existe"), paso="excel", contexto="RUTAS.xlsx")
        self.assertEqual(e.motivo, "No se encontró el archivo RUTAS.xlsx.")
        self.assertEqual(e.accion, "Revisa la ruta indicada.")

    def test_file_not_found_sin_contexto(self):
        e = traducir_excepcion(FileNotFoundError(), paso="excel")
        self.assertEqual(e.motivo, "No se encontró el archivo.")

    def test_psycopg2(self):
        e = traducir_excepcion(ErrorPsycopgFalso("timeout"), paso="db")
        self.assertEqual(e.motivo, "No se pudo conectar a la base de datos.")
        self.assertIn("DB_*", e.accion)
        self.assertIn("Cloud SQL", e.accion)

    def test_connection_error(self):
        e = traducir_excepcion(ConnectionError("reset"), paso="drive")
        self.assertEqual(e.motivo, "No hay conexión con Google o con la red.")
        self.assertIn("conexión a internet", e.accion)

    def test_timeout_y_oserror(self):
        for exc in (TimeoutError("t"), OSError("generico")):
            with self.subTest(exc=type(exc).__name__):
                e = traducir_excepcion(exc, paso="drive")
                self.assertEqual(e.motivo, "No hay conexión con Google o con la red.")

    def test_generica(self):
        e = traducir_excepcion(ValueError("raro"), paso="verificacion")
        self.assertEqual(e.motivo, "Ocurrió un error inesperado en el paso verificacion.")
        self.assertIn("log de la corrida", e.accion)
        self.assertEqual(e.detalle, "ValueError('raro')")

    def test_detalle_no_sale_en_str(self):
        e = traducir_excepcion(ValueError("secreto tecnico"), paso="x")
        self.assertNotIn("secreto tecnico", str(e))


if __name__ == "__main__":
    unittest.main()
