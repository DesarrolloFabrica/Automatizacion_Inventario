"""Pruebas de flujo_lib.drive y del Drive falso (tests/fake_drive.py). Sin red."""

from __future__ import annotations

import functools, http.client, json, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import httplib2
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from flujo_lib import drive
from flujo_lib.mensajes import ErrorFlujo
from tests.fake_drive import MIME_FOLDER, FakeDrive

SIN_ESPERA = functools.partial(drive.ejecutar, dormir=lambda _s: None)


def _http(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), b"error")


def _datos_token(scopes: list[str], horas: float = 1) -> dict:
    """token.json mínimo como el que escribe creds.to_json()."""
    expiry = (datetime.now(timezone.utc) + timedelta(hours=horas)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "token": "ya29.prueba",
        "refresh_token": "1//refresco",
        "client_id": "cliente.apps.googleusercontent.com",
        "client_secret": "secreto",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": scopes,
        "expiry": expiry,
    }


class TestExtraerIdCarpeta(unittest.TestCase):
    def test_url_folders(self):
        url = "https://drive.google.com/drive/folders/1AbC_d-9xyz?usp=sharing"
        self.assertEqual(drive.extraer_id_carpeta(url), "1AbC_d-9xyz")

    def test_url_folders_con_usuario(self):
        url = "https://drive.google.com/drive/u/0/folders/1AbC_d-9xyz"
        self.assertEqual(drive.extraer_id_carpeta(url), "1AbC_d-9xyz")

    def test_open_id(self):
        self.assertEqual(drive.extraer_id_carpeta("https://drive.google.com/open?id=1AbC_d-9"), "1AbC_d-9")

    def test_solo_query(self):
        self.assertEqual(drive.extraer_id_carpeta("?id=1AbC_d-9"), "1AbC_d-9")
        self.assertEqual(drive.extraer_id_carpeta("id=1AbC_d-9"), "1AbC_d-9")

    def test_id_suelto(self):
        self.assertEqual(drive.extraer_id_carpeta("1AbC_d-9"), "1AbC_d-9")

    def test_comillas_y_espacios(self):
        self.assertEqual(drive.extraer_id_carpeta('  "1AbC_d-9"  '), "1AbC_d-9")
        self.assertEqual(drive.extraer_id_carpeta(" 'https://drive.google.com/drive/folders/1AbC' "), "1AbC")

    def test_invalidos(self):
        for valor in ("", "   ", None, "https://drive.google.com/", "con espacios adentro",
                      "https://drive.google.com/file/d/xyz/view", "¿qué?"):
            with self.subTest(valor=valor):
                self.assertIsNone(drive.extraer_id_carpeta(valor))


class TestListarHijos(unittest.TestCase):
    def test_pagina_hasta_agotar(self):
        fake = FakeDrive()
        raiz = fake.agregar_carpeta("RAIZ")
        for i in range(2500):
            fake.agregar_archivo(f"a{i}.pdf", raiz)
        hijos = drive.listar_hijos(fake, raiz, page_size=1000)
        self.assertEqual(len(hijos), 2500)
        self.assertEqual(fake.llamadas["list"], 3)
        self.assertEqual(len({h["id"] for h in hijos}), 2500)
        self.assertIn("md5Checksum", hijos[0])
        self.assertIn("size", hijos[0])

    def test_solo_carpetas_y_sin_papelera(self):
        fake = FakeDrive()
        raiz = fake.agregar_carpeta("RAIZ")
        sub = fake.agregar_carpeta("Sub", raiz)
        borrada = fake.agregar_carpeta("Borrada", raiz)
        fake.agregar_archivo("doc.pdf", raiz)
        fake.files().update(fileId=borrada, body={"trashed": True}).execute()
        carpetas = drive.listar_hijos(fake, raiz, solo_carpetas=True)
        self.assertEqual([c["id"] for c in carpetas], [sub])
        todos = drive.listar_hijos(fake, raiz)
        self.assertEqual(len(todos), 2)

    def test_reintenta_ante_500(self):
        fake = FakeDrive()
        raiz = fake.agregar_carpeta("RAIZ")
        fake.agregar_archivo("doc.pdf", raiz)
        fake.fallar("list", status=500, veces=2)
        hijos = drive.listar_hijos(fake, raiz, ejecutar=SIN_ESPERA)
        self.assertEqual(len(hijos), 1)
        self.assertEqual(fake.llamadas["list"], 3)


