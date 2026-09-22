"""
Pruebas de la detección automática del cliente a partir de Drive.

Se reproduce la jerarquía real de la fábrica sobre el Drive simulado y se
comprueba tanto el módulo suelto como su efecto en la prevalidación.
"""

from __future__ import annotations

import unittest
from unittest import mock

from flujo_lib import clasificacion
from flujo_lib.clasificacion import ClasificacionDetectada, ascendencia, detectar
from tests.fake_drive import FakeDrive, hacer_http_error
from tests.test_prevalidacion import CABECERA, ORIGEN_A, RAIZ_A, BasePrevalidacion, _url

MIME_FOLDER = "application/vnd.google-apps.folder"


class BaseArbol(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDrive()

    def cadena(self, *nombres: str) -> str:
        """Crea carpetas anidadas de arriba hacia abajo y devuelve el id de la última."""
        padre = None
        for nombre in nombres:
            padre = self.fake.agregar_carpeta(nombre, padre)
        return padre


class TestAscendencia(BaseArbol):
    def test_devuelve_la_carpeta_y_sus_padres_de_abajo_hacia_arriba(self):
        hondo = self.cadena("PRODUCTO_FINAL", "Q2", "PRODUCTO", "ESCUELA_X", "PROGRAMA")
        nombres = [c["name"] for c in ascendencia(self.fake, hondo)]
        self.assertEqual(nombres, ["PROGRAMA", "ESCUELA_X", "PRODUCTO", "Q2", "PRODUCTO_FINAL"])

    def test_respeta_el_tope_de_niveles(self):
        hondo = self.cadena(*[f"N{i}" for i in range(10)])
        self.assertEqual(len(ascendencia(self.fake, hondo, max_niveles=3)), 3)

    def test_una_carpeta_suelta_devuelve_solo_esa(self):
        suelta = self.fake.agregar_carpeta("SOLA")
        self.assertEqual([c["name"] for c in ascendencia(self.fake, suelta)], ["SOLA"])

    def test_inicial_evita_la_primera_consulta(self):
        hondo = self.cadena("PRODUCTO", "PROGRAMA")
        inicial = self.fake.obtener(hondo)
        antes = self.fake.llamadas["get"]
        cadena = ascendencia(self.fake, hondo, inicial=inicial)
        self.assertEqual([c["name"] for c in cadena], ["PROGRAMA", "PRODUCTO"])
        self.assertEqual(self.fake.llamadas["get"] - antes, 1)  # solo el padre

    def test_inicial_sin_padres_no_se_usa(self):
        """Una carpeta leída sin el campo parents no sirve para arrancar la cadena."""
        hondo = self.cadena("PRODUCTO", "PROGRAMA")
        antes = self.fake.llamadas["get"]
        cadena = ascendencia(self.fake, hondo, inicial={"id": hondo, "name": "PROGRAMA"})
        self.assertEqual([c["name"] for c in cadena], ["PROGRAMA", "PRODUCTO"])
        self.assertEqual(self.fake.llamadas["get"] - antes, 2)


class TestDetectar(BaseArbol):
    def test_jerarquia_real_de_la_fabrica(self):
        """La ruta exacta que tiene el material en el Drive de la CUN."""
        origen = self.cadena(
            "PRODUCTO_FINAL",
            "PRODUCTO_FINAL ",
            "Q2",
            "PRODUCTO",
            "ESCUELA_CIENCIAS_SOCIALES_JURIDICAS_Y_GOBIERNO",
            "DERECHO_POR_CICLOS_PROPEDEUTICOS",
        )
        hallado = detectar(self.fake, origen)
        self.assertEqual(hallado.clasificacion, "PRODUCTO")
        self.assertEqual(hallado.cliente, "PRODUCTO")
        self.assertEqual(hallado.raiz, "LMS_Carga")
        self.assertEqual(hallado.carpeta, "PRODUCTO")
        self.assertEqual(hallado.escuela, "ESCUELA_CIENCIAS_SOCIALES_JURIDICAS_Y_GOBIERNO")
        self.assertEqual(hallado.ruta[0], "PRODUCTO_FINAL")
        self.assertEqual(hallado.ruta[-1], "DERECHO_POR_CICLOS_PROPEDEUTICOS")

    def test_producto_final_no_se_confunde_con_producto(self):
        """La raíz se llama PRODUCTO_FINAL: no debe contar como cliente."""
        origen = self.cadena("PRODUCTO_FINAL", "ESCUELA_X", "PROGRAMA")
        self.assertIsNone(detectar(self.fake, origen))

    def test_tania(self):
        origen = self.cadena("LMS_Carga", "MEN", "Q2", "TANIA", "ESCUELA_Y", "PROGRAMA")
        hallado = detectar(self.fake, origen)
        self.assertEqual(hallado.clasificacion, "TANIA")
        self.assertEqual(hallado.cliente, "TANIA")

    def test_correcciones_se_guarda_como_producto(self):
        origen = self.cadena("LMS_CORRECCIONES", "ESCUELA_Z", "PROGRAMA")
        hallado = detectar(self.fake, origen)
        self.assertEqual(hallado.clasificacion, "LMS_CORRECCIONES")
        self.assertEqual(hallado.cliente, "PRODUCTO")
        self.assertEqual(hallado.raiz, "LMS_Carga")

    def test_gana_el_ancestro_mas_cercano(self):
        origen = self.cadena("TANIA", "intermedia", "PRODUCTO", "ESCUELA_X", "PROGRAMA")
        self.assertEqual(detectar(self.fake, origen).clasificacion, "PRODUCTO")

    def test_sin_carpeta_de_cliente_no_detecta(self):
        origen = self.cadena("Mi unidad", "Trabajo", "PROGRAMA")
        self.assertIsNone(detectar(self.fake, origen))

    def test_escuela_vacia_si_la_carpeta_no_parece_escuela(self):
        origen = self.cadena("PRODUCTO", "carpeta cualquiera", "PROGRAMA")
        hallado = detectar(self.fake, origen)
        self.assertEqual(hallado.clasificacion, "PRODUCTO")
        self.assertEqual(hallado.escuela, "")

    def test_el_origen_mismo_puede_ser_la_carpeta_del_cliente(self):
        origen = self.cadena("LMS_Carga", "PRODUCTO")
        hallado = detectar(self.fake, origen)
        self.assertEqual(hallado.clasificacion, "PRODUCTO")
        self.assertEqual(hallado.escuela, "")

    def test_texto_para_el_log(self):
        origen = self.cadena("PRODUCTO", "ESCUELA_X", "PROGRAMA")
        texto = detectar(self.fake, origen).texto()
        self.assertIn("PRODUCTO", texto)
        self.assertIn("ESCUELA_X", texto)


# ---------------------------------------------------------------------------
# Efecto en la prevalidación
# ---------------------------------------------------------------------------
class TestPrevalidacionCliente(BasePrevalidacion):
    """ORIGEN_A y RAIZ_A vienen de BasePrevalidacion; aquí se les pone jerarquía."""

    def colgar(self, *nombres: str) -> None:
        """Mueve ORIGEN_A bajo la jerarquía indicada (de arriba hacia abajo)."""
        padre = None
        for nombre in nombres:
            padre = self.fake.agregar_carpeta(nombre, padre)
        self.fake.files().update(
            fileId=ORIGEN_A, body={}, addParents=padre, supportsAllDrives=True
        ).execute()

    def excel_sin_cliente(self):
        return self.excel([["etiqueta", "origen", "destino"], ["Bogotá", _url(ORIGEN_A), _url(RAIZ_A)]])

    def test_sin_cliente_en_el_excel_se_detecta_de_drive(self):
        self.colgar("PRODUCTO_FINAL", "Q2", "PRODUCTO", "ESCUELA_DERECHO")
        res = self.prevalidar(self.excel_sin_cliente())

        self.assertTrue(res.ok, [h.mensaje for h in res.errores()])
        lote = res.lotes[0]
        self.assertEqual(lote.clasificacion, "PRODUCTO")
        self.assertEqual(lote.cliente_gcp, "PRODUCTO")
        self.assertEqual(lote.raiz_gcp, "LMS_Carga")
        self.assertEqual(lote.escuela_gcp, "ESCUELA_DERECHO")
        mensaje = self.unico(res, "cliente", "ok").mensaje
        self.assertIn("detectado", mensaje)
        self.assertIn("PRODUCTO", mensaje)

    def test_sin_cliente_y_sin_poder_detectarlo_es_error(self):
        res = self.prevalidar(self.excel_sin_cliente())  # ORIGEN_A cuelga de nada
        self.assertFalse(res.ok)
        err = self.unico(res, "cliente", "error")
        self.assertIn("No se pudo saber a qué cliente pertenece", err.mensaje)
        self.assertIn("columna cliente", err.accion)

    def test_el_excel_manda_y_avisa_cuando_drive_dice_otra_cosa(self):
        self.colgar("PRODUCTO_FINAL", "Q2", "TANIA", "ESCUELA_DERECHO")
        excel = self.excel([CABECERA, ["PRODUCTO", "Bogotá", _url(ORIGEN_A), _url(RAIZ_A)]])
        res = self.prevalidar(excel)

        self.assertTrue(res.ok)  # un aviso no impide arrancar
        self.assertEqual(res.lotes[0].clasificacion, "PRODUCTO")
        aviso = self.unico(res, "cliente", "aviso")
        self.assertIn("el Excel dice PRODUCTO", aviso.mensaje)
        self.assertIn("TANIA", aviso.mensaje)
        self.assertIn("Se usará PRODUCTO", aviso.mensaje)

    def test_el_excel_y_drive_coinciden(self):
        self.colgar("PRODUCTO_FINAL", "Q2", "PRODUCTO", "ESCUELA_DERECHO")
        excel = self.excel([CABECERA, ["PRODUCTO", "Bogotá", _url(ORIGEN_A), _url(RAIZ_A)]])
        res = self.prevalidar(excel)

        self.assertEqual(self.por_area(res, "cliente", "aviso"), [])
        self.assertIn("coincide con Drive", self.unico(res, "cliente", "ok").mensaje)
        self.assertEqual(res.lotes[0].escuela_gcp, "ESCUELA_DERECHO")

    def test_con_cliente_en_el_excel_un_fallo_de_drive_solo_avisa(self):
        excel = self.excel([CABECERA, ["PRODUCTO", "Bogotá", _url(ORIGEN_A), _url(RAIZ_A)]])
        with mock.patch.object(
            clasificacion, "ascendencia", side_effect=hacer_http_error(500)
        ):
            res = self.prevalidar(excel)
        self.assertTrue(res.ok)
        self.assertTrue(self.por_area(res, "cliente", "aviso"))
        self.assertEqual(res.lotes[0].clasificacion, "PRODUCTO")

    def test_sin_cliente_un_fallo_de_drive_es_error(self):
        with mock.patch.object(
            clasificacion, "ascendencia", side_effect=hacer_http_error(500)
        ):
            res = self.prevalidar(self.excel_sin_cliente())
        self.assertFalse(res.ok)
        self.assertTrue(self.por_area(res, "cliente", "error"))

    def test_el_mismo_origen_en_dos_lotes_se_busca_una_sola_vez(self):
        self.colgar("PRODUCTO_FINAL", "Q2", "PRODUCTO", "ESCUELA_DERECHO")
        excel = self.excel(
            [
                ["etiqueta", "origen", "destino"],
                ["Lote 1", _url(ORIGEN_A), _url(RAIZ_A)],
                ["Lote 2", _url(ORIGEN_A), "raizDestino02"],
            ]
        )
        llamadas = []
        real = clasificacion.detectar

        def espia(*a, **kw):
            llamadas.append(a[1])
            return real(*a, **kw)

        with mock.patch("flujo_lib.prevalidacion.detectar_clasificacion", espia):
            res = self.prevalidar(excel)

        self.assertTrue(res.ok, [h.mensaje for h in res.errores()])
        self.assertEqual(llamadas, [ORIGEN_A])  # una sola búsqueda para los dos lotes
        self.assertTrue(all(l.clasificacion == "PRODUCTO" for l in res.lotes))


if __name__ == "__main__":
    unittest.main()
