from __future__ import annotations

import unittest

from flujo_lib.verificacion import EXTENSIONES_POR_TIPO, verificar_lote
from tests.fake_drive import FakeDrive


def _parser(partes, _programa, _meta):
    return None if "NO_INDEX" in partes[-1] else {"ok": "1"}


class TestVerificacion(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDrive()
        self.origen = self.fake.agregar_carpeta("Programa")
        self.clon = self.fake.agregar_carpeta("Programa clon")
        self.meta = {"cliente": "PRODUCTO", "raiz": "LMS_Carga"}

    def verificar(self):
        return verificar_lote(self.fake, self.origen, self.clon, programa="Programa", meta=self.meta, parser=_parser)

    def test_clon_equivalente_jpg_png_es_ok(self):
        self.fake.agregar_archivo("foto.jpg", self.origen, contenido=b"jpg", mime="image/jpeg")
        self.fake.agregar_archivo("foto.png", self.clon, contenido=b"png", mime="image/png")
        self.assertEqual(self.verificar().estado, "ok")

    def test_detecta_faltante_sobrante_integridad_jpg_y_no_indexable(self):
        self.fake.agregar_archivo("falta.pdf", self.origen, contenido=b"a")
        self.fake.agregar_archivo("distinto.pdf", self.origen, contenido=b"a")
        self.fake.agregar_archivo("distinto.pdf", self.clon, contenido=b"b")
        self.fake.agregar_archivo("sobra.pdf", self.clon, contenido=b"x")
        self.fake.agregar_archivo("queda.JPG", self.clon, contenido=b"x", mime="image/jpeg")
        self.fake.agregar_archivo("NO_INDEX.pdf", self.clon, contenido=b"x")
        texto = " ".join(self.verificar().hallazgos)
        for esperado in ("Falta en el clon", "Sobra en el clon", "Contenido distinto", "JPG sin convertir", "no indexable"):
            self.assertIn(esperado, texto)

    def test_regla_de_ubicacion(self):
        o = self.fake.agregar_carpeta("SCORM", self.origen)
        c = self.fake.agregar_carpeta("SCORM", self.clon)
        self.fake.agregar_archivo("curso.pdf", o, contenido=b"x")
        self.fake.agregar_archivo("curso.pdf", c, contenido=b"x")
        resultado = self.verificar()
        self.assertTrue(any("mal ubicado" in h and "zip" in h for h in resultado.hallazgos))


class TestReglasDeUbicacionReales(unittest.TestCase):
    """
    Las reglas se contrastaron contra el material real de la fábrica. En
    ACTIVIDADES MOODLE conviven txt y docx (184 y 138 archivos en los dos
    programas medidos); marcar el docx como error generaba un centenar de
    avisos falsos que tapaban los problemas de verdad.
    """

    def test_moodle_acepta_txt_y_docx(self):
        permitidas = EXTENSIONES_POR_TIPO["ACTIVIDADES MOODLE"]
        self.assertIn("txt", permitidas)
        self.assertIn("docx", permitidas)

    def test_moodle_sigue_rechazando_lo_que_no_toca(self):
        self.assertNotIn("mp4", EXTENSIONES_POR_TIPO["ACTIVIDADES MOODLE"])

    def test_un_docx_en_moodle_ya_no_es_diferencia(self):
        fake = FakeDrive()
        origen = fake.agregar_carpeta("PROGRAMA")
        moodle_o = fake.agregar_carpeta("ACTIVIDADES MOODLE", origen)
        fake.agregar_archivo("ACA.docx", moodle_o, contenido=b"a")
        fake.agregar_archivo("01_Quiz.txt", moodle_o, contenido=b"b")

        clon = fake.agregar_carpeta("PROGRAMA copia")
        moodle_c = fake.agregar_carpeta("ACTIVIDADES MOODLE", clon)
        fake.agregar_archivo("ACA.docx", moodle_c, contenido=b"a")
        fake.agregar_archivo("01_Quiz.txt", moodle_c, contenido=b"b")

        resultado = verificar_lote(
            fake, origen, clon, programa="PROGRAMA",
            meta={"cliente": "PRODUCTO", "raiz": "LMS_Carga"},
            parser=lambda *_a, **_k: {"ok": True},  # indexable: aquí no se prueba eso
        )
        mal_ubicados = [h for h in resultado.hallazgos if "mal ubicado" in h]
        self.assertEqual(mal_ubicados, [])


if __name__ == "__main__":
    unittest.main()
