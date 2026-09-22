"""
Pruebas de flujo_lib.progreso y de su conexión con los pasos que tardan.

Ninguna toca Google, la base de datos ni la red: se usan el FakeDrive compartido
y los dobles de tests/test_gcp.py.
"""

from __future__ import annotations

import io, threading, types, unittest
from types import SimpleNamespace
from unittest import mock

from PIL import Image

from flujo_lib import drive, gcp
from flujo_lib.clonacion import clonar_arbol
from flujo_lib.formato import convertir_arbol
from flujo_lib.progreso import Avance, Reporte
from flujo_lib.verificacion import verificar_lote
from tests.fake_drive import FakeDrive
from tests.test_gcp import FILA, Conexion, heredado

SILENCIO = lambda *_a, **_k: None  # noqa: E731
CLAVE = "ORIG1|RAIZ1"


def _jpg() -> bytes:
    salida = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(salida, "JPEG")
    return salida.getvalue()


class Espia:
    """Destino de avance: guarda cada (hechos, total, mensaje) que le llega."""

    def __init__(self):
        self.llamadas: list[tuple[int, int, str]] = []

    def __call__(self, hechos: int, total: int, mensaje: str) -> None:
        self.llamadas.append((hechos, total, mensaje))

    @property
    def ultima(self) -> tuple[int, int, str]:
        return self.llamadas[-1]


class TestAvance(unittest.TestCase):
    def test_porcentaje_sin_total_es_cero(self):
        self.assertEqual(Avance().porcentaje(), 0)
        self.assertEqual(Avance(hechos=7, total=0).porcentaje(), 0)
        self.assertEqual(Avance(hechos=7, total=-3).porcentaje(), 0)

    def test_porcentaje_normal(self):
        self.assertEqual(Avance(0, 8).porcentaje(), 0)
        self.assertEqual(Avance(1, 8).porcentaje(), 12)  # se trunca, no se redondea
        self.assertEqual(Avance(4, 8).porcentaje(), 50)
        self.assertEqual(Avance(8, 8).porcentaje(), 100)

    def test_porcentaje_nunca_se_pasa_de_100_ni_baja_de_0(self):
        self.assertEqual(Avance(20, 8).porcentaje(), 100)
        self.assertEqual(Avance(-5, 8).porcentaje(), 0)

    def test_como_dict(self):
        self.assertEqual(
            Avance(3, 4, "pieza_01.png").como_dict(),
            {"hechos": 3, "total": 4, "mensaje": "pieza_01.png", "porcentaje": 75},
        )


