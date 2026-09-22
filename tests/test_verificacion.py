from __future__ import annotations

import unittest

from flujo_lib.verificacion import verificar_lote
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


if __name__ == "__main__":
    unittest.main()
