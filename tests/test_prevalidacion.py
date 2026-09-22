"""Pruebas de flujo_lib.prevalidacion con FakeDrive, Excels temporales y base de datos falsa. Sin red."""

from __future__ import annotations

import os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

import openpyxl

from flujo_lib import prevalidacion
from flujo_lib.mensajes import ErrorFlujo
from flujo_lib.prevalidacion import (
    AREAS,
    CONSULTA_ESQUEMA,
    NIVELES,
    Hallazgo,
    ResultadoPrevalidacion,
    cargar_env,
    correos_aviso,
    prevalidar,
)
from tests.fake_drive import FakeDrive

ORIGEN_A = "origenBogota01"
RAIZ_A = "raizDestino01"
ORIGEN_B = "origenMedellin02"
RAIZ_B = "raizDestino02"
CABECERA = ["cliente", "etiqueta", "origen", "destino"]
ENV_OK = {
    "CORREOS_AVISO": "ana@cun.edu.co, luis@cun.edu.co",
    "DB_HOST": "10.0.0.5",
    "DB_PORT": "5432",
    "DB_NAME": "planner_db",
    "DB_USER": "fabrica",
    "DB_PASSWORD": "secreto",
}
CREDS = object()  # cualquier objeto sirve como credenciales inyectadas


def _url(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}?usp=sharing"


# ---------------------------------------------------------------------------
# Base de datos falsa (imita lo poco de psycopg2 que usa la prevalidación)
# ---------------------------------------------------------------------------
class ErrorPsycopgFalso(Exception):
    """Imita una excepción de psycopg2 sin importar el paquete (módulo psycopg2.*)."""

    __module__ = "psycopg2.OperationalError"


class CursorFalso:
    def __init__(self, esquemas: tuple[str, ...], fallo: BaseException | None = None):
        self.esquemas = esquemas
        self.fallo = fallo
        self.consultas: list[tuple[str, object]] = []
        self.cerrado = False
        self._fila = None

    def execute(self, sql: str, params=None) -> None:
        if self.fallo is not None:
            raise self.fallo
        self.consultas.append((sql, params))
        if sql.strip() == "SELECT 1":
            self._fila = (1,)
        elif "information_schema.schemata" in sql:
            self._fila = (1,) if params and params[0] in self.esquemas else None
        else:
            raise AssertionError(f"consulta inesperada: {sql!r}")

    def fetchone(self):
        return self._fila

    def close(self) -> None:
        self.cerrado = True


class ConexionFalsa:
    def __init__(self, esquemas: tuple[str, ...], fallo_consulta: BaseException | None = None):
        self.esquemas = esquemas
        self.fallo_consulta = fallo_consulta
        self.cursores: list[CursorFalso] = []
        self.cerradas = 0

    def cursor(self) -> CursorFalso:
        cur = CursorFalso(self.esquemas, self.fallo_consulta)
        self.cursores.append(cur)
        return cur

    def close(self) -> None:
        self.cerradas += 1


class BaseFalsa:
    """conectar_db inyectable: registra los parámetros y devuelve ConexionFalsa (o lanza)."""

    def __init__(
        self,
        esquemas: tuple[str, ...] = ("fabrica",),
        fallo_conexion: BaseException | None = None,
        fallo_consulta: BaseException | None = None,
    ):
        self.esquemas = esquemas
        self.fallo_conexion = fallo_conexion
        self.fallo_consulta = fallo_consulta
        self.llamadas: list[dict] = []
        self.conexiones: list[ConexionFalsa] = []

    def __call__(self, **parametros) -> ConexionFalsa:
        self.llamadas.append(parametros)
        if self.fallo_conexion is not None:
            raise self.fallo_conexion
        conn = ConexionFalsa(self.esquemas, self.fallo_consulta)
        self.conexiones.append(conn)
        return conn


