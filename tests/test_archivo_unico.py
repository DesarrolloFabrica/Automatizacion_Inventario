import hashlib
import unittest
from unittest.mock import MagicMock, patch

from flujo_lib import archivo_unico as modulo
from flujo_lib.mensajes import ErrorFlujo


class ArchivoUnicoTests(unittest.TestCase):
    def setUp(self):
        self.svc = MagicMock()
        self.conn = MagicMock()
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        self.cur.fetchall.return_value = [(42, "https://drive.google.com/file/d/destinoArchivo/view")]
        self.fuente = {"id": "origenArchivo", "name": "tema.mp3", "mimeType": "audio/mpeg"}
        self.destino = {"id": "destinoArchivo", "name": "tema.mp3", "mimeType": "audio/mpeg"}
        self.args = dict(origen="carpetaOrigen", destino="carpetaDestino", nombre="tema.mp3",
                         schema="fabrica1", conectar=lambda: self.conn)

    def ejecutar(self, **opciones):
        with patch.object(modulo.drive, "obtener_carpeta"), patch.object(
            modulo.drive, "listar_hijos", side_effect=[[self.fuente], [self.destino]]
        ):
            return modulo.reemplazar(self.svc, **(self.args | opciones))

    def test_exige_extension(self):
        for nombre in ["tema", "tema.", "carpeta/tema.mp3", "tema.mp3\nmal"]:
            with self.subTest(nombre=nombre), self.assertRaises(ErrorFlujo):
                modulo.validar_nombre(nombre)

    def test_distingue_mp3_de_pdf_y_mp4(self):
        with patch.object(modulo.drive, "obtener_carpeta"), patch.object(modulo.drive, "listar_hijos", return_value=[
            {"name": "tema.pdf", "id": "pdf", "mimeType": "application/pdf"},
            self.fuente, {"name": "tema.mp4", "id": "mp4", "mimeType": "video/mp4"}
        ]):
            self.assertEqual(modulo.localizar(self.svc, "carpeta", "tema.mp3")["id"], "origenArchivo")

    def test_rechaza_duplicados(self):
        with patch.object(modulo.drive, "obtener_carpeta"), patch.object(modulo.drive, "listar_hijos", return_value=[self.fuente] * 2):
            with self.assertRaises(ErrorFlujo):
                modulo.localizar(self.svc, "carpeta", "tema.mp3")

    def test_simulacion_no_descarga_ni_escribe(self):
        self.assertTrue(self.ejecutar()["simulado"])
        self.svc.files.assert_not_called()
        self.assertEqual(self.cur.execute.call_count, 1)
        self.conn.close.assert_called_once()

    def test_sin_fila_sql_no_modifica_drive(self):
        self.cur.fetchall.return_value = []
        with self.assertRaises(ErrorFlujo):
            self.ejecutar(simular=False)
        self.svc.files.assert_not_called()

    def test_tipo_diferente_no_modifica(self):
        self.destino["mimeType"] = "video/mp4"
        with self.assertRaises(ErrorFlujo):
            self.ejecutar(simular=False)
        self.svc.files.assert_not_called()

    def test_actualiza_solo_id_destino_y_fila_sql(self):
        datos = b"audio corregido"
        self.svc.files.return_value.update.return_value.next_chunk.return_value = (
            None, {"id": "destinoArchivo", "md5Checksum": hashlib.md5(datos).hexdigest()})
        with patch.object(modulo, "MediaIoBaseDownload") as descargar:
            def descarga(buffer, peticion):
                buffer.write(datos)
                instancia = MagicMock()
                instancia.next_chunk.return_value = (None, True)
                return instancia
            descargar.side_effect = descarga
            resultado = self.ejecutar(simular=False)
        self.assertFalse(resultado["simulado"])
        llamada = self.svc.files.return_value.update.call_args.kwargs
        self.assertEqual(llamada["fileId"], "destinoArchivo")
        self.assertEqual(llamada["body"], {})
        self.svc.files.return_value.create.assert_not_called()
        self.svc.files.return_value.delete.assert_not_called()
        self.assertEqual(self.cur.execute.call_args.args[1], (hashlib.sha256(datos).hexdigest(), 42))

    def test_nombre_destino_opcional_admite_normalizacion(self):
        self.destino["name"] = "tema_limpio.mp3"
        self.assertEqual(self.ejecutar(nombre_destino="tema_limpio.mp3")["nombre"], "tema_limpio.mp3")

    def test_misma_carpeta_no_modifica(self):
        with self.assertRaises(ErrorFlujo):
            self.ejecutar(destino="carpetaOrigen")
        self.svc.files.assert_not_called()

    def test_no_reemplaza_un_destino_vinculado_a_otro_origen(self):
        self.destino["properties"] = {"origen_id": "otroArchivo"}
        with self.assertRaises(ErrorFlujo):
            self.ejecutar(simular=False)
        self.svc.files.assert_not_called()

    def test_informa_fallo_parcial_despues_de_actualizar_drive(self):
        datos = b"audio corregido"
        self.svc.files.return_value.update.return_value.next_chunk.return_value = (
            None, {"id": "destinoArchivo", "md5Checksum": hashlib.md5(datos).hexdigest()})
        self.cur.execute.side_effect = [None, RuntimeError("base desconectada")]
        with patch.object(modulo, "MediaIoBaseDownload") as descargar:
            def descarga(buffer, peticion):
                buffer.write(datos)
                instancia = MagicMock()
                instancia.next_chunk.return_value = (None, True)
                return instancia
            descargar.side_effect = descarga
            with self.assertRaisesRegex(ErrorFlujo, "contenido de Drive fue actualizado"):
                self.ejecutar(simular=False)
        self.conn.close.assert_called_once()
