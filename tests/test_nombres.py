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


class TestNombreFinal(unittest.TestCase):
    """Base limpia + extensión del formato real (casos reales de fabrica, 2026-10-02)."""

    def test_extension_pegada_sin_punto(self):
        self.assertEqual(nombres.nombre_final("G1_soyunaimagenpng", "png"), "G1_soyunaimagen.png")

    def test_extension_pegada_y_repetida(self):
        self.assertEqual(nombres.nombre_final("G1_soyunaimagenpng.png", "png"), "G1_soyunaimagen.png")

    def test_formato_errado_manda_el_real(self):
        self.assertEqual(nombres.nombre_final("G1_soyunaimagen.png", "pdf"), "G1_soyunaimagen.pdf")
        self.assertEqual(nombres.nombre_final("G1_soyunaimagenpng", "pdf"), "G1_soyunaimagen.pdf")

    def test_doble_extension(self):
        self.assertEqual(nombres.nombre_final("G1_infografia.png.pdf", "pdf"), "G1_infografia.pdf")

    def test_separador_antes_de_la_extension(self):
        self.assertEqual(nombres.nombre_final("G1_mapa_png", "png"), "G1_mapa.png")
        self.assertEqual(nombres.nombre_final("G1_mapa .PNG", "png"), "G1_mapa.png")

    def test_jpg_convertido(self):
        self.assertEqual(nombres.nombre_final("pieza_01.JPG", "png"), "pieza_01.png")

    def test_nombre_correcto_no_cambia(self):
        for nombre, ext in (("G1_revista.pdf", "pdf"), ("G4_REVISTA_APRENDIZAJE_POR_TRANSFERENCIA.pdf", "pdf"),
                            ("01_Quiz.quiz", "quiz"), ("G2_v1.2.mp4", "mp4")):
            self.assertEqual(nombres.nombre_final(nombre, ext), nombre if nombre.endswith("." + ext) else f"{nombre}.{ext}")

    def test_sin_extension_real_no_se_toca(self):
        self.assertEqual(nombres.nombre_final("Actividad de aprendizaje", ""), "Actividad de aprendizaje")

    def test_palabras_que_parecen_extension_no_se_cortan(self):
        # "ai" o "doc" pegados al final son parte de palabras normales.
        self.assertEqual(nombres.nombre_final("G1_Hawai", "pdf"), "G1_Hawai.pdf")
        self.assertEqual(nombres.nombre_final("G1_protocolo_doc", "pdf"), "G1_protocolo_doc.pdf")

    def test_no_deja_el_nombre_vacio(self):
        self.assertEqual(nombres.nombre_final("png", "png"), "png.png")

    def test_es_idempotente(self):
        for nombre, ext in (("G1_soyunaimagenpng", "png"), ("a.png.pdf", "pdf"), ("x_png", "png")):
            una = nombres.nombre_final(nombre, ext)
            self.assertEqual(nombres.nombre_final(una, ext), una)


if __name__ == "__main__":
    unittest.main()
