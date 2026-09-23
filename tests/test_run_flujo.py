"""
Pruebas de run_flujo.py (fase 1) con FakeDrive, Excel temporal y dir de corridas temporal.
Nada toca la red, Google ni la base de datos: credenciales, servicio y conexión se
sustituyen con unittest.mock; los sleeps de la clonación se anulan.
"""

from __future__ import annotations

import contextlib, functools, io, json, os, tempfile, unittest
from pathlib import Path
from unittest import mock

import openpyxl

import run_flujo
from flujo_lib import clonacion
from flujo_lib.formato import ResumenFormato
from flujo_lib.verificacion import ResultadoVerificacion
from flujo_lib.mensajes import ErrorFlujo
from flujo_lib.nombres import nombre_canonico
from tests.fake_drive import FakeDrive, hacer_http_error
from tests.test_prevalidacion import BaseFalsa

ORIGEN_A = "origenBogota01"
ORIGEN_B = "origenMedellin02"
RAIZ = "raizLmsCarga01"
CABECERA = ["cliente", "etiqueta", "origen", "destino"]
ENV_OK = {
    "CORREOS_AVISO": "ana@cun.edu.co, luis@cun.edu.co",
    "DB_HOST": "10.0.0.5",
    "DB_PORT": "5432",
    "DB_NAME": "planner_db",
    "DB_USER": "fabrica",
    "DB_PASSWORD": "secreto",
}
CREDS = object()
SILENCIO = lambda *_a, **_k: None  # noqa: E731


def _url(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}?usp=sharing"


def _arbol(fake: FakeDrive, raiz_id: str, prefijo: str = "") -> dict[str, str]:
    """{ruta relativa canónica: md5 o "<carpeta>"} de todo lo visible bajo raiz_id."""
    salida: dict[str, str] = {}
    for h in fake.hijos(raiz_id):
        ruta = f"{prefijo}/{nombre_canonico(h['name'])}" if prefijo else nombre_canonico(h["name"])
        if h["mimeType"] == "application/vnd.google-apps.folder":
            salida[ruta] = "<carpeta>"
            salida.update(_arbol(fake, h["id"], ruta))
        else:
            salida[ruta] = h["md5Checksum"]
    return salida


class _PeticionQueFalla:
    def __init__(self, status: int):
        self._status = status

    def execute(self, http=None, num_retries: int = 0):
        raise hacer_http_error(self._status, uri="fake://drive/copy")


class _DriveConCopiaProhibida:
    """FakeDrive donde copiar un archivo concreto siempre falla con el código dado."""

    def __init__(self, fake: FakeDrive, file_id: str, status: int):
        self._fake = fake
        self._file_id = file_id
        self._status = status

    def files(self):
        files = self._fake.files()
        copia_real = files.copy

        def copy(fileId, **kw):
            if fileId == self._file_id:
                return _PeticionQueFalla(self._status)
            return copia_real(fileId=fileId, **kw)

        files.copy = copy
        return files

    def about(self):
        return self._fake.about()


