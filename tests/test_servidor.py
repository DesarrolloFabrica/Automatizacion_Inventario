"""
Pruebas del servicio web.

Nada sale a la red: Google se sustituye por el Drive simulado y la base de
datos por los dobles de siempre. La última clase recorre el camino completo
(petición HTTP -> cola -> run_flujo -> flujo_lib -> Drive simulado) para
comprobar que la página recibiría de verdad el avance paso a paso.
"""

from __future__ import annotations

import os, tempfile, threading, time, unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from flujo_lib.mensajes import ErrorFlujo
from servidor.app import crear_app
from servidor.configuracion import Configuracion, cargar
from servidor.corridas import Gestor, construir_lote
from tests.fake_drive import FakeDrive
from tests.test_prevalidacion import BaseFalsa

ORIGEN = "origenBogota01"
RAIZ = "raizLmsCarga01"
URL_ORIGEN = f"https://drive.google.com/drive/folders/{ORIGEN}"
URL_RAIZ = f"https://drive.google.com/drive/u/5/folders/{RAIZ}?usp=sharing"
CREDS = object()
ENV_OK = {
    "CORREOS_AVISO": "ana@cun.edu.co",
    "DB_HOST": "10.0.0.5", "DB_PORT": "5432", "DB_NAME": "planner_db",
    "DB_USER": "fabrica", "DB_PASSWORD": "secreto",
}


def configuracion_de_prueba(carpeta: Path, **kw) -> Configuracion:
    base = dict(
        schema="fabrica_pruebas", almacen="", dir_corridas=carpeta,
        simular_por_defecto=True, forzar_carga_por_defecto=False,
        modo_credenciales="token", ruta_token=carpeta / "token.json",
        ruta_cuenta_servicio=None, usuario_suplantado="",
    )
    base.update(kw)
    return Configuracion(**base)


# ---------------------------------------------------------------------------
# Armado del lote desde el formulario
# ---------------------------------------------------------------------------
class TestConstruirLote(unittest.TestCase):
    def test_enlaces_normales(self):
        lote = construir_lote(URL_ORIGEN, URL_RAIZ, "Derecho", "")
        self.assertEqual(lote.origen_id, ORIGEN)
        self.assertEqual(lote.destino_raiz_id, RAIZ)
        self.assertEqual(lote.etiqueta, "Derecho")
        self.assertTrue(lote.sin_clasificar)  # se deducirá de Drive

    def test_identificadores_sueltos(self):
        lote = construir_lote(ORIGEN, RAIZ, "", "")
        self.assertEqual((lote.origen_id, lote.destino_raiz_id), (ORIGEN, RAIZ))
        self.assertEqual(lote.etiqueta, "Lote sin nombre")

    def test_cliente_escrito(self):
        lote = construir_lote(ORIGEN, RAIZ, "x", "LMS_correcciones")
        self.assertEqual(lote.clasificacion, "LMS_CORRECCIONES")
        self.assertEqual(lote.cliente_gcp, "PRODUCTO")
        self.assertEqual(lote.raiz_gcp, "LMS_Carga")
        self.assertFalse(lote.sin_clasificar)

    def test_origen_invalido(self):
        with self.assertRaises(ErrorFlujo) as cm:
            construir_lote("carpeta de sarita", RAIZ, "", "")
        self.assertIn("origen", cm.exception.motivo)
        self.assertIn("Google Drive", cm.exception.accion)

    def test_destino_invalido(self):
        with self.assertRaises(ErrorFlujo) as cm:
            construir_lote(ORIGEN, "?????", "", "")
        self.assertIn("destino", cm.exception.motivo)

    def test_origen_igual_a_destino(self):
        with self.assertRaises(ErrorFlujo) as cm:
            construir_lote(ORIGEN, ORIGEN, "", "")
        self.assertIn("misma carpeta", cm.exception.motivo)

    def test_cliente_inexistente(self):
        with self.assertRaises(ErrorFlujo) as cm:
            construir_lote(ORIGEN, RAIZ, "", "MARKETING")
        self.assertIn("no existe", cm.exception.motivo)


