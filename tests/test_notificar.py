from __future__ import annotations

import base64, tempfile, unittest
from email import message_from_bytes
from email.header import decode_header, make_header
from pathlib import Path
from unittest import mock

from flujo_lib.almacen import crear_almacen
from flujo_lib.estado import EstadoCorrida
from flujo_lib.notificar import construir_mensaje, enviar_correo


class Peticion:
    def __init__(self, fake, body): self.fake, self.body = fake, body
    def execute(self): self.fake.enviados.append(self.body); return {"id": "correo1"}


class Gmail:
    def __init__(self): self.enviados = []
    def users(self): return self
    def messages(self): return self
    def send(self, userId, body):
        assert userId == "me"
        return Peticion(self, body)


class TestNotificar(unittest.TestCase):
    def test_un_correo_con_tabla_sql_y_adjunto(self):
        filas = [{"etiqueta": "Lote A", "destino_nombre": "Programa A", "pasos": {
            "destino": "ok", "clonacion": "ok", "formato": "ok", "verificacion": "ok", "carga": "ok"
        }, "ultimo_error": None, "programas": ["PROGRAMA_A"]}]
        with tempfile.TemporaryDirectory() as tmp:
            xlsx = Path(tmp) / "estado.xlsx"
            xlsx.write_bytes(b"xlsx-falso")
            with mock.patch("flujo_lib.notificar._consulta_sql", return_value="SELECT 1;"):
                mensaje = construir_mensaje(destinatarios=["a@cun.edu.co"], resultado="ok", schema="fabrica", filas=filas, enlace_sheet="https://sheet", estado_xlsx=xlsx)
            gmail = Gmail()
            enviar_correo(object(), mensaje, servicio=gmail)
        self.assertEqual(len(gmail.enviados), 1)
        decodificado = message_from_bytes(base64.urlsafe_b64decode(gmail.enviados[0]["raw"]))
        self.assertEqual(decodificado["To"], "a@cun.edu.co")
        asunto = str(make_header(decode_header(decodificado["Subject"])))
        self.assertIn("Proceso completado", asunto)
        self.assertEqual(len(decodificado.get_payload()), 2)

    def test_fallo_muestra_que_paso_y_que_hacer_sin_traza(self):
        filas = [{"etiqueta": "Lote", "destino_nombre": "P", "pasos": {p: "fallido" for p in ("destino", "clonacion", "formato", "verificacion", "carga")}, "ultimo_error": {"motivo": "No se pudo cargar.", "accion": "Vuelve a ejecutar."}}]
        with tempfile.TemporaryDirectory() as tmp, mock.patch("flujo_lib.notificar._consulta_sql", return_value="SELECT 1"):
            ruta = Path(tmp) / "e.xlsx"; ruta.write_bytes(b"x")
            msg = construir_mensaje(destinatarios=["a@cun.edu.co"], resultado="fallido", schema="fabrica", filas=filas, enlace_sheet="", estado_xlsx=ruta)
        texto = msg.get_payload()[0].get_payload(decode=True).decode("utf-8")
        self.assertIn("Qué pasó", texto)
        self.assertIn("Vuelve a ejecutar", texto)
        self.assertNotIn("Traceback", texto)


class TestAdjuntoDelEstado(unittest.TestCase):
    """
    En el servicio desplegado el Excel de estado vive en el bucket, no en el
    disco del contenedor. El correo tiene que salir igual.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.comunes = dict(
            destinatarios=["ana@cun.edu.co"], resultado="ok", schema="fabrica_pruebas",
            filas=[], enlace_sheet="",
        )

    def partes(self, mensaje):
        return [p for p in mensaje.walk() if p.get_content_maintype() != "multipart"]

    def test_contenido_dado_se_adjunta_sin_tocar_el_disco(self):
        inexistente = self.dir / "no_existe.estado.xlsx"
        mensaje = construir_mensaje(
            **self.comunes, estado_xlsx=inexistente, contenido=b"bytes del excel"
        )
        adjuntos = [p for p in self.partes(mensaje) if p.get_filename()]
        self.assertEqual([p.get_filename() for p in adjuntos], ["no_existe.estado.xlsx"])
        self.assertEqual(adjuntos[0].get_payload(decode=True), b"bytes del excel")

    def test_sin_adjunto_el_correo_sale_igual_y_lo_dice(self):
        inexistente = self.dir / "perdido.estado.xlsx"
        mensaje = construir_mensaje(**self.comunes, estado_xlsx=inexistente)

        self.assertEqual([p.get_filename() for p in self.partes(mensaje) if p.get_filename()], [])
        cuerpo = next(
            p.get_payload(decode=True).decode("utf-8")
            for p in self.partes(mensaje) if p.get_content_type() == "text/html"
        )
        self.assertIn("no se pudo adjuntar", cuerpo)
        self.assertIn("perdido.estado.xlsx", cuerpo)

    def test_el_cuerpo_va_siempre_antes_que_el_adjunto(self):
        """Los lectores de correo muestran la primera parte: debe ser el texto."""
        mensaje = construir_mensaje(
            **self.comunes, estado_xlsx=self.dir / "x.xlsx", contenido=b"datos"
        )
        self.assertEqual(self.partes(mensaje)[0].get_content_type(), "text/html")


class TestBytesDelEstado(unittest.TestCase):
    """De dónde saca el correo el Excel: del almacén si lo hay, del disco si no."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_del_disco(self):
        estado = EstadoCorrida.abrir(self.dir / "RUTAS.xlsx", self.dir)
        estado.exportar_excel()
        self.assertTrue((estado.bytes_excel() or b"").startswith(b"PK"))  # un .xlsx es un zip

    def test_del_almacen(self):
        almacen = crear_almacen(self.dir / "bucket")
        estado = EstadoCorrida.abrir(self.dir / "RUTAS.xlsx", self.dir, almacen=almacen)
        estado.exportar_excel()
        self.assertTrue((estado.bytes_excel() or b"").startswith(b"PK"))

    def test_sin_exportar_todavia_devuelve_nada(self):
        estado = EstadoCorrida.abrir(self.dir / "RUTAS.xlsx", self.dir)
        self.assertIsNone(estado.bytes_excel())


if __name__ == "__main__": unittest.main()