# ---------------------------------------------------------------------------
# Base común
# ---------------------------------------------------------------------------
class BaseRunFlujo(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.dir_corridas = self.dir / "corridas"
        self.fake = FakeDrive()
        # Origen A: dos archivos (uno JPG) y una subcarpeta con un archivo.
        self.fake.agregar_carpeta("Bogotá 2026", id=ORIGEN_A)
        self.fake.agregar_archivo("pieza_01.JPG", ORIGEN_A, contenido=b"jpg1", mime="image/jpeg")
        self.fake.agregar_archivo("guion.pdf", ORIGEN_A, contenido=b"pdf1")
        sub = self.fake.agregar_carpeta("Recursos", ORIGEN_A)
        self.fake.agregar_archivo("logo.png", sub, contenido=b"png1", mime="image/png")
        # Origen B: un archivo. Ambos lotes comparten la misma raíz de destino.
        self.fake.agregar_carpeta("Medellín 2026", id=ORIGEN_B)
        self.fake.agregar_archivo("pieza_02.jpeg", ORIGEN_B, contenido=b"jpg2", mime="image/jpeg")
        self.fake.agregar_carpeta("LMS_Carga", id=RAIZ)
        self.svc = self.fake  # lo que devuelve construir_servicio (se puede envolver)
        self.db = BaseFalsa(esquemas=("fabrica", "fabrica_pruebas"))
        self.resumenes: list[clonacion.ResumenClon] = []
        self.salida = io.StringIO()

        def espia_clonar(*a, **kw):
            kw.setdefault("dormir", SILENCIO)  # nunca esperar de verdad en pruebas
            resumen = clonacion.clonar_arbol(*a, **kw)
            self.resumenes.append(resumen)
            return resumen

        self.cargar_credenciales = mock.Mock(return_value=CREDS)
        parches = [
            mock.patch("flujo_lib.prevalidacion.drive.cargar_credenciales", self.cargar_credenciales),
            mock.patch("flujo_lib.prevalidacion.drive.construir_servicio", lambda _creds: self.svc),
            mock.patch("flujo_lib.prevalidacion._conectar_psycopg2", self.db),
            mock.patch("run_flujo.clonar_arbol", espia_clonar),
            mock.patch("run_flujo.convertir_arbol", return_value=ResumenFormato()),
            mock.patch("run_flujo.verificar_lote", return_value=ResultadoVerificacion()),
            mock.patch("run_flujo.generar_inventario", side_effect=lambda _s, _t, salida: salida),
            mock.patch("run_flujo.publicar_inventario", return_value="https://docs.google.com/spreadsheets/d/fake/edit"),
            mock.patch("run_flujo.escanear_lote", return_value=[{"programa_nombre": "PROGRAMA", "archivo_enlace": "https://drive/fake"}]),
            mock.patch("run_flujo.cargar_lote", return_value={"total": 1, "insertados": 1, "existentes": 0, "simulados": 0}),
            mock.patch("run_flujo.construir_mensaje", return_value=object()),
            mock.patch("run_flujo.enviar_correo", return_value={"id": "correo-falso"}),
            mock.patch.dict(os.environ, ENV_OK),
        ]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self._tmp.cleanup()

    # ----- ayudas ---------------------------------------------------------
    def excel(self, filas: list[list] | None = None, nombre: str = "RUTAS.xlsx") -> Path:
        if filas is None:
            filas = [
                CABECERA,
                ["PRODUCTO", "Bogotá", _url(ORIGEN_A), _url(RAIZ)],
                ["TANIA", "Medellín", ORIGEN_B, RAIZ],
            ]
        libro = openpyxl.Workbook()
        hoja = libro.active
        for fila in filas:
            hoja.append(fila)
        ruta = self.dir / nombre
        libro.save(ruta)
        libro.close()
        return ruta

    def correr(self, excel: Path, *extra: str) -> int:
        argv = ["--excel", str(excel), "--dir-corridas", str(self.dir_corridas), *extra]
        with contextlib.redirect_stdout(self.salida):
            return run_flujo.main(argv)

    def estado(self, stem: str = "RUTAS") -> dict:
        return json.loads((self.dir_corridas / f"{stem}.estado.json").read_text(encoding="utf-8"))

    def lote(self, origen_id: str, stem: str = "RUTAS") -> dict:
        return self.estado(stem)["lotes"][f"{origen_id}|{RAIZ}"]

    def pasos(self, origen_id: str) -> dict[str, str]:
        return {p: v["estado"] for p, v in self.lote(origen_id)["pasos"].items()}

    def subcarpeta(self, nombre: str, raiz_id: str = RAIZ) -> list[dict]:
        return [
            h
            for h in self.fake.hijos(raiz_id)
            if h["name"] == nombre and h["mimeType"] == "application/vnd.google-apps.folder"
        ]

    def logs(self) -> list[Path]:
        return sorted((self.dir_corridas / "logs").glob("*.log"))


# ---------------------------------------------------------------------------
# (a) corrida feliz y (b)/(c) reanudación
# ---------------------------------------------------------------------------
class TestCorridaFeliz(BaseRunFlujo):
    def test_fase3_envia_un_solo_correo_por_corrida(self):
        excel = self.excel()
        self.assertEqual(self.correr(excel), 0)
        self.assertEqual(run_flujo.enviar_correo.call_count, 1)
        self.assertEqual(run_flujo.cargar_lote.call_count, 2)
        self.assertTrue(all(self.lote(origen)["pasos"]["carga"]["estado"] == "ok" for origen in (ORIGEN_A, ORIGEN_B)))

    def test_simular_no_escribe_base_ni_envia_correo(self):
        excel = self.excel()
        self.assertEqual(self.correr(excel, "--simular"), 0)
        self.assertTrue(all(llamada.kwargs["simular"] for llamada in run_flujo.cargar_lote.call_args_list))
        run_flujo.enviar_correo.assert_not_called()

    def test_a_dos_lotes_clonados_estado_y_excel(self):
        excel = self.excel()
        codigo = self.correr(excel)
        self.assertEqual(codigo, 0, self.salida.getvalue())

        # Se creó UNA subcarpeta con el nombre exacto de cada origen dentro de la raíz.
        sub_a = self.subcarpeta("Bogotá 2026")
        sub_b = self.subcarpeta("Medellín 2026")
        self.assertEqual(len(sub_a), 1)
        self.assertEqual(len(sub_b), 1)
        # El clon queda igual al origen (mismos nombres canónicos y contenidos).
        self.assertEqual(_arbol(self.fake, sub_a[0]["id"]), _arbol(self.fake, ORIGEN_A))
        self.assertEqual(_arbol(self.fake, sub_b[0]["id"]), _arbol(self.fake, ORIGEN_B))
        self.assertEqual(
            set(_arbol(self.fake, sub_a[0]["id"])), {"pieza_01.png", "guion.pdf", "Recursos", "Recursos/logo.png"}
        )
        self.assertEqual(self.fake.llamadas["copy"], 4)
        # Nada se tocó bajo los orígenes.
        self.assertEqual(len(self.fake.hijos(ORIGEN_A)), 3)
        self.assertEqual(len(self.fake.hijos(ORIGEN_B)), 1)

        # Estado JSON.
        datos = self.estado()
        self.assertEqual(len(datos["corridas"]), 1)
        self.assertEqual(datos["corridas"][0]["resultado"], "ok")
        self.assertIsNotNone(datos["corridas"][0]["fin"])
        self.assertEqual(
            datos["corridas"][0]["argumentos"],
            {
                "excel": str(excel.resolve()),
                "schema": "fabrica_pruebas",
                "simular": False,
                "forzar_carga": False,
                "solo_prevalidar": False,
                "rehacer": [],
                "no_interactivo": False,
            },
        )
        esperado = {
            "destino": "ok",
            "clonacion": "ok",
            "formato": "ok",
            "verificacion": "ok",
            "carga": "ok",
        }
        self.assertEqual(self.pasos(ORIGEN_A), esperado)
        self.assertEqual(self.pasos(ORIGEN_B), esperado)
        lote_a = self.lote(ORIGEN_A)
        self.assertEqual(lote_a["destino_id"], sub_a[0]["id"])
        self.assertEqual(lote_a["destino_nombre"], "Bogotá 2026")
        self.assertEqual(lote_a["etiqueta"], "Bogotá")
        self.assertEqual(lote_a["cliente"], "PRODUCTO")
        self.assertIsNone(lote_a["ultimo_error"])
        self.assertIn("creada «Bogotá 2026» dentro de «LMS_Carga»", lote_a["pasos"]["destino"]["detalle"])
        self.assertIn("3 archivo(s) copiado(s)", lote_a["pasos"]["clonacion"]["detalle"])
        self.assertIn("convertido", lote_a["pasos"]["formato"]["detalle"])
        self.assertIn("insertado", lote_a["pasos"]["carga"]["detalle"])

        # Excel de estado y log de la corrida.
        xlsx = self.dir_corridas / "RUTAS.estado.xlsx"
        self.assertTrue(xlsx.is_file())
        libro = openpyxl.load_workbook(xlsx)
        hoja = libro["Estado"]
        self.assertEqual([c.value for c in hoja[2]][:4], ["Bogotá", 2, "PRODUCTO", "Bogotá 2026"])
        self.assertEqual(hoja.cell(2, 6).value, "OK")
        self.assertEqual(hoja.cell(2, 7).value, "OK")
        self.assertEqual(hoja.cell(2, 8).value, "OK")
        libro.close()
        logs = self.logs()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].name, f"{datos['corridas'][0]['id']}_RUTAS.log")
        texto_log = logs[0].read_text(encoding="utf-8")
        self.assertIn("OK Cuenta de Google: fabrica.contenidos@cun.edu.co", texto_log)
        self.assertIn("[archivo] pieza_01.JPG", texto_log)
        self.assertIn(run_flujo.NOTA_FASES, texto_log)

        # Consola: hallazgos, resumen alineado y rutas.
        consola = self.salida.getvalue()
        self.assertIn("OK Excel RUTAS.xlsx: 2 lote(s) por procesar.", consola)
        self.assertIn("Resumen por lote:", consola)
        self.assertRegex(consola, r"Etiqueta\s+Carpeta destino\s+Destino\s+Clonación")
        self.assertRegex(consola, r"Bogotá\s+Bogotá 2026\s+OK\s+OK")
        self.assertRegex(consola, r"Medellín\s+Medellín 2026\s+OK\s+OK")
        self.assertIn(str(xlsx), consola)
        self.assertIn(str(logs[0]), consola)
        self.assertIn(run_flujo.NOTA_FASES, consola)

    def test_b_segunda_corrida_no_vuelve_a_copiar(self):
        excel = self.excel()
        self.assertEqual(self.correr(excel), 0)
        copias = self.fake.llamadas["copy"]
        creaciones = self.fake.llamadas["create"]
        self.salida = io.StringIO()

        self.assertEqual(self.correr(excel), 0)

        self.assertEqual(self.fake.llamadas["copy"], copias)
        self.assertEqual(self.fake.llamadas["create"], creaciones)
        self.assertEqual(len(self.resumenes), 2)  # solo la primera corrida clonó
        self.assertEqual(len(self.subcarpeta("Bogotá 2026")), 1)
        consola = self.salida.getvalue()
        self.assertIn("ya clonado en una corrida anterior (usa --rehacer clonacion", consola)
        self.assertIn("carpeta destino ya resuelta en una corrida anterior", consola)
        datos = self.estado()
        self.assertEqual([c["resultado"] for c in datos["corridas"]], ["ok", "ok"])
        self.assertEqual(self.pasos(ORIGEN_A)["clonacion"], "ok")
        self.assertEqual(len(self.logs()), 2)

    def test_c_rehacer_clonacion_vuelve_a_recorrer_sin_duplicar(self):
        excel = self.excel()
        self.assertEqual(self.correr(excel), 0)
        copias = self.fake.llamadas["copy"]
        listados = self.fake.llamadas["list"]
        # Simula un archivo nuevo en el origen entre corridas: solo ese se copia.
        self.fake.agregar_archivo("nuevo.pdf", ORIGEN_B, contenido=b"n")

        self.assertEqual(self.correr(excel, "--rehacer", "clonacion"), 0)

        self.assertEqual(len(self.resumenes), 4)
        self.assertGreater(self.resumenes[2].archivos_omitidos, 0)
        self.assertEqual(self.resumenes[2].archivos_copiados, 0)  # lote A sin cambios
        self.assertEqual(self.resumenes[3].archivos_omitidos, 1)
        self.assertEqual(self.resumenes[3].archivos_copiados, 1)  # nuevo.pdf del lote B
        self.assertEqual(self.fake.llamadas["copy"], copias + 1)
        self.assertGreater(self.fake.llamadas["list"], listados)
        self.assertEqual(len(self.subcarpeta("Bogotá 2026")), 1)
        sub_b = self.subcarpeta("Medellín 2026")[0]
        self.assertEqual(_arbol(self.fake, sub_b["id"]), _arbol(self.fake, ORIGEN_B))
        datos = self.estado()
        self.assertEqual(datos["corridas"][1]["argumentos"]["rehacer"], ["clonacion"])
        self.assertIn("archivo(s) ya existente(s)", self.lote(ORIGEN_A)["pasos"]["clonacion"]["detalle"])
        self.assertIn("ya resuelta en una corrida anterior", self.salida.getvalue())  # destino no se rehízo

    def test_rehacer_todo_repite_destino_y_clonacion(self):
        excel = self.excel()
        self.assertEqual(self.correr(excel), 0)
        self.salida = io.StringIO()

        self.assertEqual(self.correr(excel, "--rehacer", "todo"), 0)

        consola = self.salida.getvalue()
        self.assertNotIn("ya resuelta en una corrida anterior", consola)
        self.assertNotIn("ya clonado en una corrida anterior", consola)
        self.assertIn("reutilizada «Bogotá 2026» dentro de «LMS_Carga»", consola)
        self.assertEqual(len(self.subcarpeta("Bogotá 2026")), 1)  # no se duplica la carpeta
        self.assertEqual(self.estado()["corridas"][1]["argumentos"]["rehacer"], list(run_flujo.PASOS))

    def test_destino_borrado_entre_corridas_se_resuelve_y_reclona(self):
        excel = self.excel()
        self.assertEqual(self.correr(excel), 0)
        anterior = self.lote(ORIGEN_A)["destino_id"]
        self.fake.files().update(fileId=anterior, body={"trashed": True}).execute()

        self.assertEqual(self.correr(excel), 0)

        lote = self.lote(ORIGEN_A)
        self.assertNotEqual(lote["destino_id"], anterior)
        self.assertEqual(lote["pasos"]["destino"]["estado"], "ok")
        self.assertEqual(lote["pasos"]["clonacion"]["estado"], "ok")
        self.assertEqual(_arbol(self.fake, ORIGEN_A), _arbol(self.fake, lote["destino_id"]))

    def test_compuerta_retenida_y_forzada(self):
        excel = self.excel([CABECERA, ["PRODUCTO", "Bogotá", ORIGEN_A, RAIZ]])
        diferencia = ResultadoVerificacion("con_diferencias", ["Archivo mal ubicado: x.pdf."])
        with mock.patch("run_flujo.verificar_lote", return_value=diferencia):
            self.assertEqual(self.correr(excel), 2)
        self.assertEqual(self.lote(ORIGEN_A)["pasos"]["carga"]["estado"], "omitido")

        with mock.patch("run_flujo.verificar_lote", return_value=diferencia):
            self.assertEqual(self.correr(excel, "--rehacer", "verificacion", "--forzar-carga"), 2)
        lote = self.lote(ORIGEN_A)
        self.assertEqual(lote["pasos"]["verificacion"]["estado"], "con_diferencias")
        self.assertEqual(lote["pasos"]["carga"]["estado"], "ok")


