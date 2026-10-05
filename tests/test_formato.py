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

    def test_pdf_llamado_png_solo_se_renombra(self):
        pdf = self.fake.agregar_archivo("G1_soyunaimagen.png", self.raiz, contenido=b"%PDF-1.7", mime="application/pdf")
        resumen = convertir_arbol(self.fake, self.raiz)
        self.assertTrue(resumen.ok())
        self.assertEqual((resumen.convertidos, resumen.renombrados), (0, 1))
        reg = self.fake.obtener(pdf)
        self.assertEqual((reg["name"], reg["mimeType"], reg["trashed"]), ("G1_soyunaimagen.pdf", "application/pdf", False))
        self.assertEqual(self.fake.contenido(pdf), b"%PDF-1.7")  # el contenido no se toca
        self.assertIn("renombrado(s) a su formato real", resumen.texto())

    def test_extension_pegada_de_un_clon_viejo_se_corrige(self):
        png = self.fake.agregar_archivo("G1_soyunaimagenpng.png", self.raiz, contenido=b"png", mime="image/png")
        convertir_arbol(self.fake, self.raiz)
        self.assertEqual(self.fake.obtener(png)["name"], "G1_soyunaimagen.png")

    def test_png_convertido_conserva_el_vinculo_con_el_origen(self):
        origen = self.fake.agregar_carpeta("Origen")
        jpg = self.fake.files().copy(
            fileId=self.fake.agregar_archivo("foto.JPG", origen, contenido=_jpg(), mime="image/jpeg"),
            body={"name": "foto.png", "parents": [self.raiz], "properties": {"origen_id": "ORIGEN123"}},
            fields="id",
        ).execute()["id"]
        resumen = convertir_arbol(self.fake, self.raiz)
        self.assertTrue(resumen.ok())
        vivos = [h for h in self.fake.hijos(self.raiz) if h["mimeType"] == "image/png"]
        self.assertEqual(len(vivos), 1)
        self.assertEqual(vivos[0]["name"], "foto.png")
        self.assertEqual(vivos[0]["properties"], {"origen_id": "ORIGEN123"})
        self.assertTrue(self.fake.obtener(jpg)["trashed"])

    def test_nombre_bueno_no_hace_llamadas(self):
        self.fake.agregar_archivo("G1_revista.pdf", self.raiz, contenido=b"%PDF", mime="application/pdf")
        resumen = convertir_arbol(self.fake, self.raiz)
        self.assertEqual((resumen.convertidos, resumen.renombrados), (0, 0))
        self.assertEqual(self.fake.llamadas["update"], 0)


if __name__ == "__main__":
    unittest.main()
