"""Pruebas de flujo_lib.nombres (nombre canónico origen↔clon)."""

from __future__ import annotations

import unittest

from flujo_lib import nombres

# (nombre, extension, base, es_jpg, nombre_png, nombre_canonico)
CASOS = [
    ("Foto.JPG", "jpg", "Foto", True, "Foto.png", "Foto.png"),
    ("a.b.jpeg", "jpeg", "a.b", True, "a.b.png", "a.b.png"),
    ("archivo", "", "archivo", False, "archivo", "archivo"),
    (".env", "", ".env", False, ".env", ".env"),
    ("x.PNG", "png", "x", False, "x.PNG", "x.png"),
    ("x.Png", "png", "x", False, "x.Png", "x.png"),
    ("pieza_bogota_01.JPG", "jpg", "pieza_bogota_01", True, "pieza_bogota_01.png", "pieza_bogota_01.png"),
    ("Informe Final.PDF", "pdf", "Informe Final", False, "Informe Final.PDF", "Informe Final.pdf"),
    ("archivo.", "", "archivo.", False, "archivo.", "archivo."),
    ("", "", "", False, "", ""),
]


class TestNombres(unittest.TestCase):
    def test_extension(self):
        for nombre, ext, *_ in CASOS:
            with self.subTest(nombre=nombre):
                self.assertEqual(nombres.extension(nombre), ext)

    def test_base(self):
        for nombre, _ext, base, *_ in CASOS:
            with self.subTest(nombre=nombre):
                self.assertEqual(nombres.base(nombre), base)

    def test_es_jpg(self):
        for nombre, _ext, _base, es_jpg, *_ in CASOS:
            with self.subTest(nombre=nombre):
                self.assertEqual(nombres.es_jpg(nombre), es_jpg)

    def test_nombre_png(self):
        for nombre, _ext, _base, _jpg, png, _canon in CASOS:
            with self.subTest(nombre=nombre):
                self.assertEqual(nombres.nombre_png(nombre), png)

    def test_nombre_canonico(self):
        for nombre, _ext, _base, _jpg, _png, canon in CASOS:
            with self.subTest(nombre=nombre):
                self.assertEqual(nombres.nombre_canonico(nombre), canon)

    def test_base_se_conserva_intacta(self):
        # Mayúsculas, espacios y acentos de la base no se tocan: solo la extensión.
        self.assertEqual(nombres.nombre_canonico("Pieza  Bogotá_01.JPEG"), "Pieza  Bogotá_01.png")

    def test_jpg_y_png_comparten_canonico(self):
        variantes = {"x.JPG", "x.jpg", "x.jpeg", "x.JPEG", "x.png", "x.PNG"}
        self.assertEqual({nombres.nombre_canonico(v) for v in variantes}, {"x.png"})
        self.assertNotEqual(nombres.nombre_canonico("x.gif"), nombres.nombre_canonico("x.png"))

    def test_nombre_png_coincide_con_el_conversor(self):
        # El conversor actual usa re.sub(r"\.(jpe?g)$", ".png", nombre, flags=re.I).
        import re

        for nombre in ("Foto.JPG", "a.b.jpeg", "x.Jpg", "doc.pdf", "sin_extension"):
            esperado = re.sub(r"\.(jpe?g)$", ".png", nombre, flags=re.IGNORECASE)
            with self.subTest(nombre=nombre):
                self.assertEqual(nombres.nombre_png(nombre), esperado)


if __name__ == "__main__":
    unittest.main()