# ---------------------------------------------------------------------------
# (d) (e) (f) prevalidación
# ---------------------------------------------------------------------------
class TestPrevalidacion(BaseRunFlujo):
    def test_d_excel_sin_cliente_no_arranca(self):
        excel = self.excel([["etiqueta", "origen", "destino"], ["Bogotá", _url(ORIGEN_A), _url(RAIZ)]])

        self.assertEqual(self.correr(excel), 1)

        self.assertEqual(self.fake.llamadas["create"], 0)
        self.assertEqual(self.fake.llamadas["copy"], 0)
        self.assertEqual(self.fake.hijos(RAIZ), [])
        datos = self.estado()
        self.assertEqual(datos["corridas"][0]["resultado"], "fallido_prevalidacion")
        self.assertIsNotNone(datos["corridas"][0]["fin"])
        self.assertEqual(datos["lotes"], {})
        self.assertTrue((self.dir_corridas / "RUTAS.estado.xlsx").is_file())
        consola = self.salida.getvalue()
        self.assertIn("ERROR ", consola)
        self.assertIn("cliente", consola)
        self.assertIn("El flujo no arrancó. Corrige lo anterior y vuelve a ejecutar.", consola)
        self.assertIn(str(datos and self.logs()[0]), consola)

    def test_e_solo_prevalidar_no_crea_nada(self):
        excel = self.excel()

        self.assertEqual(self.correr(excel, "--solo-prevalidar"), 0)

        self.assertEqual(self.fake.llamadas["create"], 0)
        self.assertEqual(self.fake.llamadas["copy"], 0)
        self.assertGreater(self.fake.llamadas["get"], 0)  # sí revisó las carpetas
        datos = self.estado()
        self.assertEqual(datos["corridas"][0]["resultado"], "prevalidacion_ok")
        self.assertTrue(datos["corridas"][0]["argumentos"]["solo_prevalidar"])
        self.assertEqual(datos["lotes"], {})
        self.assertEqual(self.resumenes, [])
        self.assertIn("Prevalidación correcta. No se clonó nada", self.salida.getvalue())

    def test_f_raiz_destino_inexistente_falla_en_prevalidacion(self):
        excel = self.excel(
            [
                CABECERA,
                ["PRODUCTO", "Bogotá", _url(ORIGEN_A), _url(RAIZ)],
                ["TANIA", "Medellín", ORIGEN_B, "raizQueNoExiste99"],
            ]
        )

        self.assertEqual(self.correr(excel), 1)

        self.assertEqual(self.fake.llamadas["create"], 0)
        self.assertEqual(self.fake.llamadas["copy"], 0)
        self.assertEqual(self.fake.hijos(RAIZ), [])  # ni siquiera el lote bueno se tocó
        datos = self.estado()
        self.assertEqual(datos["corridas"][0]["resultado"], "fallido_prevalidacion")
        self.assertEqual(datos["lotes"], {})
        consola = self.salida.getvalue()
        self.assertIn("ERROR No se encontró la carpeta destino del lote «Medellín».", consola)
        self.assertIn("OK Lote «Bogotá»", consola)

    def test_simular_convierte_db_y_correo_en_avisos(self):
        excel = self.excel()
        with mock.patch.dict(os.environ, {"CORREOS_AVISO": ""}):
            self.assertEqual(self.correr(excel, "--simular"), 0)
        consola = self.salida.getvalue()
        self.assertIn("AVISO No hay destinatarios de correo configurados.", consola)
        self.assertIn("simulación", consola)
        datos = self.estado()
        self.assertTrue(datos["corridas"][0]["argumentos"]["simular"])
        self.assertEqual(datos["corridas"][0]["resultado"], "ok")

    def test_no_interactivo_llega_a_cargar_credenciales(self):
        self.assertEqual(self.correr(self.excel(), "--no-interactivo", "--solo-prevalidar"), 0)
        self.cargar_credenciales.assert_called_once_with(interactivo=False)
        self.assertTrue(self.estado()["corridas"][0]["argumentos"]["no_interactivo"])

    def test_interactivo_por_defecto(self):
        self.assertEqual(self.correr(self.excel(), "--solo-prevalidar"), 0)
        self.cargar_credenciales.assert_called_once_with(interactivo=True)

    def test_token_invalido_no_arranca(self):
        self.cargar_credenciales.side_effect = ErrorFlujo(
            "Hay que autorizar la cuenta fábrica de contenidos en Google.",
            "Ejecuta python renovar_token.py y vuelve a ejecutar.",
            paso="token",
        )
        self.assertEqual(self.correr(self.excel()), 1)
        consola = self.salida.getvalue()
        self.assertIn("ERROR Hay que autorizar la cuenta fábrica de contenidos en Google.", consola)
        self.assertEqual(self.fake.llamadas["get"], 0)
        self.assertEqual(self.estado()["corridas"][0]["resultado"], "fallido_prevalidacion")

    def test_excel_inexistente_devuelve_1_sin_crear_estado(self):
        salida_err = io.StringIO()
        with contextlib.redirect_stderr(salida_err):
            codigo = self.correr(self.dir / "NO_EXISTE.xlsx")
        self.assertEqual(codigo, 1)
        self.assertIn("No se encontró el Excel", salida_err.getvalue())
        self.assertFalse(self.dir_corridas.exists())