class TestObtenerCarpeta(unittest.TestCase):
    def test_ok(self):
        fake = FakeDrive()
        raiz = fake.agregar_carpeta("Mi carpeta")
        carpeta = drive.obtener_carpeta(fake, raiz)
        self.assertEqual(carpeta["name"], "Mi carpeta")
        self.assertEqual(carpeta["mimeType"], MIME_FOLDER)

    def test_404(self):
        fake = FakeDrive()
        ctx = "la carpeta origen del lote «X»"
        with self.assertRaises(ErrorFlujo) as cm:
            drive.obtener_carpeta(fake, "no_existe", contexto=ctx)
        self.assertEqual(cm.exception.motivo, f"No se encontró {ctx}.")
        self.assertEqual(cm.exception.paso, "drive")
        self.assertEqual(fake.llamadas["get"], 1)

    def test_403(self):
        fake = FakeDrive()
        raiz = fake.agregar_carpeta("RAIZ")
        fake.fallar("get", status=403)
        with self.assertRaises(ErrorFlujo) as cm:
            drive.obtener_carpeta(fake, raiz, contexto="la carpeta destino del lote «X»")
        self.assertIn("no tiene permiso sobre la carpeta destino del lote «X»", cm.exception.motivo)

    def test_id_de_archivo(self):
        fake = FakeDrive()
        raiz = fake.agregar_carpeta("RAIZ")
        archivo = fake.agregar_archivo("doc.pdf", raiz)
        with self.assertRaises(ErrorFlujo) as cm:
            drive.obtener_carpeta(fake, archivo, contexto="la carpeta origen del lote «X»")
        self.assertIn("no es una carpeta", cm.exception.motivo)
        self.assertEqual(cm.exception.motivo, "La carpeta origen del lote «X» no es una carpeta de Drive.")

    def test_en_papelera(self):
        fake = FakeDrive()
        carpeta_id = fake.agregar_carpeta("Archivada")
        fake.files().update(fileId=carpeta_id, body={"trashed": True}).execute()
        with self.assertRaises(ErrorFlujo) as cm:
            drive.obtener_carpeta(fake, carpeta_id, contexto="la carpeta origen")
        self.assertIn("papelera", cm.exception.motivo)
        self.assertIn("Restáurala", cm.exception.accion)


class TestCrearCarpeta(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDrive()
        self.raiz = self.fake.agregar_carpeta("RAIZ")

    def _subcarpetas(self):
        return [h for h in self.fake.hijos(self.raiz) if h["mimeType"] == MIME_FOLDER]

    def test_crea_una_vez(self):
        carpeta, creada = drive.crear_carpeta(self.fake, "Nueva", self.raiz, dormir=lambda _s: None)
        self.assertTrue(creada)
        self.assertEqual(carpeta["name"], "Nueva")
        self.assertEqual(len(self._subcarpetas()), 1)
        self.assertEqual(self._subcarpetas()[0]["id"], carpeta["id"])
        self.assertEqual(self.fake.llamadas["create"], 1)

    def test_timeout_tras_crear_no_duplica(self):
        self.fake.fallar("create", status=500, aplicar_efecto=True)
        carpeta, creada = drive.crear_carpeta(self.fake, "Nueva", self.raiz, dormir=lambda _s: None)
        self.assertTrue(creada)
        subs = self._subcarpetas()
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["id"], carpeta["id"])
        self.assertEqual(self.fake.llamadas["create"], 1)

    def test_fallo_red_sin_efecto_reintenta(self):
        self.fake.fallar_red("create", veces=2)
        carpeta, creada = drive.crear_carpeta(self.fake, "Nueva", self.raiz, dormir=lambda _s: None)
        self.assertTrue(creada)
        self.assertEqual(len(self._subcarpetas()), 1)
        self.assertEqual(self.fake.llamadas["create"], 3)

    def test_403_relanza_sin_reintentar(self):
        self.fake.fallar("create", status=403)
        with self.assertRaises(HttpError) as cm:
            drive.crear_carpeta(self.fake, "Nueva", self.raiz, dormir=lambda _s: None)
        self.assertEqual(cm.exception.resp.status, 403)
        self.assertEqual(self.fake.llamadas["create"], 1)
        self.assertEqual(self._subcarpetas(), [])

    def test_agota_intentos(self):
        self.fake.fallar("create", status=503, veces=6)
        with self.assertRaises(HttpError):
            drive.crear_carpeta(self.fake, "Nueva", self.raiz, dormir=lambda _s: None)
        self.assertEqual(self.fake.llamadas["create"], 6)