class TestReporte(unittest.TestCase):
    def setUp(self):
        self.recibido: list[tuple[str, str, Avance]] = []
        self.reporte = Reporte(destino=lambda c, p, a: self.recibido.append((c, p, a)))

    def test_sin_destino_no_falla_y_recuerda(self):
        reporte = Reporte()
        reporte.iniciar(CLAVE, "clonacion", total=3, mensaje="Copiando")
        reporte.paso_a_paso(CLAVE, "clonacion", "uno.pdf")
        self.assertEqual(reporte.actual(CLAVE, "clonacion"), Avance(1, 3, "uno.pdf"))

    def test_iniciar_paso_a_paso_y_terminar(self):
        self.reporte.iniciar(CLAVE, "clonacion", total=2, mensaje="Copiando")
        self.assertEqual(self.reporte.actual(CLAVE, "clonacion"), Avance(0, 2, "Copiando"))
        self.reporte.paso_a_paso(CLAVE, "clonacion", "uno.pdf")
        self.assertEqual(self.reporte.actual(CLAVE, "clonacion"), Avance(1, 2, "uno.pdf"))
        self.reporte.paso_a_paso(CLAVE, "clonacion")  # sin mensaje: conserva el anterior
        self.assertEqual(self.reporte.actual(CLAVE, "clonacion"), Avance(2, 2, "uno.pdf"))
        self.reporte.terminar(CLAVE, "clonacion", "Listo")
        self.assertEqual(self.reporte.actual(CLAVE, "clonacion"), Avance(2, 2, "Listo"))

    def test_incremento_distinto_de_uno(self):
        self.reporte.iniciar(CLAVE, "carga", total=100)
        self.reporte.paso_a_paso(CLAVE, "carga", "lote", incremento=25)
        self.assertEqual(self.reporte.actual(CLAVE, "carga").hechos, 25)
        self.assertEqual(self.reporte.actual(CLAVE, "carga").porcentaje(), 25)

    def test_fijar_total_conserva_lo_hecho(self):
        self.reporte.iniciar(CLAVE, "formato")
        self.reporte.paso_a_paso(CLAVE, "formato", "foto.png")
        self.reporte.fijar_total(CLAVE, "formato", 4)
        self.assertEqual(self.reporte.actual(CLAVE, "formato"), Avance(1, 4, "foto.png"))

    def test_fijar_pone_todo_de_una(self):
        self.reporte.fijar(CLAVE, "verificacion", 2, 3, "Comparando")
        self.assertEqual(self.reporte.actual(CLAVE, "verificacion"), Avance(2, 3, "Comparando"))

    def test_terminar_sin_total_usa_lo_hecho(self):
        self.reporte.paso_a_paso(CLAVE, "carga", "fila")
        self.reporte.paso_a_paso(CLAVE, "carga", "fila")
        self.reporte.terminar(CLAVE, "carga")
        self.assertEqual(self.reporte.actual(CLAVE, "carga"), Avance(2, 2, "fila"))
        self.assertEqual(self.reporte.actual(CLAVE, "carga").porcentaje(), 100)

    def test_el_destino_recibe_cada_cambio(self):
        self.reporte.iniciar(CLAVE, "clonacion", total=2, mensaje="Copiando")
        self.reporte.paso_a_paso(CLAVE, "clonacion", "uno.pdf")
        self.reporte.paso_a_paso(CLAVE, "clonacion", "dos.pdf")
        self.reporte.terminar(CLAVE, "clonacion", "Listo")
        self.assertEqual([c for c, _p, _a in self.recibido], [CLAVE] * 4)
        self.assertEqual([p for _c, p, _a in self.recibido], ["clonacion"] * 4)
        self.assertEqual(
            [(a.hechos, a.total, a.mensaje) for _c, _p, a in self.recibido],
            [(0, 2, "Copiando"), (1, 2, "uno.pdf"), (2, 2, "dos.pdf"), (2, 2, "Listo")],
        )

    def test_el_destino_recibe_una_copia(self):
        self.reporte.iniciar(CLAVE, "clonacion", total=2)
        self.recibido[-1][2].hechos = 99
        self.assertEqual(self.reporte.actual(CLAVE, "clonacion").hechos, 0)

    def test_actual_devuelve_copia_y_vacio_si_no_hay(self):
        self.assertEqual(self.reporte.actual("otro|lote", "carga"), Avance())
        self.reporte.iniciar(CLAVE, "carga", total=5)
        copia = self.reporte.actual(CLAVE, "carga")
        copia.hechos = 4
        self.assertEqual(self.reporte.actual(CLAVE, "carga").hechos, 0)

    def test_pasos_y_lotes_no_se_mezclan(self):
        self.reporte.iniciar(CLAVE, "clonacion", total=2)
        self.reporte.paso_a_paso(CLAVE, "clonacion", "uno")
        self.reporte.iniciar("OTRO|LOTE", "clonacion", total=9)
        self.assertEqual(self.reporte.actual(CLAVE, "clonacion").hechos, 1)
        self.assertEqual(self.reporte.actual("OTRO|LOTE", "clonacion"), Avance(0, 9, ""))
        self.assertEqual(self.reporte.actual(CLAVE, "formato"), Avance())
        self.assertEqual(sorted(self.reporte.todos()), [CLAVE, "OTRO|LOTE"])

    def test_dos_hilos_no_pierden_cuentas(self):
        candado = threading.Lock()
        recibidos = []

        def destino(_clave, _paso, avance):
            with candado:
                recibidos.append(avance.hechos)

        reporte = Reporte(destino=destino)
        reporte.iniciar(CLAVE, "clonacion", total=1000)

        def trabajar():
            for _ in range(500):
                reporte.paso_a_paso(CLAVE, "clonacion", "archivo")

        hilos = [threading.Thread(target=trabajar) for _ in range(2)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

        self.assertEqual(reporte.actual(CLAVE, "clonacion").hechos, 1000)
        self.assertEqual(reporte.actual(CLAVE, "clonacion").porcentaje(), 100)
        self.assertEqual(len(recibidos), 1001)  # el iniciar + los 1000 pasos
        self.assertEqual(max(recibidos), 1000)


class TestAvanceClonacion(unittest.TestCase):
    """El avance de clonar_arbol cuenta una vez cada archivo del origen."""

    def setUp(self):
        self.fake = FakeDrive()
        self.origen = self.fake.agregar_carpeta("ORIGEN")
        self.fake.agregar_archivo("readme.pdf", self.origen, contenido=b"raiz")
        self.fake.agregar_archivo("pieza_01.JPG", self.origen, contenido=b"jpg", mime="image/jpeg")
        sub = self.fake.agregar_carpeta("A", self.origen)
        self.fake.agregar_archivo("guía.docx", sub, contenido=b"docx")
        hondo = self.fake.agregar_carpeta("B", sub)
        self.fake.agregar_archivo("nota.txt", hondo, contenido=b"txt")
        self.fake.agregar_carpeta("Vacía", self.origen)
        self.destino = self.fake.agregar_carpeta("DESTINO")
        self.espia = Espia()

    def _clonar(self, **kw):
        return clonar_arbol(
            self.fake, self.origen, self.destino, log=SILENCIO, dormir=SILENCIO, **kw
        )

    def test_total_correcto_y_ultima_llamada_completa(self):
        resumen = self._clonar(avance=self.espia)
        self.assertTrue(resumen.ok())
        self.assertEqual(self.espia.llamadas[0], (0, 4, "4 archivo(s) por clonar"))
        self.assertTrue(all(total == 4 for _h, total, _m in self.espia.llamadas))
        self.assertEqual([h for h, _t, _m in self.espia.llamadas], [0, 1, 2, 3, 4])
        self.assertEqual(self.espia.ultima[0], 4)
        self.assertEqual(self.espia.ultima[0], self.espia.llamadas[-1][1])  # hechos == total
        self.assertEqual(
            sorted(m for _h, _t, m in self.espia.llamadas[1:]),
            ["guía.docx", "nota.txt", "pieza_01.JPG", "readme.pdf"],
        )

    def test_los_omitidos_tambien_avanzan(self):
        self._clonar()  # primera clonación completa, sin avance
        self.espia = Espia()
        resumen = self._clonar(avance=self.espia)
        self.assertEqual(resumen.archivos_omitidos, 4)
        self.assertEqual(self.espia.ultima[:2], (4, 4))

    def _contador_de_listados(self):
        """listar_hijos de verdad, contando cuántas veces se llama."""
        cuenta = {"n": 0}

        def listar(svc, parent_id, **kw):
            cuenta["n"] += 1
            return drive.listar_hijos(svc, parent_id, **kw)

        return cuenta, listar

    def test_sin_avance_no_recorre_el_origen_para_contar(self):
        cuenta, listar = self._contador_de_listados()
        resumen = self._clonar(listar=listar)
        sin_avance = cuenta["n"]

        # Mismo escenario desde cero, ahora con avance: solo cambian los listados
        # del conteo previo (las 4 carpetas del origen), nunca el resultado.
        self.setUp()
        cuenta, listar = self._contador_de_listados()
        con_avance = self._clonar(listar=listar, avance=self.espia)

        self.assertEqual(cuenta["n"], sin_avance + 4)
        self.assertEqual(resumen, con_avance)


class TestAvanceFormato(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDrive()
        self.raiz = self.fake.agregar_carpeta("Clon")
        self.espia = Espia()

    def test_total_de_jpg_y_ultima_llamada_completa(self):
        self.fake.agregar_archivo("foto.JPG", self.raiz, contenido=_jpg(), mime="image/jpeg")
        self.fake.agregar_archivo("texto.pdf", self.raiz, contenido=b"pdf")
        sub = self.fake.agregar_carpeta("Sub", self.raiz)
        self.fake.agregar_archivo("otra.jpeg", sub, contenido=_jpg(), mime="image/jpeg")
        resumen = convertir_arbol(self.fake, self.raiz, avance=self.espia)
        self.assertEqual(resumen.convertidos, 2)
        self.assertEqual(self.espia.llamadas[0], (0, 2, "2 imagen(es) por convertir"))
        self.assertEqual([h for h, _t, _m in self.espia.llamadas], [0, 1, 2])
        self.assertEqual(self.espia.ultima[0], self.espia.ultima[1])
        self.assertEqual(sorted(m for _h, _t, m in self.espia.llamadas[1:]), ["foto.JPG", "otra.jpeg"])

    def test_los_ya_convertidos_avanzan(self):
        self.fake.agregar_archivo("foto.jpg", self.raiz, contenido=_jpg(), mime="image/jpeg")
        self.fake.agregar_archivo("foto.png", self.raiz, contenido=b"png", mime="image/png")
        resumen = convertir_arbol(self.fake, self.raiz, avance=self.espia)
        self.assertEqual((resumen.convertidos, resumen.ya_convertidos), (0, 1))
        self.assertEqual(self.espia.ultima[:2], (1, 1))

    def test_sin_jpg_el_total_es_cero(self):
        self.fake.agregar_archivo("texto.pdf", self.raiz, contenido=b"pdf")
        convertir_arbol(self.fake, self.raiz, avance=self.espia)
        self.assertEqual(self.espia.llamadas, [(0, 0, "0 imagen(es) por convertir")])
        self.assertEqual(Avance(*self.espia.ultima).porcentaje(), 0)

    def test_el_que_falla_tambien_avanza(self):
        self.fake.agregar_archivo("mala.jpg", self.raiz, contenido=b"no-es-imagen", mime="image/jpeg")
        resumen = convertir_arbol(self.fake, self.raiz, avance=self.espia)
        self.assertFalse(resumen.ok())
        self.assertEqual(self.espia.ultima[:2], (1, 1))

    def test_sin_avance_no_cambia_nada(self):
        self.fake.agregar_archivo("foto.JPG", self.raiz, contenido=_jpg(), mime="image/jpeg")
        resumen = convertir_arbol(self.fake, self.raiz)
        self.assertEqual(resumen.convertidos, 1)


class TestAvanceVerificacion(unittest.TestCase):
    def test_tres_fases_con_mensajes(self):
        fake = FakeDrive()
        origen = fake.agregar_carpeta("Programa")
        clon = fake.agregar_carpeta("Programa clon")
        fake.agregar_archivo("foto.jpg", origen, contenido=b"jpg", mime="image/jpeg")
        fake.agregar_archivo("foto.png", clon, contenido=b"png", mime="image/png")
        espia = Espia()
        resultado = verificar_lote(
            fake,
            origen,
            clon,
            programa="Programa",
            meta={"cliente": "PRODUCTO", "raiz": "LMS_Carga"},
            parser=lambda *_a: {"ok": "1"},
            avance=espia,
        )
        self.assertEqual(resultado.estado, "ok")
        self.assertEqual([h for h, _t, _m in espia.llamadas], [0, 1, 2, 3])
        self.assertTrue(all(total == 3 for _h, total, _m in espia.llamadas))
        self.assertEqual(
            [m for _h, _t, m in espia.llamadas[:3]],
            ["Inventariando origen", "Inventariando clon", "Comparando"],
        )
        self.assertEqual(espia.ultima[0], espia.ultima[1])


class TestAvanceGcp(unittest.TestCase):
    def test_escanear_lote_avanza_por_registro(self):
        registros = [
            {"archivo_nombre": "uno.pdf", "archivo_enlace": "https://drive/1"},
            {"archivo_nombre": "dos.pdf", "archivo_enlace": "https://drive/2"},
        ]
        rutas = types.SimpleNamespace(escanear_ruta_drive=lambda *_a: registros)
        lote = SimpleNamespace(cliente_gcp="PRODUCTO", raiz_gcp="LMS_Carga", escuela_gcp="")
        espia = Espia()
        with mock.patch.object(gcp, "_heredados", return_value=(None, rutas)):
            filas = gcp.escanear_lote(FakeDrive(), "DEST", lote, "PROGRAMA", avance=espia)
        self.assertEqual(len(filas), 2)
        self.assertEqual(espia.llamadas[0], (0, 0, "Escaneando el clon"))
        self.assertEqual(espia.llamadas[1], (0, 2, "2 archivo(s) encontrados en el clon"))
        self.assertEqual([h for h, _t, _m in espia.llamadas[2:]], [1, 2])
        self.assertEqual(espia.ultima, (2, 2, "dos.pdf"))

    def test_cargar_lote_avanza_por_fila(self):
        filas = [{**FILA, "archivo_enlace": f"https://drive/file/d/{i}/view"} for i in range(5)]
        espia = Espia()
        conexion = Conexion()
        with mock.patch.object(gcp, "_heredados", return_value=(heredado(), None)):
            stats = gcp.cargar_lote(filas, conectar=lambda: conexion, avance=espia)
        self.assertEqual(stats["insertados"], 5)
        self.assertEqual(espia.llamadas[0], (0, 5, "5 archivo(s) por cargar"))
        self.assertEqual([h for h, _t, _m in espia.llamadas], [0, 1, 2, 3, 4, 5])
        self.assertEqual(espia.ultima[:2], (5, 5))

    def test_cargar_lote_cuenta_tambien_las_filas_sin_enlace(self):
        filas = [FILA, {**FILA, "archivo_enlace": ""}]
        espia = Espia()
        conexion = Conexion()
        with mock.patch.object(gcp, "_heredados", return_value=(heredado(), None)):
            gcp.cargar_lote(filas, conectar=lambda: conexion, avance=espia)
        self.assertEqual(espia.ultima[:2], (2, 2))

    def test_simular_avanza_hasta_el_final_sin_conectar(self):
        espia = Espia()
        conectar = mock.Mock(side_effect=AssertionError("no debe conectar"))
        gcp.cargar_lote([FILA, FILA], simular=True, conectar=conectar, avance=espia)
        self.assertEqual(espia.ultima, (2, 2, "Simulación: no se escribió en la base"))
        conectar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