# ---------------------------------------------------------------------------
# (g) parser
# ---------------------------------------------------------------------------
class TestParser(unittest.TestCase):
    def test_defaults(self):
        args = run_flujo.construir_parser().parse_args([])
        self.assertIsNone(args.excel)
        self.assertEqual(args.schema, "fabrica_pruebas")  # pruebas, no producción
        self.assertFalse(args.simular)
        self.assertFalse(args.forzar_carga)
        self.assertFalse(args.solo_prevalidar)
        self.assertFalse(args.no_interactivo)
        self.assertIsNone(args.rehacer)
        self.assertEqual(args.dir_corridas, run_flujo.DIR_CORRIDAS)
        self.assertEqual(run_flujo.pasos_a_rehacer(args.rehacer), set())

    def test_rehacer_repetible_y_todo(self):
        parser = run_flujo.construir_parser()
        args = parser.parse_args(["--rehacer", "clonacion", "--rehacer", "destino"])
        self.assertEqual(args.rehacer, ["clonacion", "destino"])
        self.assertEqual(run_flujo.pasos_a_rehacer(args.rehacer), {"clonacion", "destino"})
        self.assertEqual(run_flujo.pasos_efectivos_a_rehacer(["clonacion"]), {"clonacion", "formato", "verificacion", "carga"})
        self.assertEqual(run_flujo.pasos_efectivos_a_rehacer(["verificacion"]), {"verificacion", "carga"})
        args = parser.parse_args(["--rehacer", "todo"])
        self.assertEqual(run_flujo.pasos_a_rehacer(args.rehacer), set(run_flujo.PASOS))
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["--rehacer", "correo"])

    def test_opciones_y_dir_corridas_oculto(self):
        parser = run_flujo.construir_parser()
        args = parser.parse_args(
            ["--excel", "C:/x/RUTAS.xlsx", "--schema", "fabrica_pruebas", "--simular", "--forzar-carga",
             "--solo-prevalidar", "--no-interactivo", "--dir-corridas", "C:/tmp/c"]
        )
        self.assertEqual(args.excel, Path("C:/x/RUTAS.xlsx"))
        self.assertEqual(args.schema, "fabrica_pruebas")
        self.assertTrue(args.simular and args.forzar_carga and args.solo_prevalidar and args.no_interactivo)
        self.assertEqual(args.dir_corridas, Path("C:/tmp/c"))
        self.assertNotIn("dir-corridas", parser.format_help())
        self.assertIn("--rehacer {destino,clonacion,formato,verificacion,carga,todo}", parser.format_help())

    def test_argumentos_corrida(self):
        args = run_flujo.construir_parser().parse_args(["--rehacer", "todo", "--forzar-carga"])
        datos = run_flujo.argumentos_corrida(args, Path("C:/x/RUTAS.xlsx"))
        self.assertEqual(datos["rehacer"], list(run_flujo.PASOS))
        self.assertTrue(datos["forzar_carga"])
        self.assertEqual(datos["schema"], "fabrica_pruebas")


