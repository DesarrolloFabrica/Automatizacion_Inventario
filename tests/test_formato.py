from __future__ import annotations

import io, unittest

from PIL import Image

from flujo_lib.formato import convertir_arbol
from tests.fake_drive import FakeDrive


def _jpg() -> bytes:
    salida = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(salida, "JPEG")
    return salida.getvalue()


class TestFormato(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDrive()
        self.raiz = self.fake.agregar_carpeta("Clon")

    def test_convierte_recursivo_y_deja_origen_intacto(self):
        origen = self.fake.agregar_carpeta("Origen")
        original = self.fake.agregar_archivo("foto.JPG", origen, contenido=_jpg(), mime="image/jpeg")
        clon_jpg = self.fake.agregar_archivo("foto.JPG", self.raiz, contenido=_jpg(), mime="image/jpeg")
        sub = self.fake.agregar_carpeta("Sub", self.raiz)
        self.fake.agregar_archivo("otra.jpeg", sub, contenido=_jpg(), mime="image/jpeg")
        resumen = convertir_arbol(self.fake, self.raiz)
        self.assertTrue(resumen.ok())
        self.assertEqual(resumen.convertidos, 2)
        self.assertTrue(self.fake.obtener(clon_jpg)["trashed"])
        self.assertFalse(self.fake.obtener(original)["trashed"])
        self.assertEqual([h["name"] for h in self.fake.hijos(self.raiz) if h["mimeType"] != "application/vnd.google-apps.folder"], ["foto.png"])

    def test_reanuda_si_png_ya_existe(self):
        jpg = self.fake.agregar_archivo("foto.jpg", self.raiz, contenido=_jpg(), mime="image/jpeg")
        self.fake.agregar_archivo("foto.png", self.raiz, contenido=b"png", mime="image/png")
        resumen = convertir_arbol(self.fake, self.raiz)
        self.assertEqual((resumen.convertidos, resumen.ya_convertidos), (0, 1))
        self.assertTrue(self.fake.obtener(jpg)["trashed"])
        self.assertEqual(len([h for h in self.fake.hijos(self.raiz) if h["name"] == "foto.png"]), 1)

    def test_png_vacio_no_hace_perder_el_jpg_si_falla(self):
        jpg = self.fake.agregar_archivo("foto.jpg", self.raiz, contenido=b"no-es-imagen", mime="image/jpeg")
        self.fake.agregar_archivo("foto.png", self.raiz, contenido=b"", mime="image/png")
        resumen = convertir_arbol(self.fake, self.raiz)
        self.assertFalse(resumen.ok())
        self.assertFalse(self.fake.obtener(jpg)["trashed"])


if __name__ == "__main__":
    unittest.main()
