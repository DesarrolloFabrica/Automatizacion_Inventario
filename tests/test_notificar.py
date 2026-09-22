from __future__ import annotations

import base64, tempfile, unittest
from email import message_from_bytes
from email.header import decode_header, make_header
from pathlib import Path
from unittest import mock

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


if __name__ == "__main__": unittest.main()