# ---------------------------------------------------------------------------
# Cola y estados del gestor
# ---------------------------------------------------------------------------
class TestGestor(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.corridas: list = []
        self.cfg = configuracion_de_prueba(self.dir)

    def gestor(self, correr=None) -> Gestor:
        g = Gestor(self.cfg, correr=correr or self.corridas.append, automatico=False)
        self.addCleanup(g.detener)
        return g

    def test_lanzar_encola_y_devuelve_id(self):
        g = self.gestor()
        trabajo = g.lanzar(origen=URL_ORIGEN, destino=URL_RAIZ, etiqueta="Derecho")
        self.assertEqual(trabajo.estado_general, "en_cola")
        self.assertTrue(trabajo.id)
        self.assertIs(g.obtener(trabajo.id), trabajo)
        self.assertTrue(trabajo.simular)  # el valor por defecto de la configuración

    def test_no_deja_dos_corridas_para_el_mismo_origen(self):
        g = self.gestor()
        g.lanzar(origen=URL_ORIGEN, destino=URL_RAIZ)
        with self.assertRaises(ErrorFlujo) as cm:
            g.lanzar(origen=ORIGEN, destino=RAIZ)  # mismo origen, escrito distinto
        self.assertIn("en marcha", cm.exception.motivo)

    def test_otro_origen_si_se_puede_encolar(self):
        g = self.gestor()
        g.lanzar(origen=URL_ORIGEN, destino=URL_RAIZ)
        otro = g.lanzar(origen="otroOrigen99", destino=RAIZ)
        self.assertEqual(otro.estado_general, "en_cola")
        self.assertEqual(len(g.listar()), 2)

    def test_una_corrida_acepta_varias_parejas_origen_destino(self):
        g = self.gestor()
        lotes = [
            construir_lote(ORIGEN, RAIZ, "Carpetas", "", fila=1),
            construir_lote("otroOrigen99", "otraRaizDestino99", "Carpetas", "", fila=2),
        ]
        trabajo = g.lanzar(lotes=lotes)

        self.assertEqual(trabajo.lotes, lotes)
        self.assertEqual(len(g.ver(trabajo.id)["lotes"]), 2)

    def test_no_deja_repetir_un_origen_dentro_de_la_misma_corrida(self):
        g = self.gestor()
        lotes = [
            construir_lote(ORIGEN, RAIZ, "Carpetas", ""),
            construir_lote(ORIGEN, "otraRaizDestino99", "Carpetas", ""),
        ]
        with self.assertRaises(ErrorFlujo) as cm:
            g.lanzar(lotes=lotes)
        self.assertIn("más de una vez", cm.exception.motivo)

    def test_terminada_libera_el_origen(self):
        g = self.gestor()
        primera = g.lanzar(origen=URL_ORIGEN, destino=URL_RAIZ)
        primera.estado_general = "ok"
        segunda = g.lanzar(origen=URL_ORIGEN, destino=URL_RAIZ)
        self.assertNotEqual(primera.id, segunda.id)

    def test_cancelar_antes_de_empezar(self):
        g = self.gestor()
        trabajo = g.lanzar(origen=URL_ORIGEN, destino=URL_RAIZ)
        self.assertTrue(g.cancelar(trabajo.id))
        self.assertEqual(trabajo.estado_general, "cancelada")
        self.assertFalse(g.cancelar(trabajo.id))  # ya terminó

    def test_ver_devuelve_la_forma_que_espera_la_pagina(self):
        g = self.gestor()
        trabajo = g.lanzar(origen=URL_ORIGEN, destino=URL_RAIZ, etiqueta="Derecho")
        datos = g.ver(trabajo.id)
        self.assertEqual(
            set(datos),
            {"id", "estado", "inicio", "fin", "simular", "esquema",
             "prevalidacion", "lotes", "mensajes", "resumen", "enlaces"},
        )
        paso = datos["lotes"][0]["pasos"]["clonacion"]
        self.assertEqual(paso["estado"], "pendiente")
        self.assertEqual(set(paso["progreso"]), {"hechos", "total", "mensaje", "porcentaje"})

    def test_procesar_marca_inicio_fin_y_recoge_el_log(self):
        import logging

        def correr(trabajo):
            logging.getLogger().info("copiando algo")
            logging.getLogger().warning("ojo con esto")
            trabajo.estado_general = "ok"

        g = self.gestor(correr=correr)
        trabajo = g.lanzar(origen=URL_ORIGEN, destino=URL_RAIZ)
        g.procesar(trabajo.id)

        self.assertEqual(trabajo.estado_general, "ok")
        self.assertTrue(trabajo.inicio and trabajo.fin)
        niveles = {m["nivel"] for m in trabajo.mensajes}
        textos = [m["texto"] for m in trabajo.mensajes]
        self.assertIn("copiando algo", textos)
        self.assertIn("aviso", niveles)

    def test_un_fallo_no_tumba_el_hilo_y_queda_explicado(self):
        def correr(_trabajo):
            raise PermissionError("archivo bloqueado")

        g = self.gestor(correr=correr)
        trabajo = g.lanzar(origen=URL_ORIGEN, destino=URL_RAIZ)
        g.procesar(trabajo.id)
        self.assertEqual(trabajo.estado_general, "fallido")
        self.assertIn("abierto o protegido", trabajo.resumen["motivo"])

    def test_el_hilo_de_fondo_toma_los_trabajos(self):
        hecho = threading.Event()

        def correr(trabajo):
            trabajo.estado_general = "ok"
            hecho.set()

        g = Gestor(self.cfg, correr=correr, automatico=True)
        self.addCleanup(g.detener)
        g.lanzar(origen=URL_ORIGEN, destino=URL_RAIZ)
        self.assertTrue(hecho.wait(timeout=5), "el hilo no procesó la corrida")


# ---------------------------------------------------------------------------
# La API
# ---------------------------------------------------------------------------
class TestApi(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.cfg = configuracion_de_prueba(self.dir)
        self.gestor = Gestor(self.cfg, correr=lambda _t: None, automatico=False)
        self.addCleanup(self.gestor.detener)
        self.cliente = TestClient(crear_app(self.cfg, self.gestor))

    def test_lanzar_devuelve_201_y_el_id(self):
        r = self.cliente.post("/api/corridas", json={"origen": URL_ORIGEN, "destino": URL_RAIZ,
                                                     "etiqueta": "Derecho", "simular": True})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertTrue(r.json()["corrida_id"])

    def test_enlace_malo_devuelve_400_con_que_hacer(self):
        r = self.cliente.post("/api/corridas", json={"origen": "no sirve", "destino": URL_RAIZ})
        self.assertEqual(r.status_code, 400)
        self.assertIn("origen", r.json()["motivo"])
        self.assertTrue(r.json()["accion"])

    def test_varias_filas_se_convierten_en_lotes_y_las_vacias_se_ignoran(self):
        r = self.cliente.post(
            "/api/corridas",
            json={
                "lotes": [
                    {"origen": URL_ORIGEN, "destino": URL_RAIZ},
                    {"origen": "", "destino": ""},
                    {"origen": "otroOrigen99", "destino": "otraRaizDestino99"},
                ],
                "etiqueta": "Carpetas del día",
                "cliente": "",
                "simular": False,
            },
        )

        self.assertEqual(r.status_code, 201, r.text)
        trabajo = self.gestor.obtener(r.json()["corrida_id"])
        self.assertEqual(len(trabajo.lotes), 2)
        self.assertFalse(trabajo.simular)

    def test_fila_a_medio_llenar_explica_que_falta(self):
        r = self.cliente.post(
            "/api/corridas",
            json={"lotes": [{"origen": URL_ORIGEN, "destino": ""}]},
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("destino", r.json()["motivo"])
        self.assertIn("dos enlaces", r.json()["accion"])

    def test_todas_las_filas_vacias_no_crean_corrida(self):
        r = self.cliente.post(
            "/api/corridas",
            json={"lotes": [{"origen": "", "destino": ""}]},
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("No hay carpetas", r.json()["motivo"])

    def test_dos_veces_el_mismo_origen_devuelve_409(self):
        self.cliente.post("/api/corridas", json={"origen": URL_ORIGEN, "destino": URL_RAIZ})
        r = self.cliente.post("/api/corridas", json={"origen": URL_ORIGEN, "destino": URL_RAIZ})
        self.assertEqual(r.status_code, 409)
        self.assertIn("en marcha", r.json()["motivo"])

    def test_corrida_inexistente_devuelve_404(self):
        self.assertEqual(self.cliente.get("/api/corridas/nada").status_code, 404)
        self.assertEqual(self.cliente.post("/api/corridas/nada/cancelar").status_code, 404)

    def test_listar_y_ver(self):
        creada = self.cliente.post(
            "/api/corridas", json={"origen": URL_ORIGEN, "destino": URL_RAIZ, "etiqueta": "Derecho"}
        ).json()["corrida_id"]
        listado = self.cliente.get("/api/corridas").json()["corridas"]
        self.assertEqual(listado[0]["id"], creada)
        self.assertEqual(listado[0]["etiqueta"], "Derecho")
        self.assertEqual(self.cliente.get(f"/api/corridas/{creada}").json()["id"], creada)

    def test_cancelar(self):
        creada = self.cliente.post(
            "/api/corridas", json={"origen": URL_ORIGEN, "destino": URL_RAIZ}
        ).json()["corrida_id"]
        r = self.cliente.post(f"/api/corridas/{creada}/cancelar")
        self.assertEqual(r.status_code, 202)
        self.assertTrue(r.json()["cancelando"])

    def test_salud_avisa_del_esquema(self):
        with mock.patch("servidor.configuracion.cargar_credenciales", side_effect=OSError("sin token")):
            datos = self.cliente.get("/api/salud").json()
        self.assertEqual(datos["esquema"], "fabrica_pruebas")
        self.assertFalse(datos["produccion"])
        self.assertIsNone(datos["cuenta"])

    def test_la_pagina_se_sirve(self):
        inicio = self.cliente.get("/")
        self.assertEqual(inicio.status_code, 200)
        self.assertIn("text/html", inicio.headers["content-type"])
        self.assertEqual(inicio.headers["cache-control"], "no-store, max-age=0")
        self.assertIn("app.js?v=20260923-3", inicio.text)
        self.assertIn("estilos.css?v=20260923-3", inicio.text)
        for archivo in ("estilos.css", "app.js"):
            respuesta = self.cliente.get(f"/{archivo}")
            self.assertEqual(respuesta.status_code, 200, archivo)
            self.assertEqual(respuesta.headers["cache-control"], "no-store, max-age=0")


# ---------------------------------------------------------------------------
# Camino completo: petición HTTP -> corrida real sobre el Drive simulado
# ---------------------------------------------------------------------------
class TestExtremoAExtremo(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

        self.fake = FakeDrive()
        # Jerarquía real: el cliente cuelga de las carpetas padre del origen.
        padre = self.fake.agregar_carpeta("PRODUCTO_FINAL")
        q2 = self.fake.agregar_carpeta("Q2", padre)
        producto = self.fake.agregar_carpeta("PRODUCTO", q2)
        escuela = self.fake.agregar_carpeta("ESCUELA_DERECHO", producto)
        self.fake.agregar_carpeta("DERECHO", parent_id=escuela, id=ORIGEN)
        sem = self.fake.agregar_carpeta("SEMESTRE I", ORIGEN)
        paq = self.fake.agregar_carpeta("NOTEBOOK", sem)
        materia = self.fake.agregar_carpeta("01. CIVIL", paq)
        for nombre in ("G1001_guia.pdf", "G1002_taller.pdf", "G1003_mapa.pdf"):
            self.fake.agregar_archivo(nombre, materia, contenido=nombre.encode())
        self.fake.agregar_carpeta("prueba", id=RAIZ)

        parches = [
            mock.patch("servidor.configuracion.cargar_credenciales", return_value=CREDS),
            mock.patch("flujo_lib.prevalidacion.drive.construir_servicio", lambda _c: self.fake),
            mock.patch("flujo_lib.prevalidacion._conectar_psycopg2", BaseFalsa(esquemas=("fabrica_pruebas",))),
            mock.patch("run_flujo.publicar_inventario", return_value="https://docs.google.com/sheet"),
            mock.patch("flujo_lib.clonacion.time.sleep", lambda *_a: None),
            mock.patch.dict(os.environ, ENV_OK),
        ]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)

        self.cfg = configuracion_de_prueba(self.dir)
        self.gestor = Gestor(self.cfg, automatico=False)
        self.addCleanup(self.gestor.detener)
        self.cliente = TestClient(crear_app(self.cfg, self.gestor))

    def test_una_corrida_completa_desde_la_api(self):
        creada = self.cliente.post("/api/corridas", json={
            "origen": URL_ORIGEN, "destino": URL_RAIZ,
            "etiqueta": "Derecho civil", "simular": True,
        })
        self.assertEqual(creada.status_code, 201, creada.text)
        corrida_id = creada.json()["corrida_id"]

        self.gestor.procesar(corrida_id)  # sin hilo, para que la prueba sea determinista
        datos = self.cliente.get(f"/api/corridas/{corrida_id}").json()

        self.assertIn(datos["estado"], {"ok", "con_pendientes"}, datos.get("resumen"))
        lote = datos["lotes"][0]
        self.assertEqual(lote["etiqueta"], "Derecho civil")

        # El cliente se dedujo de Drive sin que nadie lo escribiera.
        detectado = [h for h in datos["prevalidacion"] if h["area"] == "cliente"]
        self.assertTrue(detectado)
        self.assertIn("PRODUCTO", detectado[0]["mensaje"])

        # Se creó la carpeta con el nombre del origen dentro de la raíz.
        clones = [h for h in self.fake.hijos(RAIZ) if h["name"] == "DERECHO"]
        self.assertEqual(len(clones), 1)
        self.assertEqual(lote["destino_nombre"], "DERECHO")
        self.assertTrue(lote["destino_enlace"].endswith(clones[0]["id"]))

        # Los pasos de Drive quedaron bien y la clonación reporta su avance.
        self.assertEqual(lote["pasos"]["destino"]["estado"], "ok")
        self.assertEqual(lote["pasos"]["clonacion"]["estado"], "ok")
        progreso = lote["pasos"]["clonacion"]["progreso"]
        self.assertEqual(progreso["total"], 3)
        self.assertEqual(progreso["hechos"], 3)
        self.assertEqual(progreso["porcentaje"], 100)

        # Y la página recibe mensajes para pintar la consola.
        self.assertTrue(datos["mensajes"])
        self.assertTrue(any("DERECHO" in m["texto"] for m in datos["mensajes"]))

    def test_simular_no_toca_la_base_y_lo_dice(self):
        corrida_id = self.cliente.post("/api/corridas", json={
            "origen": URL_ORIGEN, "destino": URL_RAIZ, "simular": True,
        }).json()["corrida_id"]
        self.gestor.procesar(corrida_id)
        datos = self.cliente.get(f"/api/corridas/{corrida_id}").json()
        carga = datos["lotes"][0]["pasos"]["carga"]
        if carga["estado"] == "ok":
            self.assertIn("Simulación", carga["detalle"])

    def test_cancelar_detiene_la_corrida_entre_pasos(self):
        """Lo ya hecho se conserva; lo que faltaba queda marcado como omitido."""
        corrida_id = self.cliente.post("/api/corridas", json={
            "origen": URL_ORIGEN, "destino": URL_RAIZ, "simular": True}).json()["corrida_id"]
        self.assertTrue(self.cliente.post(f"/api/corridas/{corrida_id}/cancelar").json()["cancelando"])

        # Al cancelar antes de empezar queda cancelada de una vez, sin tocar Drive.
        datos = self.cliente.get(f"/api/corridas/{corrida_id}").json()
        self.assertEqual(datos["estado"], "cancelada")
        self.assertEqual([h for h in self.fake.hijos(RAIZ) if h["name"] == "DERECHO"], [])

    def test_cancelar_a_mitad_conserva_lo_hecho(self):
        corrida_id = self.cliente.post("/api/corridas", json={
            "origen": URL_ORIGEN, "destino": URL_RAIZ, "simular": True}).json()["corrida_id"]
        trabajo = self.gestor.obtener(corrida_id)
        trabajo.cancelacion.set()          # como si se pulsara justo al arrancar
        trabajo.estado_general = "en_cola"  # se deshace el atajo de cancelar en cola
        self.gestor.procesar(corrida_id)

        datos = self.cliente.get(f"/api/corridas/{corrida_id}").json()
        self.assertEqual(datos["estado"], "cancelada")
        pasos = datos["lotes"][0]["pasos"]
        self.assertEqual(pasos["destino"]["estado"], "ok")  # lo hecho se conserva
        self.assertEqual(pasos["clonacion"]["estado"], "omitido")
        self.assertIn("canceló", pasos["clonacion"]["detalle"])

    def test_segunda_corrida_reutiliza_el_clon(self):
        primero = self.cliente.post("/api/corridas", json={
            "origen": URL_ORIGEN, "destino": URL_RAIZ, "simular": True}).json()["corrida_id"]
        self.gestor.procesar(primero)
        copias_tras_la_primera = self.fake.llamadas["copy"]

        segundo = self.cliente.post("/api/corridas", json={
            "origen": URL_ORIGEN, "destino": URL_RAIZ, "simular": True}).json()["corrida_id"]
        self.gestor.procesar(segundo)

        # Es otra corrida (estado propio), así que vuelve a recorrer, pero no copia de nuevo.
        self.assertEqual(self.fake.llamadas["copy"], copias_tras_la_primera)
        clones = [h for h in self.fake.hijos(RAIZ) if h["name"] == "DERECHO"]
        self.assertEqual(len(clones), 1)


if __name__ == "__main__":
    unittest.main()
