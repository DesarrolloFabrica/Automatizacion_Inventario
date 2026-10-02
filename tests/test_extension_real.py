"""La extensión que se carga a GCP sale del formato real (mimeType), no del nombre."""

from __future__ import annotations

import unittest

from flujo_lib import gcp

_, rutas = gcp._heredados()
import generar_base_lms as lms  # noqa: E402  (queda importable tras _heredados)


def archivo(nombre: str, mime: str, extension: str | None = None) -> dict:
    """Metadatos como los entrega Drive; fileExtension sale del nombre si no se indica."""
    if extension is None:
        extension = nombre.rsplit(".", 1)[-1] if "." in nombre else ""
    return {"name": nombre, "mimeType": mime, "fileExtension": extension}


class ExtensionRealTest(unittest.TestCase):
    def test_pdf_nombrado_png_se_carga_como_pdf(self):
        self.assertEqual(lms.obtener_extension(archivo("G1_x.png", "application/pdf")), "pdf")

    def test_pdf_sin_punto_en_el_nombre(self):
        # En el clon Drive deja fileExtension vacío; antes quedaba "sin extensión".
        self.assertEqual(lms.obtener_extension(archivo("G2_DOCUMENTO_DATOSpdf", "application/pdf", "")), "pdf")

    def test_google_doc_nombrado_docx_queda_sin_extension(self):
        self.assertEqual(lms.obtener_extension(archivo("ACA.docx", "application/vnd.google-apps.document", "")), "")
        self.assertEqual(lms.obtener_extension(archivo("INVENTARIO", "application/vnd.google-apps.spreadsheet", "")), "")

    def test_nombre_y_formato_coinciden_no_cambia(self):
        casos = [
            ("G1_VIDEO.mp4", "video/mp4", "mp4"),
            ("G1_PODCAST.MP3", "audio/mpeg", "mp3"),
            ("ACA.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
            ("QUIZ_1.txt", "text/plain", "txt"),
            ("G1_SCORM.zip", "application/zip", "zip"),
            ("G1_INFOGRAFIA.png", "image/png", "png"),
        ]
        for nombre, mime, esperado in casos:
            with self.subTest(nombre=nombre):
                self.assertEqual(lms.obtener_extension(archivo(nombre, mime)), esperado)

    def test_variantes_del_mismo_formato_se_respetan(self):
        # Drive reporta .ai como PDF y .quiz como texto: son el mismo formato, no un error.
        self.assertEqual(lms.obtener_extension(archivo("propiedades.ai", "application/pdf")), "ai")
        self.assertEqual(lms.obtener_extension(archivo("PRUEBA.quiz", "text/plain")), "quiz")
        self.assertEqual(lms.obtener_extension(archivo("foto.jpeg", "image/jpeg")), "jpeg")

    def test_tipo_generico_usa_el_nombre(self):
        self.assertEqual(lms.obtener_extension(archivo("proyecto.prproj", "application/octet-stream")), "prproj")
        self.assertEqual(lms.obtener_extension(archivo("uuid", "application/octet-stream", "")), "")

    def test_escaneo_usa_el_formato_real(self):
        """El escaneo del programa (lo que termina en Cloud SQL) toma el formato real."""
        hijos = {
            "prog": [{"id": "sem", "name": "8", "mimeType": lms.MIME_FOLDER}],
            "sem": [{"id": "paq", "name": "NOTEBOOK", "mimeType": lms.MIME_FOLDER}],
            "paq": [{"id": "mat", "name": "FLUIDOS", "mimeType": lms.MIME_FOLDER}],
            "mat": [{"id": "gra", "name": "G2_HIDROSTATICA", "mimeType": lms.MIME_FOLDER}],
            "gra": [
                {"id": "f1", "name": "G2_DOCUMENTO_HIDROSTATICA.png", "mimeType": "application/pdf",
                 "fileExtension": "png", "webViewLink": "https://drive/file/d/f1/view"},
                {"id": "f2", "name": "G2_INFOGRAFIA_HIDROSTATICA.png", "mimeType": "image/png",
                 "fileExtension": "png", "webViewLink": "https://drive/file/d/f2/view"},
            ],
        }

        class Svc:
            def files(self):
                return self

            def get(self, fileId, **_):
                self._id = fileId
                return self

            def execute(self):
                return {"id": self._id, "name": "INGENIERIA_ENERGIAS"}

        original = rutas.listar_hijos
        rutas.listar_hijos = lambda _svc, carpeta_id: hijos.get(carpeta_id, [])
        try:
            registros = rutas.escanear_carpeta_programa(Svc(), "prog", {"cliente": "PRODUCTO", "escuela": "ESCUELA_DE_INGENIERIA"})
        finally:
            rutas.listar_hijos = original
        por_nombre = {r["archivo_nombre"]: r["extension"] for r in registros}
        self.assertEqual(por_nombre, {"G2_DOCUMENTO_HIDROSTATICA.png": "pdf", "G2_INFOGRAFIA_HIDROSTATICA.png": "png"})


class SinExtensionTest(unittest.TestCase):
    def test_sin_extension_se_guarda_vacia(self):
        """"sin_extension" no cabe en extension.tipo (varchar(10)); se usa '' como los ya cargados."""
        carga, _ = gcp._heredados()
        pedidos = []

        class Cur:
            def execute(self, sql, params=None):
                pedidos.append(params)

            def fetchone(self):
                return (831,)

        self.assertEqual(carga.resolver_extension_id(Cur(), "", {}), 831)
        self.assertEqual(pedidos[0], ("", ""))


def _jpeg() -> bytes:
    import io
    from PIL import Image
    salida = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(salida, "JPEG")
    return salida.getvalue()


class ConversionPorFormatoRealTest(unittest.TestCase):
    """El paso JPG→PNG decide por lo que el archivo ES, no por cómo se llama."""

    def setUp(self):
        from tests.fake_drive import FakeDrive
        self.fake = FakeDrive()
        self.raiz = self.fake.agregar_carpeta("Clon")

    def archivos(self):
        return sorted((h["name"], h["mimeType"]) for h in self.fake.hijos(self.raiz))

    def test_pdf_llamado_jpg_no_se_toca(self):
        from flujo_lib.formato import convertir_arbol
        pdf = self.fake.agregar_archivo("G1_DOCUMENTO.jpg", self.raiz, contenido=b"%PDF-1.4", mime="application/pdf")
        resumen = convertir_arbol(self.fake, self.raiz)
        # Antes intentaba convertirlo como imagen y el paso fallaba entero.
        self.assertTrue(resumen.ok())
        self.assertEqual(resumen.convertidos, 0)
        self.assertFalse(self.fake.obtener(pdf)["trashed"])

    def test_jpeg_llamado_png_se_convierte(self):
        from flujo_lib.formato import convertir_arbol
        jpeg = self.fake.agregar_archivo("G1_INFOGRAFIA.png", self.raiz, contenido=_jpeg(), mime="image/jpeg")
        resumen = convertir_arbol(self.fake, self.raiz)
        self.assertTrue(resumen.ok())
        self.assertEqual(resumen.convertidos, 1)
        self.assertTrue(self.fake.obtener(jpeg)["trashed"])
        self.assertEqual(self.archivos(), [("G1_INFOGRAFIA.png", "image/png")])

    def test_png_llamado_jpg_solo_se_renombra(self):
        from flujo_lib.formato import convertir_arbol
        png = self.fake.agregar_archivo("G1_PORTADA.jpg", self.raiz, contenido=b"png", mime="image/png")
        resumen = convertir_arbol(self.fake, self.raiz)
        self.assertTrue(resumen.ok())
        self.assertEqual(resumen.convertidos, 1)
        self.assertEqual(self.fake.obtener(png)["name"], "G1_PORTADA.png")  # mismo archivo, sin recodificar
        self.assertEqual(self.fake.contenido(png), b"png")


class VerificacionPorFormatoRealTest(unittest.TestCase):
    """La regla de ubicación por carpeta mira el formato real."""

    def verificar(self, carpeta: str, nombre: str, mime: str):
        from flujo_lib.verificacion import verificar_lote
        from tests.fake_drive import FakeDrive
        fake = FakeDrive()
        raices = []
        for raiz in ("Programa", "Programa clon"):
            r = fake.agregar_carpeta(raiz)
            fake.agregar_archivo(nombre, fake.agregar_carpeta(carpeta, r), contenido=b"x", mime=mime)
            raices.append(r)
        return verificar_lote(fake, *raices, programa="Programa", meta={}, parser=lambda *_a: {"ok": True})

    def test_pdf_llamado_png_en_portada_es_diferencia(self):
        resultado = self.verificar("PORTADA MATERIA", "PORTADA.png", "application/pdf")
        self.assertEqual(resultado.estado, "con_diferencias")
        self.assertIn("el archivo es «pdf»", resultado.hallazgos[0])

    def test_png_real_en_portada_es_ok(self):
        self.assertEqual(self.verificar("PORTADA MATERIA", "PORTADA.png", "image/png").estado, "ok")

    def test_google_doc_llamado_docx_en_moodle_se_avisa(self):
        resultado = self.verificar("ACTIVIDADES MOODLE", "ACA.docx", "application/vnd.google-apps.document")
        self.assertEqual(resultado.estado, "con_diferencias")
        self.assertIn("documento de Google", resultado.hallazgos[0])

    def test_pdf_llamado_jpg_no_se_reporta_como_jpg_sin_convertir(self):
        resultado = self.verificar("CONTENIDOS", "G1_DOCUMENTO.jpg", "application/pdf")
        self.assertFalse(any("JPG sin convertir" in h for h in resultado.hallazgos))

    def test_jpeg_llamado_png_se_reporta_sin_convertir(self):
        resultado = self.verificar("CONTENIDOS", "G1_INFOGRAFIA.png", "image/jpeg")
        self.assertTrue(any("JPG sin convertir" in h for h in resultado.hallazgos))


if __name__ == "__main__":
    unittest.main()
