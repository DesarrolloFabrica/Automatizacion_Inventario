"""
Pruebas del acceso: lista de autorizados, fichas firmadas y entrada con Google.

Nada sale a la red: el canje del código contra Google se sustituye por una
función que devuelve la identidad que quiera cada prueba.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from servidor import autenticacion
from servidor.app import crear_app
from servidor.autenticacion import AccesoInvalido, ConfiguracionAcceso
from servidor.corridas import Gestor
from tests.test_servidor import ACCESO, configuracion_de_prueba

ANA = {"correo": "ana@cun.edu.co", "nombre": "Ana Gómez"}
INTRUSA = {"correo": "alguien@gmail.com", "nombre": "Alguien"}


def _canje(identidad):
    """Sustituto de la llamada a Google: devuelve siempre esta identidad."""
    return lambda _cfg, _codigo, _retorno: dict(identidad)


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
class TestCargarConfiguracion(unittest.TestCase):
    def test_lee_la_lista_y_normaliza(self):
        cfg = autenticacion.cargar(
            {
                "GOOGLE_OAUTH_CLIENT_ID": "abc",
                "GOOGLE_OAUTH_CLIENT_SECRET": "xyz",
                "SESION_SECRETO": "s",
                "USUARIOS_PERMITIDOS": " Ana@CUN.edu.co , luis@cun.edu.co;eva@cun.edu.co ",
            }
        )
        self.assertEqual(
            cfg.correos_permitidos,
            frozenset({"ana@cun.edu.co", "luis@cun.edu.co", "eva@cun.edu.co"}),
        )
        self.assertTrue(cfg.configurado)

    def test_sin_credenciales_no_esta_configurado(self):
        self.assertFalse(autenticacion.cargar({}).configurado)

    def test_sin_secreto_se_inventa_uno_y_sigue_funcionando(self):
        cfg = autenticacion.cargar(
            {"GOOGLE_OAUTH_CLIENT_ID": "abc", "GOOGLE_OAUTH_CLIENT_SECRET": "xyz"}
        )
        self.assertTrue(cfg.secreto_sesion)
        self.assertTrue(cfg.configurado)

    def test_la_cookie_es_segura_salvo_que_se_apague(self):
        self.assertTrue(autenticacion.cargar({}).cookie_segura)
        self.assertFalse(autenticacion.cargar({"COOKIE_SEGURA": "0"}).cookie_segura)

    def test_horas_de_sesion_fuera_de_rango_se_recortan(self):
        self.assertEqual(autenticacion.cargar({"HORAS_SESION": "0"}).horas_sesion, 1)
        self.assertEqual(autenticacion.cargar({"HORAS_SESION": "9999"}).horas_sesion, 168)
        self.assertEqual(autenticacion.cargar({"HORAS_SESION": "ocho"}).horas_sesion, 8)

    def test_permitido_no_distingue_mayusculas_ni_espacios(self):
        self.assertTrue(autenticacion.permitido(ACCESO, "  ANA@cun.edu.co "))
        self.assertFalse(autenticacion.permitido(ACCESO, "otra@cun.edu.co"))


# ---------------------------------------------------------------------------
# Fichas firmadas
# ---------------------------------------------------------------------------
class TestFichas(unittest.TestCase):
    def test_ida_y_vuelta(self):
        ficha = autenticacion.firmar("semilla", {"correo": "ana@cun.edu.co"}, segundos=60)
        self.assertEqual(
            autenticacion.verificar("semilla", ficha)["correo"], "ana@cun.edu.co"
        )

    def test_otra_semilla_no_vale(self):
        ficha = autenticacion.firmar("semilla", {"a": 1}, segundos=60)
        self.assertIsNone(autenticacion.verificar("otra", ficha))

    def test_contenido_manipulado_no_vale(self):
        ficha = autenticacion.firmar("semilla", {"correo": "ana@cun.edu.co"}, segundos=60)
        crudo, _, firma = ficha.partition(".")
        falsa = autenticacion.firmar("inventada", {"correo": "jefe@cun.edu.co"}, segundos=60)
        self.assertIsNone(autenticacion.verificar("semilla", falsa.split(".")[0] + "." + firma))
        self.assertIsNone(autenticacion.verificar("semilla", crudo + ".abc"))

    def test_caducada_no_vale(self):
        ficha = autenticacion.firmar("semilla", {"a": 1}, segundos=1)
        despues = time.time() + 10
        with mock.patch.object(autenticacion.time, "time", lambda: despues):
            self.assertIsNone(autenticacion.verificar("semilla", ficha))

    def test_basura_no_revienta(self):
        for valor in (None, "", "sin-punto", "a.b", "...."):
            self.assertIsNone(autenticacion.verificar("semilla", valor))

    def test_quitar_a_alguien_de_la_lista_invalida_su_sesion(self):
        ficha = autenticacion.crear_sesion(ACCESO, "ana@cun.edu.co", "Ana")
        self.assertIsNotNone(autenticacion.leer_sesion(ACCESO, ficha))
        sin_ana = ConfiguracionAcceso(**{**ACCESO.__dict__, "correos_permitidos": frozenset()})
        self.assertIsNone(autenticacion.leer_sesion(sin_ana, ficha))


class TestDireccionDeRetorno(unittest.TestCase):
    def test_prefiere_la_url_publica(self):
        cfg = ConfiguracionAcceso(**{**ACCESO.__dict__, "url_publica": "https://fabrica.run.app"})
        self.assertEqual(
            autenticacion.direccion_de_retorno(cfg, "http://interno/"),
            "https://fabrica.run.app/api/sesion/callback",
        )

    def test_sin_url_publica_usa_la_de_la_peticion(self):
        self.assertEqual(
            autenticacion.direccion_de_retorno(ACCESO, "http://localhost:8080/"),
            "http://localhost:8080/api/sesion/callback",
        )

    def test_la_url_de_google_lleva_el_cliente_y_el_estado(self):
        url = autenticacion.url_autorizacion(ACCESO, "http://x/cb", "ESTADO")
        self.assertIn(ACCESO.client_id, url)
        self.assertIn("state=ESTADO", url)
        self.assertIn("response_type=code", url)


# ---------------------------------------------------------------------------
# El acceso visto desde la API
# ---------------------------------------------------------------------------
class BaseWeb(unittest.TestCase):
    def crear_cliente(self, *, canjear=None, acceso=ACCESO) -> TestClient:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = configuracion_de_prueba(Path(tmp.name))
        gestor = Gestor(cfg, correr=lambda _t: None, automatico=False)
        self.addCleanup(gestor.detener)
        app = crear_app(cfg, gestor, acceso=acceso, canjear=canjear or _canje(ANA))
        # Las redirecciones se miran una a una: son la mitad de lo que se prueba.
        return TestClient(app, follow_redirects=False)

    def entrar(self, cliente: TestClient):
        """Entra de verdad, por la vuelta de Google: así la cookie es la real."""
        estado = autenticacion.firmar(ACCESO.secreto_sesion, {"destino": "/"}, segundos=60)
        respuesta = cliente.get("/api/sesion/callback", params={"code": "ok", "state": estado})
        self.assertEqual(respuesta.status_code, 303, respuesta.text)


class TestPuertaCerrada(BaseWeb):
    def test_la_api_sin_sesion_responde_401_con_que_hacer(self):
        r = self.crear_cliente().get("/api/salud")
        self.assertEqual(r.status_code, 401)
        self.assertIn("accion", r.json())

    def test_lanzar_una_corrida_sin_sesion_no_llega_al_gestor(self):
        r = self.crear_cliente().post("/api/corridas", json={"origen": "x", "destino": "y"})
        self.assertEqual(r.status_code, 401)

    def test_la_pagina_sin_sesion_manda_a_la_entrada(self):
        r = self.crear_cliente().get("/")
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/login?destino=%2F")

    def test_la_pantalla_de_entrada_y_sus_estaticos_son_publicos(self):
        cliente = self.crear_cliente()
        for ruta in ("/login", "/login.js", "/estilos.css"):
            self.assertEqual(cliente.get(ruta).status_code, 200, ruta)

    def test_una_cookie_inventada_no_abre_la_puerta(self):
        cliente = self.crear_cliente()
        cliente.cookies.set(autenticacion.COOKIE, "yo.lafirmo")
        self.assertEqual(cliente.get("/api/salud").status_code, 401)

    def test_sin_configurar_lo_dice_en_vez_de_dejar_entrar(self):
        a_medias = ConfiguracionAcceso(**{**ACCESO.__dict__, "client_secret": ""})
        r = self.crear_cliente(acceso=a_medias).get("/api/salud")
        self.assertEqual(r.status_code, 503)
        self.assertIn("GOOGLE_OAUTH_CLIENT_ID", r.json()["accion"])


class TestEntrar(BaseWeb):
    def test_iniciar_lleva_a_google(self):
        r = self.crear_cliente().get("/api/sesion/iniciar")
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith(autenticacion.AUTORIZACION))

    def test_la_vuelta_de_google_deja_la_cookie_y_lleva_al_destino(self):
        cliente = self.crear_cliente()
        estado = autenticacion.firmar(
            ACCESO.secreto_sesion, {"destino": "/index.html"}, segundos=60
        )
        r = cliente.get("/api/sesion/callback", params={"code": "ok", "state": estado})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/index.html")
        self.assertEqual(cliente.get("/api/sesion").json()["correo"], "ana@cun.edu.co")

    def test_quien_no_esta_en_la_lista_no_entra(self):
        cliente = self.crear_cliente(canjear=_canje(INTRUSA))
        estado = autenticacion.firmar(ACCESO.secreto_sesion, {"destino": "/"}, segundos=60)
        r = cliente.get("/api/sesion/callback", params={"code": "ok", "state": estado})
        self.assertEqual(r.status_code, 303)
        self.assertIn("error=sin_permiso", r.headers["location"])
        self.assertNotIn(autenticacion.COOKIE, cliente.cookies)
        self.assertEqual(cliente.get("/api/salud").status_code, 401)

    def test_sin_estado_valido_no_se_acepta_la_vuelta(self):
        # Es la defensa contra que alguien provoque la entrada desde otra web.
        r = self.crear_cliente().get("/api/sesion/callback", params={"code": "ok", "state": "nop"})
        self.assertEqual(r.status_code, 303)
        self.assertIn("error=caducado", r.headers["location"])

    def test_si_google_falla_se_explica_en_la_pantalla_de_entrada(self):
        def explota(*_a):
            raise AccesoInvalido("código ya usado")

        estado = autenticacion.firmar(ACCESO.secreto_sesion, {"destino": "/"}, segundos=60)
        r = self.crear_cliente(canjear=explota).get(
            "/api/sesion/callback", params={"code": "ok", "state": estado}
        )
        self.assertIn("error=fallo", r.headers["location"])

    def test_si_la_persona_cancela_en_google_se_vuelve_sin_drama(self):
        r = self.crear_cliente().get("/api/sesion/callback", params={"error": "access_denied"})
        self.assertIn("error=cancelado", r.headers["location"])

    def test_un_destino_externo_se_ignora(self):
        # El destino viaja firmado, pero además solo se admiten rutas internas:
        # «//sitio.malo» también empieza por barra y sería otra web.
        cliente = self.crear_cliente()
        for fuera in ("https://sitio.malo/", "//sitio.malo/", "sitio.malo"):
            r = cliente.get("/api/sesion/iniciar", params={"destino": fuera})
            estado = r.headers["location"].split("state=")[1].split("&")[0]
            self.assertEqual(
                autenticacion.verificar(ACCESO.secreto_sesion, estado)["destino"], "/", fuera
            )


class TestSalir(BaseWeb):
    def test_cerrar_borra_la_cookie_y_vuelve_a_la_entrada(self):
        cliente = self.crear_cliente()
        self.entrar(cliente)
        r = cliente.get("/api/sesion/cerrar")
        self.assertEqual(r.status_code, 303)
        self.assertIn("/login", r.headers["location"])
        self.assertEqual(cliente.get("/api/salud").status_code, 401)

    def test_sesion_dice_quien_esta_dentro(self):
        cliente = self.crear_cliente()
        self.entrar(cliente)
        self.assertEqual(
            cliente.get("/api/sesion").json(), {"correo": "ana@cun.edu.co", "nombre": "Ana Gómez"}
        )


if __name__ == "__main__":
    unittest.main()