class TestEjecutar(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDrive()
        self.raiz = self.fake.agregar_carpeta("RAIZ")

    def test_reintenta_500(self):
        self.fake.fallar("get", status=500, veces=2)
        esperas = []
        r = drive.ejecutar(self.fake.files().get(fileId=self.raiz), dormir=esperas.append)
        self.assertEqual(r["id"], self.raiz)
        self.assertEqual(self.fake.llamadas["get"], 3)
        self.assertEqual(len(esperas), 2)
        self.assertTrue(all(0 < e <= 90 for e in esperas))

    def test_reintenta_fallo_red(self):
        self.fake.fallar_red("get")
        r = drive.ejecutar(self.fake.files().get(fileId=self.raiz), dormir=lambda _s: None)
        self.assertEqual(r["id"], self.raiz)
        self.assertEqual(self.fake.llamadas["get"], 2)

    def test_no_reintenta_404(self):
        with self.assertRaises(HttpError) as cm:
            drive.ejecutar(self.fake.files().get(fileId="nada"), dormir=lambda _s: None)
        self.assertEqual(cm.exception.resp.status, 404)
        self.assertEqual(self.fake.llamadas["get"], 1)

    def test_agota_intentos(self):
        self.fake.fallar("get", status=503, veces=5)
        with self.assertRaises(HttpError) as cm:
            drive.ejecutar(self.fake.files().get(fileId=self.raiz), max_intentos=3, dormir=lambda _s: None)
        self.assertEqual(cm.exception.resp.status, 503)
        self.assertEqual(self.fake.llamadas["get"], 3)


class TestEsErrorTransitorio(unittest.TestCase):
    def test_transitorios(self):
        for exc in (_http(408), _http(429), _http(500), _http(503), ConnectionError(),
                    TimeoutError(), OSError(), http.client.IncompleteRead(b""),
                    http.client.RemoteDisconnected()):
            with self.subTest(exc=repr(exc)):
                self.assertTrue(drive.es_error_transitorio(exc))

    def test_no_transitorios(self):
        for exc in (_http(400), _http(401), _http(403), _http(404), ValueError(), ErrorFlujo("m")):
            with self.subTest(exc=repr(exc)):
                self.assertFalse(drive.es_error_transitorio(exc))


class TestPapeleraYQuienSoy(unittest.TestCase):
    def test_enviar_a_papelera(self):
        fake = FakeDrive()
        raiz = fake.agregar_carpeta("RAIZ")
        archivo = fake.agregar_archivo("doc.pdf", raiz)
        drive.enviar_a_papelera(fake, archivo)
        self.assertTrue(fake.obtener(archivo)["trashed"])
        self.assertEqual(fake.hijos(raiz), [])

    def test_papelera_ignora_404(self):
        fake = FakeDrive()
        drive.enviar_a_papelera(fake, "no_existe")  # no lanza
        self.assertEqual(fake.llamadas["update"], 1)

    def test_papelera_relanza_403(self):
        fake = FakeDrive()
        raiz = fake.agregar_carpeta("RAIZ")
        fake.fallar("update", status=403)
        with self.assertRaises(HttpError):
            drive.enviar_a_papelera(fake, raiz)

    def test_quien_soy(self):
        self.assertEqual(drive.quien_soy(FakeDrive("alguien@cun.edu.co")), "alguien@cun.edu.co")


class TestToken(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.token = self.dir / "token.json"
        self.credenciales = self.dir / "credentials.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _escribir(self, datos: dict) -> None:
        self.token.write_text(json.dumps(datos), encoding="utf-8")

    def test_estado_token_ausente(self):
        est = drive.estado_token(self.token)
        self.assertFalse(est.existe)
        self.assertFalse(est.valido)
        self.assertFalse(est.scopes_completos)
        self.assertIsNone(est.correo)

    def test_estado_token_scopes_incompletos(self):
        self._escribir(_datos_token(["https://www.googleapis.com/auth/drive"]))
        est = drive.estado_token(self.token)
        self.assertTrue(est.existe)
        self.assertFalse(est.scopes_completos)
        self.assertTrue(est.refrescable)
        self.assertIn("permisos", est.detalle)

    def test_estado_token_vigente_completo(self):
        datos = _datos_token(list(drive.SCOPES))
        datos["account"] = "fabrica@cun.edu.co"
        self._escribir(datos)
        est = drive.estado_token(self.token)
        self.assertEqual((est.existe, est.valido, est.scopes_completos, est.refrescable), (True, True, True, True))
        self.assertEqual(est.correo, "fabrica@cun.edu.co")

    def test_estado_token_ilegible(self):
        self.token.write_text("{esto no es json", encoding="utf-8")
        est = drive.estado_token(self.token)
        self.assertTrue(est.existe)
        self.assertFalse(est.valido)
        self.assertFalse(est.refrescable)

    def test_estado_token_vencido_y_revocado(self):
        self._escribir(_datos_token(list(drive.SCOPES), horas=-2))
        with mock.patch.object(drive.Credentials, "refresh", side_effect=RefreshError("invalid_grant")):
            est = drive.estado_token(self.token)
        self.assertTrue(est.existe)
        self.assertFalse(est.valido)
        self.assertFalse(est.refrescable)
        self.assertIn("autorizar de nuevo", est.detalle)

    def test_cargar_credenciales_sin_token_no_interactivo(self):
        with self.assertRaises(ErrorFlujo) as cm:
            drive.cargar_credenciales(interactivo=False, ruta_token=self.token, ruta_credenciales=self.credenciales)
        self.assertIn("renovar_token.py", cm.exception.accion)
        self.assertEqual(cm.exception.paso, "token")
        self.assertIn("autorizar", cm.exception.motivo)

    def test_cargar_credenciales_scopes_incompletos_no_interactivo(self):
        self._escribir(_datos_token(["https://www.googleapis.com/auth/drive"]))
        with self.assertRaises(ErrorFlujo) as cm:
            drive.cargar_credenciales(interactivo=False, ruta_token=self.token, ruta_credenciales=self.credenciales)
        self.assertIn("renovar_token.py", cm.exception.accion)
        self.assertIn("permisos", cm.exception.detalle)

    def test_cargar_credenciales_token_vigente(self):
        self._escribir(_datos_token(list(drive.SCOPES)))
        creds = drive.cargar_credenciales(interactivo=False, ruta_token=self.token, ruta_credenciales=self.credenciales)
        self.assertTrue(creds.valid)
        self.assertTrue(creds.has_scopes(drive.SCOPES))

    def test_cargar_credenciales_refresca_y_guarda(self):
        self._escribir(_datos_token(list(drive.SCOPES), horas=-2))

        def falso_refresh(creds, request):
            creds.token = "ya29.nuevo"
            creds.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)

        with mock.patch.object(drive.Credentials, "refresh", falso_refresh):
            creds = drive.cargar_credenciales(interactivo=False, ruta_token=self.token, ruta_credenciales=self.credenciales)
        self.assertEqual(creds.token, "ya29.nuevo")
        guardado = json.loads(self.token.read_text(encoding="utf-8"))
        self.assertEqual(guardado["token"], "ya29.nuevo")
        self.assertEqual(set(guardado["scopes"]), set(drive.SCOPES))
        self.assertEqual([p.name for p in self.dir.iterdir()], ["token.json"])  # sin temporales sueltos

    def test_cargar_credenciales_refresh_revocado_no_interactivo(self):
        self._escribir(_datos_token(list(drive.SCOPES), horas=-2))
        with mock.patch.object(drive.Credentials, "refresh", side_effect=RefreshError("invalid_grant")):
            with self.assertRaises(ErrorFlujo) as cm:
                drive.cargar_credenciales(interactivo=False, ruta_token=self.token, ruta_credenciales=self.credenciales)
        self.assertIn("renovar_token.py", cm.exception.accion)

    def test_autorizar_sin_credentials(self):
        with self.assertRaises(ErrorFlujo) as cm:
            drive.autorizar(self.credenciales, self.token)
        self.assertIn("credentials.json", cm.exception.motivo)
        self.assertEqual(cm.exception.paso, "token")
        self.assertFalse(self.token.exists())


class TestFakeDrive(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDrive()
        self.raiz = self.fake.agregar_carpeta("RAIZ")
        self.sub = self.fake.agregar_carpeta("Sub", self.raiz)
        self.pdf = self.fake.agregar_archivo("doc.pdf", self.raiz, contenido=b"pdf")
        self.jpg = self.fake.agregar_archivo("foto.JPG", self.raiz, contenido=b"jpg", mime="image/jpeg")
        self.otro = self.fake.agregar_archivo("otro.pdf", self.sub)

    def _ids(self, q, **kw):
        r = self.fake.files().list(q=q, **kw).execute()
        return {f["id"] for f in r["files"]}

    def test_q_parents_trashed_mime(self):
        q = f"'{self.raiz}'   in   parents AND trashed=false and mimeType = '{MIME_FOLDER}'"
        self.assertEqual(self._ids(q), {self.sub})
        q = f"'{self.raiz}' in parents and trashed = false and mimeType != '{MIME_FOLDER}'"
        self.assertEqual(self._ids(q), {self.pdf, self.jpg})

    def test_q_sin_trashed_incluye_papelera(self):
        self.fake.files().update(fileId=self.pdf, body={"trashed": True}).execute()
        self.assertEqual(self._ids(f"'{self.raiz}' in parents"), {self.sub, self.pdf, self.jpg})
        self.assertEqual(self._ids(f"'{self.raiz}' in parents and trashed = false"), {self.sub, self.jpg})
        self.assertEqual(self._ids(f"'{self.raiz}' in parents and trashed = true"), {self.pdf})

    def test_q_name_con_comilla_escapada(self):
        con_comilla = self.fake.agregar_archivo("it's.pdf", self.raiz)
        self.assertEqual(self._ids(f"'{self.raiz}' in parents and name = 'it\\'s.pdf'"), {con_comilla})
        self.assertEqual(self._ids(f"name = 'doc.pdf' and '{self.raiz}' in parents"), {self.pdf})

    def test_q_desconocida_lanza(self):
        with self.assertRaises(ValueError):
            self.fake.files().list(q="fullText contains 'x'").execute()

    def test_q_padre_inexistente_404(self):
        with self.assertRaises(HttpError) as cm:
            self.fake.files().list(q="'nada' in parents").execute()
        self.assertEqual(cm.exception.resp.status, 404)

    def test_hijos_de_carpeta_en_papelera_salen_trashed(self):
        self.fake.files().update(fileId=self.sub, body={"trashed": True}).execute()
        self.assertEqual(self._ids(f"'{self.sub}' in parents and trashed = false"), set())
        self.assertEqual(self.fake.hijos(self.sub), [])
        self.assertEqual(len(self.fake.hijos(self.sub, incluir_papelera=True)), 1)

    def test_paginacion(self):
        for i in range(25):
            self.fake.agregar_archivo(f"p{i}.pdf", self.sub)
        vistos, token, paginas = [], None, 0
        while True:
            r = self.fake.files().list(q=f"'{self.sub}' in parents", pageSize=10, pageToken=token).execute()
            vistos.extend(f["id"] for f in r["files"])
            paginas += 1
            token = r.get("nextPageToken")
            if not token:
                break
        self.assertEqual(paginas, 3)
        self.assertEqual(len(vistos), 26)
        self.assertEqual(len(set(vistos)), 26)

    def test_fields_recorta(self):
        r = self.fake.files().get(fileId=self.pdf, fields="id, name").execute()
        self.assertEqual(set(r), {"id", "name"})
        r = self.fake.files().get(fileId=self.pdf).execute()
        self.assertEqual(set(r), {"id", "name", "mimeType", "kind"})
        r = self.fake.files().list(q=f"'{self.raiz}' in parents", fields="nextPageToken, files(id, md5Checksum)").execute()
        self.assertTrue(all(set(f) <= {"id", "md5Checksum"} for f in r["files"]))
        r = self.fake.files().get(fileId=self.pdf, fields="*").execute()
        self.assertEqual(r["size"], "3")
        self.assertEqual(r["fileExtension"], "pdf")
        self.assertIn("webViewLink", r)

    def test_get_404(self):
        with self.assertRaises(HttpError) as cm:
            self.fake.files().get(fileId="nada").execute()
        self.assertEqual(cm.exception.resp.status, 404)

    def test_create_y_copy(self):
        nueva = self.fake.files().create(
            body={"name": "Creada", "parents": [self.raiz], "mimeType": MIME_FOLDER}, fields="id, name"
        ).execute()
        self.assertEqual(nueva["name"], "Creada")
        self.assertEqual(self.fake.obtener(nueva["id"])["parents"], [self.raiz])
        copia = self.fake.files().copy(
            fileId=self.jpg, body={"name": "foto.JPG", "parents": [nueva["id"]]}, fields="id"
        ).execute()
        reg = self.fake.obtener(copia["id"])
        self.assertEqual((reg["name"], reg["parents"], reg["mimeType"]), ("foto.JPG", [nueva["id"]], "image/jpeg"))
        self.assertEqual(reg["md5Checksum"], self.fake.obtener(self.jpg)["md5Checksum"])
        self.assertEqual(self.fake.contenido(copia["id"]), b"jpg")
        self.assertEqual(self.fake.files().get_media(fileId=copia["id"]).execute(), b"jpg")

    def test_copy_de_carpeta_falla(self):
        with self.assertRaises(HttpError):
            self.fake.files().copy(fileId=self.sub, body={}).execute()

    def test_fallar_http(self):
        self.fake.fallar("get", status=500, veces=2)
        for _ in range(2):
            with self.assertRaises(HttpError) as cm:
                self.fake.files().get(fileId=self.raiz).execute()
            self.assertEqual(cm.exception.resp.status, 500)
        self.assertEqual(self.fake.files().get(fileId=self.raiz).execute()["id"], self.raiz)
        self.assertEqual(self.fake.llamadas["get"], 3)

    def test_fallar_red(self):
        self.fake.fallar_red("list")
        with self.assertRaises(ConnectionError):
            self.fake.files().list(q=f"'{self.raiz}' in parents").execute()
        self.assertEqual(len(self.fake.files().list(q=f"'{self.raiz}' in parents").execute()["files"]), 3)

    def test_fallar_sin_efecto_no_aplica(self):
        self.fake.fallar("create", status=503)
        with self.assertRaises(HttpError):
            self.fake.files().create(body={"name": "X", "parents": [self.raiz], "mimeType": MIME_FOLDER}).execute()
        self.assertEqual([h["name"] for h in self.fake.hijos(self.raiz) if h["mimeType"] == MIME_FOLDER], ["Sub"])

    def test_fallar_con_efecto_aplica(self):
        self.fake.fallar("copy", status=500, aplicar_efecto=True)
        with self.assertRaises(HttpError):
            self.fake.files().copy(fileId=self.pdf, body={"name": "doc.pdf", "parents": [self.sub]}).execute()
        nombres = sorted(h["name"] for h in self.fake.hijos(self.sub))
        self.assertEqual(nombres, ["doc.pdf", "otro.pdf"])

    def test_fallar_operacion_desconocida(self):
        with self.assertRaises(ValueError):
            self.fake.fallar("borrar")

    def test_about(self):
        r = self.fake.about().get(fields="user(emailAddress)").execute()
        self.assertEqual(r["user"]["emailAddress"], "fabrica.contenidos@cun.edu.co")
        self.assertEqual(self.fake.llamadas["about"], 1)

    def test_obtener_inexistente(self):
        with self.assertRaises(KeyError):
            self.fake.obtener("nada")


if __name__ == "__main__":
    unittest.main()