# ---------------------------------------------------------------------------
# Base de las pruebas
# ---------------------------------------------------------------------------
class BasePrevalidacion(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.fake = FakeDrive()
        self.fake.agregar_carpeta("Bogotá 2026", id=ORIGEN_A)
        self.fake.agregar_archivo("pieza_01.JPG", ORIGEN_A, mime="image/jpeg")
        self.fake.agregar_carpeta("LMS_Carga", id=RAIZ_A)
        self.fake.agregar_carpeta("Medellín 2026", id=ORIGEN_B)
        self.fake.agregar_carpeta("LMS_Carga 2", id=RAIZ_B)
        self.db = BaseFalsa()
        self.registro: list[str] = []

    def tearDown(self):
        self._tmp.cleanup()

    def excel(self, filas: list[list] | None = None, nombre: str = "RUTAS.xlsx") -> Path:
        if filas is None:
            filas = [
                CABECERA,
                ["PRODUCTO", "Bogotá", _url(ORIGEN_A), _url(RAIZ_A)],
                ["TANIA", "Medellín", ORIGEN_B, RAIZ_B],
            ]
        libro = openpyxl.Workbook()
        hoja = libro.active
        for fila in filas:
            hoja.append(fila)
        ruta = self.dir / nombre
        libro.save(ruta)
        libro.close()
        return ruta

    def prevalidar(self, excel: Path | None = None, **kw) -> ResultadoPrevalidacion:
        parametros = dict(
            schema="fabrica",
            simular=False,
            interactivo=False,
            env=dict(ENV_OK),
            cargar_credenciales=lambda **_k: CREDS,
            construir_servicio=lambda _creds: self.fake,
            conectar_db=self.db,
            log=self.registro.append,
        )
        parametros.update(kw)
        return prevalidar(excel if excel is not None else self.excel(), **parametros)

    def por_area(self, res: ResultadoPrevalidacion, area: str, nivel: str | None = None) -> list[Hallazgo]:
        return [h for h in res.hallazgos if h.area == area and (nivel is None or h.nivel == nivel)]

    def unico(self, res: ResultadoPrevalidacion, area: str, nivel: str) -> Hallazgo:
        hallazgos = self.por_area(res, area, nivel)
        self.assertEqual(len(hallazgos), 1, f"{area}/{nivel}: {[h.mensaje for h in hallazgos]}")
        return hallazgos[0]


# ---------------------------------------------------------------------------
# Todo en orden
# ---------------------------------------------------------------------------
class TestTodoOk(BasePrevalidacion):
    def test_sin_errores_y_todo_cargado(self):
        res = self.prevalidar()
        self.assertTrue(res.ok, [h.texto() for h in res.errores()])
        self.assertEqual(res.errores(), [])
        self.assertEqual(res.avisos(), [])
        self.assertEqual(res.cuenta, "fabrica.contenidos@cun.edu.co")
        self.assertIs(res.creds, CREDS)
        self.assertIs(res.svc, self.fake)
        self.assertEqual([l.etiqueta for l in res.lotes], ["Bogotá", "Medellín"])
        self.assertEqual(set(res.carpetas), {ORIGEN_A, RAIZ_A, ORIGEN_B, RAIZ_B})
        self.assertEqual(res.carpetas[ORIGEN_A]["name"], "Bogotá 2026")
        self.assertEqual(res.carpetas[RAIZ_B]["name"], "LMS_Carga 2")
        # un ok por área (drive: uno por lote) y en el orden del contrato
        self.assertEqual(
            [h.area for h in res.hallazgos],
            ["excel", "token", "drive", "drive", "cliente", "cliente", "correo", "db"],
        )
        self.assertTrue(all(h.nivel == "ok" for h in res.hallazgos))

    def test_mensajes_ok(self):
        res = self.prevalidar()
        self.assertEqual(self.unico(res, "excel", "ok").mensaje, "Excel RUTAS.xlsx: 2 lote(s) por procesar.")
        self.assertEqual(self.unico(res, "token", "ok").mensaje, "Cuenta de Google: fabrica.contenidos@cun.edu.co")
        drive_ok = self.por_area(res, "drive", "ok")
        self.assertIn("Lote «Bogotá»", drive_ok[0].mensaje)
        self.assertIn("«Bogotá 2026»", drive_ok[0].mensaje)
        self.assertIn("«LMS_Carga»", drive_ok[0].mensaje)
        self.assertEqual(self.unico(res, "correo", "ok").mensaje, "Correos de aviso: ana@cun.edu.co, luis@cun.edu.co.")
        self.assertIn("esquema fabrica disponible", self.unico(res, "db", "ok").mensaje)

    def test_conexion_db_con_datos_del_env_y_cerrada(self):
        res = self.prevalidar()
        self.assertTrue(res.ok)
        self.assertEqual(len(self.db.llamadas), 1)
        parametros = self.db.llamadas[0]
        self.assertEqual(parametros["host"], "10.0.0.5")
        self.assertEqual(parametros["port"], 5432)
        self.assertEqual(parametros["dbname"], "planner_db")
        self.assertEqual(parametros["user"], "fabrica")
        self.assertEqual(parametros["password"], "secreto")
        self.assertEqual(parametros["connect_timeout"], 30)
        conn = self.db.conexiones[0]
        self.assertEqual(conn.cerradas, 1)
        cur = conn.cursores[0]
        self.assertTrue(cur.cerrado)
        self.assertEqual(cur.consultas[0][0], "SELECT 1")
        self.assertEqual(cur.consultas[1], (CONSULTA_ESQUEMA, ("fabrica",)))

    def test_puerto_por_defecto_si_falta_db_port(self):
        env = dict(ENV_OK)
        del env["DB_PORT"]
        res = self.prevalidar(env=env)
        self.assertTrue(res.ok)
        self.assertEqual(self.db.llamadas[0]["port"], 5432)

    def test_interactivo_se_pasa_a_cargar_credenciales(self):
        cargar = mock.Mock(return_value=CREDS)
        construir = mock.Mock(return_value=self.fake)
        res = self.prevalidar(interactivo=True, cargar_credenciales=cargar, construir_servicio=construir)
        self.assertTrue(res.ok)
        cargar.assert_called_once_with(interactivo=True)
        construir.assert_called_once_with(CREDS)

    def test_misma_carpeta_en_dos_lotes_se_consulta_una_vez(self):
        excel = self.excel(
            [CABECERA, ["PRODUCTO", "Lote 1", ORIGEN_A, RAIZ_A], ["PRODUCTO", "Lote 2", ORIGEN_A, RAIZ_B]]
        )
        res = self.prevalidar(excel)
        self.assertTrue(res.ok)
        self.assertEqual(self.fake.llamadas["get"], 3)  # origen una vez + dos raíces
        self.assertEqual(len(self.por_area(res, "drive", "ok")), 2)

    def test_log_recibe_progreso_y_acepta_none(self):
        self.prevalidar()
        self.assertTrue(any("Excel" in linea for linea in self.registro))
        self.assertTrue(any("Drive" in linea for linea in self.registro))
        self.assertTrue(any("base de datos" in linea for linea in self.registro))
        res = self.prevalidar(log=None)
        self.assertTrue(res.ok)

    def test_env_none_carga_env_y_usa_os_environ(self):
        with mock.patch.object(prevalidacion, "cargar_env") as cargar, mock.patch.dict(os.environ, ENV_OK):
            res = self.prevalidar(env=None)
        cargar.assert_called_once_with()
        self.assertTrue(res.ok, [h.texto() for h in res.errores()])
        self.assertEqual(self.db.llamadas[0]["dbname"], "planner_db")


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------
class TestExcel(BasePrevalidacion):
    def test_excel_invalido_es_error_y_el_resto_sigue(self):
        excel = self.excel([CABECERA, ["PRODUCTO", "Bogotá", "no es un enlace", RAIZ_A]])
        res = self.prevalidar(excel)
        self.assertFalse(res.ok)
        err = self.unico(res, "excel", "error")
        self.assertIn("Fila 2: enlace de origen inválido", err.mensaje)
        self.assertEqual(err.accion, "Corrige el Excel y vuelve a ejecutar.")
        self.assertEqual(res.lotes, [])
        # token, correo y db se revisan igual; drive no (no hay lotes)
        self.assertEqual(res.cuenta, "fabrica.contenidos@cun.edu.co")
        self.assertEqual(self.por_area(res, "drive"), [])
        self.assertEqual(self.fake.llamadas["get"], 0)
        self.assertEqual(len(self.por_area(res, "correo", "ok")), 1)
        self.assertEqual(len(self.por_area(res, "db", "ok")), 1)
        self.assertEqual(res.carpetas, {})

    def test_excel_inexistente(self):
        res = self.prevalidar(self.dir / "NO_EXISTE.xlsx")
        err = self.unico(res, "excel", "error")
        self.assertIn("No se encontró el Excel", err.mensaje)
        self.assertEqual(res.lotes, [])

    def test_excepcion_inesperada_al_leer_no_se_propaga(self):
        with mock.patch.object(prevalidacion, "leer_lotes", side_effect=RuntimeError("boom")):
            res = self.prevalidar()
        err = self.unico(res, "excel", "error")
        self.assertEqual(err.mensaje, "Ocurrió un error inesperado en el paso excel.")
        self.assertIn("boom", err.detalle)


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------
class TestToken(BasePrevalidacion):
    def test_error_flujo_al_cargar_credenciales(self):
        fallo = ErrorFlujo(
            "Hay que autorizar la cuenta fábrica de contenidos en Google.",
            "Ejecuta python renovar_token.py y vuelve a ejecutar.",
            paso="token",
            detalle="No existe token.json.",
        )
        cargar = mock.Mock(side_effect=fallo)
        construir = mock.Mock(return_value=self.fake)
        res = self.prevalidar(cargar_credenciales=cargar, construir_servicio=construir)
        self.assertFalse(res.ok)
        err = self.unico(res, "token", "error")
        self.assertEqual(err.mensaje, "Hay que autorizar la cuenta fábrica de contenidos en Google.")
        self.assertEqual(err.accion, "Ejecuta python renovar_token.py y vuelve a ejecutar.")
        self.assertEqual(err.detalle, "No existe token.json.")
        cargar.assert_called_once_with(interactivo=False)
        construir.assert_not_called()
        # sin servicio no se consultan carpetas
        self.assertIsNone(res.svc)
        self.assertEqual(res.cuenta, "")
        self.assertEqual(self.fake.llamadas["get"], 0)
        self.assertEqual(self.por_area(res, "drive"), [])
        self.assertEqual(res.carpetas, {})
        # los lotes sí se leyeron y correo/db se revisaron
        self.assertEqual(len(res.lotes), 2)
        self.assertEqual(len(self.por_area(res, "correo", "ok")), 1)
        self.assertEqual(len(self.por_area(res, "db", "ok")), 1)

    def test_excepcion_generica_al_cargar_credenciales_se_traduce(self):
        res = self.prevalidar(cargar_credenciales=mock.Mock(side_effect=ConnectionError("sin red")))
        err = self.unico(res, "token", "error")
        self.assertEqual(err.mensaje, "No hay conexión con Google o con la red.")
        self.assertIsNone(res.svc)

    def test_sesion_vencida_al_preguntar_quien_soy(self):
        self.fake.fallar("about", status=401)
        res = self.prevalidar()
        err = self.unico(res, "token", "error")
        self.assertEqual(err.mensaje, "La sesión de Google venció.")
        self.assertEqual(err.accion, "Ejecuta python renovar_token.py y vuelve a ejecutar.")
        self.assertIsNone(res.svc)
        self.assertEqual(self.fake.llamadas["get"], 0)


# ---------------------------------------------------------------------------
# Drive
# ---------------------------------------------------------------------------
class TestDrive(BasePrevalidacion):
    def test_origen_inexistente_404(self):
        excel = self.excel(
            [CABECERA, ["PRODUCTO", "Bogotá", "noExiste123", RAIZ_A], ["TANIA", "Medellín", ORIGEN_B, RAIZ_B]]
        )
        res = self.prevalidar(excel)
        self.assertFalse(res.ok)
        err = self.unico(res, "drive", "error")
        self.assertEqual(err.mensaje, "No se encontró la carpeta origen del lote «Bogotá».")
        self.assertEqual(
            err.accion, "Revisa el enlace en el Excel y que la carpeta no haya sido movida o eliminada."
        )
        # la raíz de ese lote sí se revisó y el otro lote quedó bien
        self.assertNotIn("noExiste123", res.carpetas)
        self.assertEqual(set(res.carpetas), {RAIZ_A, ORIGEN_B, RAIZ_B})
        ok = self.unico(res, "drive", "ok")
        self.assertIn("Lote «Medellín»", ok.mensaje)

    def test_destino_que_es_archivo(self):
        archivo = self.fake.agregar_archivo("guia.pdf", RAIZ_A, id="archivoGuia01")
        excel = self.excel([CABECERA, ["PRODUCTO", "Bogotá", ORIGEN_A, archivo]])
        res = self.prevalidar(excel)
        err = self.unico(res, "drive", "error")
        self.assertEqual(err.mensaje, "La carpeta destino del lote «Bogotá» no es una carpeta de Drive.")
        self.assertEqual(err.accion, "Revisa el enlace en el Excel.")
        self.assertEqual(set(res.carpetas), {ORIGEN_A})

    def test_sin_permiso_403(self):
        self.fake.fallar("get", status=403)  # la primera consulta es el origen del primer lote
        res = self.prevalidar()
        err = self.unico(res, "drive", "error")
        self.assertEqual(
            err.mensaje, "La cuenta fábrica de contenidos no tiene permiso sobre la carpeta origen del lote «Bogotá»."
        )
        self.assertEqual(err.accion, "Pide acceso a esa carpeta y vuelve a ejecutar.")
        self.assertEqual(set(res.carpetas), {RAIZ_A, ORIGEN_B, RAIZ_B})

    def test_origen_y_destino_malos_en_el_mismo_lote_dan_dos_errores(self):
        excel = self.excel([CABECERA, ["PRODUCTO", "Bogotá", "noExiste1", "noExiste2"]])
        res = self.prevalidar(excel)
        errores = self.por_area(res, "drive", "error")
        self.assertEqual(
            [e.mensaje for e in errores],
            [
                "No se encontró la carpeta origen del lote «Bogotá».",
                "No se encontró la carpeta destino del lote «Bogotá».",
            ],
        )
        self.assertEqual(self.por_area(res, "drive", "ok"), [])


# ---------------------------------------------------------------------------
# Correo
# ---------------------------------------------------------------------------
class TestCorreo(BasePrevalidacion):
    def test_sin_correos_es_error(self):
        env = dict(ENV_OK)
        del env["CORREOS_AVISO"]
        res = self.prevalidar(env=env)
        self.assertFalse(res.ok)
        err = self.unico(res, "correo", "error")
        self.assertEqual(err.mensaje, "No hay destinatarios de correo configurados.")
        self.assertEqual(err.accion, "Agrega CORREOS_AVISO en el archivo .env.")

    def test_correos_vacios_es_error(self):
        res = self.prevalidar(env={**ENV_OK, "CORREOS_AVISO": " , ,"})
        self.assertEqual(self.unico(res, "correo", "error").mensaje, "No hay destinatarios de correo configurados.")

    def test_sin_correos_con_simular_es_aviso(self):
        env = dict(ENV_OK)
        del env["CORREOS_AVISO"]
        res = self.prevalidar(env=env, simular=True)
        self.assertTrue(res.ok)
        aviso = self.unico(res, "correo", "aviso")
        self.assertEqual(aviso.mensaje, "No hay destinatarios de correo configurados.")
        self.assertEqual(self.por_area(res, "correo", "error"), [])
        self.assertEqual(len(res.avisos()), 1)

    def test_correo_sin_arroba_es_error(self):
        res = self.prevalidar(env={**ENV_OK, "CORREOS_AVISO": "ana@cun.edu.co, luis.cun.edu.co"})
        err = self.unico(res, "correo", "error")
        self.assertIn("luis.cun.edu.co", err.mensaje)
        self.assertNotIn("ana@cun.edu.co", err.mensaje)
        self.assertIn("CORREOS_AVISO", err.accion)

    def test_correo_sin_arroba_con_simular_es_aviso(self):
        res = self.prevalidar(env={**ENV_OK, "CORREOS_AVISO": "sinarroba"}, simular=True)
        self.assertTrue(res.ok)
        self.assertIn("sinarroba", self.unico(res, "correo", "aviso").mensaje)

    def test_correos_aviso_parsea(self):
        self.assertEqual(correos_aviso({"CORREOS_AVISO": " a@x.co, ,b@y.co ;c@z.co,"}), ["a@x.co", "b@y.co", "c@z.co"])
        self.assertEqual(correos_aviso({}), [])
        self.assertEqual(correos_aviso({"CORREOS_AVISO": ""}), [])


# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------
class TestBaseDatos(BasePrevalidacion):
    def test_faltan_variables_es_error_y_no_conecta(self):
        env = dict(ENV_OK)
        del env["DB_HOST"]
        env["DB_PASSWORD"] = "   "
        res = self.prevalidar(env=env)
        self.assertFalse(res.ok)
        err = self.unico(res, "db", "error")
        self.assertIn("DB_HOST", err.mensaje)
        self.assertIn("DB_PASSWORD", err.mensaje)
        self.assertNotIn("DB_NAME", err.mensaje)
        self.assertIn(".env", err.accion)
        self.assertEqual(self.db.llamadas, [])

    def test_faltan_variables_con_simular_es_aviso(self):
        env = {k: v for k, v in ENV_OK.items() if not k.startswith("DB_")}
        res = self.prevalidar(env=env, simular=True)
        self.assertTrue(res.ok)
        aviso = self.unico(res, "db", "aviso")
        self.assertIn("DB_HOST, DB_NAME, DB_USER, DB_PASSWORD", aviso.mensaje)
        self.assertEqual(self.db.llamadas, [])

    def test_db_port_no_numerico(self):
        res = self.prevalidar(env={**ENV_OK, "DB_PORT": "cinco"})
        err = self.unico(res, "db", "error")
        self.assertIn("DB_PORT", err.mensaje)
        self.assertIn("5432", err.accion)
        self.assertEqual(self.db.llamadas, [])

    def test_esquema_ausente(self):
        res = self.prevalidar(schema="fabrica_pruebas")
        self.assertFalse(res.ok)
        err = self.unico(res, "db", "error")
        self.assertEqual(err.mensaje, "El esquema fabrica_pruebas no existe en la base de datos.")
        self.assertEqual(err.accion, "Revisa el parámetro --schema.")
        conn = self.db.conexiones[0]
        self.assertEqual(conn.cerradas, 1)
        self.assertEqual(conn.cursores[0].consultas[1], (CONSULTA_ESQUEMA, ("fabrica_pruebas",)))

    def test_esquema_ausente_con_simular_es_aviso(self):
        res = self.prevalidar(schema="fabrica_pruebas", simular=True)
        self.assertTrue(res.ok)
        self.assertEqual(self.unico(res, "db", "aviso").mensaje, "El esquema fabrica_pruebas no existe en la base de datos.")

    def test_fallo_al_conectar_se_traduce(self):
        db = BaseFalsa(fallo_conexion=ErrorPsycopgFalso("could not connect to server"))
        res = self.prevalidar(conectar_db=db)
        self.assertFalse(res.ok)
        err = self.unico(res, "db", "error")
        self.assertEqual(err.mensaje, "No se pudo conectar a la base de datos.")
        self.assertIn("DB_*", err.accion)
        self.assertIn("could not connect", err.detalle)
        self.assertEqual(db.conexiones, [])  # nunca hubo conexión que cerrar

    def test_fallo_en_la_consulta_cierra_la_conexion(self):
        db = BaseFalsa(fallo_consulta=ErrorPsycopgFalso("permission denied for schema"))
        res = self.prevalidar(conectar_db=db)
        err = self.unico(res, "db", "error")
        self.assertEqual(err.mensaje, "No se pudo conectar a la base de datos.")
        conn = db.conexiones[0]
        self.assertEqual(conn.cerradas, 1)
        self.assertTrue(conn.cursores[0].cerrado)

    def test_fallo_de_red_al_conectar(self):
        db = BaseFalsa(fallo_conexion=TimeoutError("timed out"))
        res = self.prevalidar(conectar_db=db)
        self.assertEqual(self.unico(res, "db", "error").mensaje, "No hay conexión con Google o con la red.")

    def test_fallo_al_conectar_con_simular_es_aviso(self):
        db = BaseFalsa(fallo_conexion=ErrorPsycopgFalso("could not connect"))
        res = self.prevalidar(conectar_db=db, simular=True)
        self.assertTrue(res.ok)
        self.assertEqual(self.unico(res, "db", "aviso").mensaje, "No se pudo conectar a la base de datos.")

    def test_sin_psycopg2_instalado(self):
        # sys.modules["psycopg2"] = None hace que `import psycopg2` lance ImportError.
        with mock.patch.dict(sys.modules, {"psycopg2": None}):
            res = self.prevalidar(conectar_db=None)
        err = self.unico(res, "db", "error")
        self.assertIn("psycopg2", err.mensaje)
        self.assertIn("pip install psycopg2-binary", err.accion)
        with mock.patch.dict(sys.modules, {"psycopg2": None}):
            res = self.prevalidar(conectar_db=None, simular=True)
        self.assertTrue(res.ok)
        self.assertIn("psycopg2", self.unico(res, "db", "aviso").mensaje)

    def test_error_al_cerrar_no_tapa_el_resultado(self):
        class ConexionQueNoCierra(ConexionFalsa):
            def close(self):
                super().close()
                raise RuntimeError("ya cerrada")

        def conectar(**_k):
            return ConexionQueNoCierra(("fabrica",))

        res = self.prevalidar(conectar_db=conectar)
        self.assertTrue(res.ok)


# ---------------------------------------------------------------------------
# Varios problemas a la vez y estructuras
# ---------------------------------------------------------------------------
class TestAcumulado(BasePrevalidacion):
    def test_todos_los_problemas_salen_juntos(self):
        excel = self.excel([CABECERA, ["PRODUCTO", "Bogotá", "noExiste123", RAIZ_A]])
        db = BaseFalsa(fallo_conexion=ErrorPsycopgFalso("x"))
        res = self.prevalidar(excel, env={"DB_HOST": "h", "DB_NAME": "n", "DB_USER": "u", "DB_PASSWORD": "p"}, conectar_db=db)
        self.assertFalse(res.ok)
        self.assertEqual([e.area for e in res.errores()], ["drive", "correo", "db"])
        self.assertEqual(len(self.por_area(res, "token", "ok")), 1)
        self.assertEqual(len(self.por_area(res, "excel", "ok")), 1)

    def test_hallazgo_texto_y_validacion(self):
        self.assertEqual(Hallazgo("error", "db", "Falló.", "Haz esto.").texto(), "ERROR Falló. Haz esto.")
        self.assertEqual(Hallazgo("ok", "excel", "Bien.").texto(), "OK Bien.")
        self.assertEqual(Hallazgo("aviso", "correo", "Ojo.").detalle, "")
        with self.assertRaises(ValueError):
            Hallazgo("fatal", "db", "m")
        with self.assertRaises(ValueError):
            Hallazgo("ok", "sheets", "m")
        self.assertEqual(NIVELES, ("ok", "aviso", "error"))
        self.assertEqual(AREAS, ("excel", "token", "drive", "cliente", "correo", "db"))

    def test_resultado_ok_errores_avisos(self):
        res = ResultadoPrevalidacion()
        self.assertTrue(res.ok)
        self.assertEqual((res.lotes, res.creds, res.svc, res.cuenta, res.carpetas), ([], None, None, "", {}))
        res.agregar("ok", "excel", "bien")
        res.agregar("aviso", "correo", "ojo")
        self.assertTrue(res.ok)
        self.assertEqual([h.mensaje for h in res.avisos()], ["ojo"])
        res.agregar("error", "db", "mal", "arregla", "detalle técnico")
        self.assertFalse(res.ok)
        self.assertEqual([h.mensaje for h in res.errores()], ["mal"])
        self.assertEqual(res.errores()[0].detalle, "detalle técnico")


# ---------------------------------------------------------------------------
# cargar_env
# ---------------------------------------------------------------------------
class TestCargarEnv(unittest.TestCase):
    CLAVES = ("CORREOS_AVISO", "DB_HOST", "PREV_SOLO_RAIZ", "PREV_SOLO_CLON", "PREV_PREVIA")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _escribir(self, relativa: str, contenido: str) -> None:
        ruta = self.root / relativa
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(contenido, encoding="utf-8")

    def test_raiz_gana_y_subcarpetas_completan(self):
        self._escribir(".env", "CORREOS_AVISO=raiz@cun.edu.co\nPREV_SOLO_RAIZ=1\nPREV_PREVIA=archivo\n")
        self._escribir("LMS_Fabrica/.env", "CORREOS_AVISO=lms@cun.edu.co\nDB_HOST=lms-host\n")
        self._escribir("CLONACION_CARPETA/.env", "DB_HOST=clon-host\nPREV_SOLO_CLON=1\n")
        with mock.patch.dict(os.environ):
            for clave in self.CLAVES:
                os.environ.pop(clave, None)
            os.environ["PREV_PREVIA"] = "sistema"
            cargar_env(self.root)
            self.assertEqual(os.environ["CORREOS_AVISO"], "raiz@cun.edu.co")
            self.assertEqual(os.environ["DB_HOST"], "lms-host")
            self.assertEqual(os.environ["PREV_SOLO_RAIZ"], "1")
            self.assertEqual(os.environ["PREV_SOLO_CLON"], "1")
            self.assertEqual(os.environ["PREV_PREVIA"], "sistema")  # el sistema nunca se pisa
        self.assertNotIn("PREV_SOLO_RAIZ", os.environ)

    def test_sin_archivos_env_no_falla(self):
        with mock.patch.dict(os.environ):
            for clave in self.CLAVES:
                os.environ.pop(clave, None)
            cargar_env(self.root)
            self.assertNotIn("CORREOS_AVISO", os.environ)


if __name__ == "__main__":
    unittest.main()