# ---------------------------------------------------------------------------
# (h) fallos por lote
# ---------------------------------------------------------------------------
class TestFallosPorLote(BaseRunFlujo):
    def test_fallo_de_carga_revierte_un_lote_sigue_con_otro_y_notifica_una_vez(self):
        excel = self.excel()
        run_flujo.cargar_lote.side_effect = [RuntimeError("base no disponible"), {"total": 1, "insertados": 1, "existentes": 0}]
        self.assertEqual(self.correr(excel), 1)
        self.assertEqual(self.lote(ORIGEN_A)["pasos"]["carga"]["estado"], "fallido")
        self.assertEqual(self.lote(ORIGEN_B)["pasos"]["carga"]["estado"], "ok")
        self.assertEqual(run_flujo.enviar_correo.call_count, 1)

    def test_h_403_en_un_archivo_deja_el_lote_fallido_y_el_otro_ok(self):
        prohibido = self.fake.agregar_archivo("secreto.pdf", ORIGEN_A, contenido=b"s")
        self.svc = _DriveConCopiaProhibida(self.fake, prohibido, 403)
        excel = self.excel()

        self.assertEqual(self.correr(excel), 1)

        self.assertEqual(
            self.pasos(ORIGEN_A),
            {"destino": "ok", "clonacion": "fallido", "formato": "omitido", "verificacion": "omitido", "carga": "omitido"},
        )
        self.assertEqual(
            self.pasos(ORIGEN_B),
            {"destino": "ok", "clonacion": "ok", "formato": "ok", "verificacion": "ok", "carga": "ok"},
        )
        lote_a = self.lote(ORIGEN_A)
        paso = lote_a["pasos"]["clonacion"]
        self.assertEqual(paso["motivo"], "No se pudieron copiar 1 archivo(s) del lote «Bogotá».")
        self.assertEqual(
            paso["accion"],
            "Vuelve a ejecutar; solo se copiará lo que falta. Si persiste, revisa permisos de esos archivos.",
        )
        self.assertEqual(paso["detalle"], "secreto.pdf")
        self.assertEqual(lote_a["ultimo_error"]["paso"], "clonacion")
        self.assertEqual(lote_a["ultimo_error"]["motivo"], paso["motivo"])
        self.assertEqual(lote_a["pasos"]["formato"]["detalle"], run_flujo.NOTA_OMITIDO)
        self.assertIsNone(self.lote(ORIGEN_B)["ultimo_error"])
        self.assertEqual(self.estado()["corridas"][0]["resultado"], "fallido")
        # El resto del lote A sí se copió y el lote B quedó completo.
        sub_a = self.subcarpeta("Bogotá 2026")[0]
        self.assertEqual(
            set(_arbol(self.fake, sub_a["id"])), {"pieza_01.png", "guion.pdf", "Recursos", "Recursos/logo.png"}
        )
        sub_b = self.subcarpeta("Medellín 2026")[0]
        self.assertEqual(_arbol(self.fake, sub_b["id"]), _arbol(self.fake, ORIGEN_B))
        consola = self.salida.getvalue()
        self.assertIn("Lote «Bogotá»: No se pudieron copiar 1 archivo(s) del lote «Bogotá».", consola)
        self.assertRegex(consola, r"Bogotá\s+Bogotá 2026\s+OK\s+Fallido")
        self.assertRegex(consola, r"Medellín\s+Medellín 2026\s+OK\s+OK")
        self.assertIn("Corrida terminada con fallos: 1 de 2 lote(s) verificados.", consola)
        # En el Excel de estado se ve qué pasó y qué hacer.
        libro = openpyxl.load_workbook(self.dir_corridas / "RUTAS.estado.xlsx")
        hoja = libro["Estado"]
        self.assertEqual(hoja.cell(2, 7).value, "Fallido")
        self.assertEqual(hoja.cell(2, 11).value, paso["motivo"])
        self.assertEqual(hoja.cell(2, 12).value, paso["accion"])
        libro.close()

    def test_reintento_tras_403_completa_el_lote_y_limpia_pendientes(self):
        prohibido = self.fake.agregar_archivo("secreto.pdf", ORIGEN_A, contenido=b"s")
        self.svc = _DriveConCopiaProhibida(self.fake, prohibido, 403)
        excel = self.excel()
        self.assertEqual(self.correr(excel), 1)
        copias = self.fake.llamadas["copy"]

        self.svc = self.fake  # "se arreglaron los permisos"
        self.assertEqual(self.correr(excel), 0)

        self.assertEqual(self.fake.llamadas["copy"], copias + 1)  # solo lo que faltaba
        self.assertEqual(
            self.pasos(ORIGEN_A),
            {"destino": "ok", "clonacion": "ok", "formato": "ok", "verificacion": "ok", "carga": "ok"},
        )
        lote_a = self.lote(ORIGEN_A)
        self.assertIsNone(lote_a["ultimo_error"])
        self.assertIn("convertido", lote_a["pasos"]["formato"]["detalle"])
        sub_a = self.subcarpeta("Bogotá 2026")
        self.assertEqual(len(sub_a), 1)
        self.assertEqual(_arbol(self.fake, sub_a[0]["id"]), _arbol(self.fake, ORIGEN_A))
        self.assertEqual([c["resultado"] for c in self.estado()["corridas"]], ["fallido", "ok"])

    def test_fallo_al_crear_destino_marca_todo_omitido_y_sigue_con_el_otro_lote(self):
        # El primer create (la subcarpeta del lote A) falla con 403 definitivo.
        self.fake.fallar("create", status=403)
        excel = self.excel()

        self.assertEqual(self.correr(excel), 1)

        self.assertEqual(
            self.pasos(ORIGEN_A),
            {"destino": "fallido", "clonacion": "omitido", "formato": "omitido", "verificacion": "omitido", "carga": "omitido"},
        )
        self.assertEqual(self.pasos(ORIGEN_B)["clonacion"], "ok")
        lote_a = self.lote(ORIGEN_A)
        self.assertEqual(
            lote_a["ultimo_error"]["motivo"],
            "La cuenta fábrica de contenidos no tiene permiso sobre la carpeta destino del lote «Bogotá».",
        )
        self.assertEqual(lote_a["ultimo_error"]["accion"], "Pide acceso a esa carpeta y vuelve a ejecutar.")
        self.assertIsNone(lote_a["destino_id"])
        self.assertEqual(lote_a["pasos"]["clonacion"]["detalle"], run_flujo.NOTA_OMITIDO)
        self.assertEqual(self.subcarpeta("Bogotá 2026"), [])
        self.assertEqual(len(self.subcarpeta("Medellín 2026")), 1)
        self.assertRegex(self.salida.getvalue(), r"Bogotá\s+-\s+Fallido\s+Omitido")

    def test_error_inesperado_en_clonacion_se_traduce_y_no_tumba_el_run(self):
        with mock.patch("run_flujo.clonar_arbol", side_effect=RuntimeError("boom")):
            self.assertEqual(self.correr(self.excel()), 1)
        lote_a = self.lote(ORIGEN_A)
        self.assertEqual(lote_a["pasos"]["clonacion"]["estado"], "fallido")
        self.assertEqual(lote_a["ultimo_error"]["motivo"], "Ocurrió un error inesperado en el paso clonacion.")
        self.assertEqual(lote_a["pasos"]["clonacion"]["detalle"], "RuntimeError('boom')")
        self.assertEqual(self.pasos(ORIGEN_B)["clonacion"], "fallido")  # ambos lotes pasaron por el paso
        self.assertEqual(self.estado()["corridas"][0]["resultado"], "fallido")
        texto_log = self.logs()[0].read_text(encoding="utf-8")
        # El detalle técnico va al registro, nunca a la página ni al correo.
        self.assertIn("detalle técnico: RuntimeError('boom')", texto_log)
        self.assertNotIn("RuntimeError", self.salida.getvalue())  # pero no a la consola

    def test_excel_de_estado_abierto_no_cambia_el_codigo_de_salida(self):
        error = ErrorFlujo("El archivo RUTAS.estado.xlsx está abierto o protegido.", "Ciérralo y vuelve a ejecutar.")
        with mock.patch.object(run_flujo.EstadoCorrida, "exportar_excel", side_effect=error):
            self.assertEqual(self.correr(self.excel()), 0)
        self.assertIn("No se pudo actualizar el Excel de estado.", self.salida.getvalue())
        self.assertFalse((self.dir_corridas / "RUTAS.estado.xlsx").exists())
        self.assertEqual(self.estado()["corridas"][0]["resultado"], "ok")

    def test_raiz_con_el_nombre_del_origen_se_usa_directa(self):
        raiz_directa = self.fake.agregar_carpeta("Medellín 2026")
        excel = self.excel([CABECERA, ["TANIA", "Medellín", ORIGEN_B, raiz_directa]])

        self.assertEqual(self.correr(excel), 0)

        self.assertEqual(self.fake.llamadas["create"], 0)  # sin subcarpeta
        lote = self.estado()["lotes"][f"{ORIGEN_B}|{raiz_directa}"]
        self.assertEqual(lote["destino_id"], raiz_directa)
        self.assertIn("directa", lote["pasos"]["destino"]["detalle"])
        self.assertIn("se usa directamente sin crear subcarpeta", lote["pasos"]["destino"]["detalle"])
        self.assertEqual(_arbol(self.fake, raiz_directa), _arbol(self.fake, ORIGEN_B))
        self.assertIn("WARNING", self.salida.getvalue())


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
class TestLogging(unittest.TestCase):
    def test_configurar_y_cerrar_logging(self):
        import logging

        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "logs" / "x.log"
            consola = io.StringIO()
            handlers = run_flujo.configurar_logging(ruta, stream=consola)
            try:
                logging.info("hola «mundo»")
                logging.debug("solo archivo")
            finally:
                run_flujo.cerrar_logging(handlers)
            texto = ruta.read_text(encoding="utf-8")
            self.assertIn("INFO hola «mundo»", texto)
            self.assertIn("DEBUG solo archivo", texto)
            self.assertIn("INFO hola «mundo»", consola.getvalue())
            self.assertNotIn("solo archivo", consola.getvalue())
            self.assertFalse(any(h in logging.getLogger().handlers for h in handlers))
            self.assertEqual(logging.getLogger("googleapiclient").level, logging.WARNING)


if __name__ == "__main__":
    unittest.main()
