"""
Prueba de extremo a extremo del run único (fase 4).

Recorre el flujo completo sobre un Drive simulado: prevalidación, carpeta
destino, clonación, conversión JPG→PNG, verificación, compuerta, carga a Cloud
SQL, inventario y correo final.

Solo se sustituye lo que saldría a la red: la sesión de Google, la conexión a
la base de datos, la publicación en Google Sheets y el envío por Gmail. Todo lo
demás se ejecuta de verdad, incluidos los módulos heredados de LMS_Fabrica y
CLONACION_CARPETA.

Escenario (dos lotes que comparten la misma carpeta raíz de destino):
  - Lote «Bogotá»: material correcto, con un JPG que debe quedar en PNG.
  - Lote «Medellín»: un PDF dentro de PORTADA MATERIA (mal ubicado), que la
    verificación detecta y la compuerta retiene antes de cargar.
"""

from __future__ import annotations

import base64, contextlib, email, functools, io, json, os, tempfile, unittest
from pathlib import Path
from unittest import mock

import openpyxl
from PIL import Image

import run_flujo
from flujo_lib import clonacion, gcp
from tests.fake_drive import FakeDrive
from tests.test_prevalidacion import BaseFalsa

ORIGEN_A = "origenBogota01"
ORIGEN_B = "origenMedellin02"
RAIZ = "raizLmsCarga01"
NOMBRE_A = "Bogotá 2026"
NOMBRE_B = "Medellín 2026"
CABECERA = ["cliente", "etiqueta", "origen", "destino"]
ENLACE_SHEET = "https://docs.google.com/spreadsheets/d/inventario-falso/edit"
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
MIME_FOLDER = "application/vnd.google-apps.folder"


def _url(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}?usp=sharing"


def _jpeg(color: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    """JPEG real: la conversión usa Pillow de verdad, no un contenido inventado."""
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buffer, format="JPEG")
    return buffer.getvalue()


def _rutas(fake: FakeDrive, raiz_id: str, prefijo: str = "") -> dict[str, str]:
    """{ruta relativa: md5 o "<carpeta>"} de todo lo visible bajo raiz_id."""
    salida: dict[str, str] = {}
    for hijo in fake.hijos(raiz_id):
        ruta = f"{prefijo}/{hijo['name']}" if prefijo else hijo["name"]
        if hijo["mimeType"] == MIME_FOLDER:
            salida[ruta] = "<carpeta>"
            salida.update(_rutas(fake, hijo["id"], ruta))
        else:
            salida[ruta] = hijo["md5Checksum"]
    return salida


# ---------------------------------------------------------------------------
# Base de datos simulada: responde lo que pregunta cargar_base_gcp
# ---------------------------------------------------------------------------
class CursorFalso:
    """
    Responde lo que consulta la carga: siguiente id, existencia por enlace,
    conteo previo por programa y borrado de reemplazo.

    `archivos_previos` simula lo que ya hay cargado: {programa: cuántos}.
    """

    def __init__(
        self,
        enlaces_existentes: tuple[str, ...] = (),
        archivos_previos: dict[str, int] | None = None,
    ):
        self.sql: list[tuple[str, tuple]] = []
        self.inserciones: list[tuple[str, tuple]] = []
        self.borrados: list[tuple[str, ...]] = []
        self.rowcount = 0
        self._existentes = tuple(enlaces_existentes)
        self._previos = dict(archivos_previos or {})
        self._resultado: tuple | None = None
        self._filas: list[tuple] = []
        self._id = 0

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def execute(self, sql, params=()):
        texto = " ".join(str(sql).split())
        parametros = tuple(params or ())
        self.sql.append((texto, parametros))
        self._resultado = None
        self._filas = []
        mayus = texto.upper()

        if mayus.startswith("DELETE FROM"):
            programas = tuple(parametros[0] or ()) if parametros else ()
            self.borrados.append(programas)
            self.rowcount = sum(self._previos.pop(p, 0) for p in programas)
        elif mayus.startswith("INSERT INTO"):
            self.inserciones.append((texto, parametros))
        elif "GROUP BY P.NOMBRE" in mayus:  # conteo previo por programa
            programas = tuple(parametros[0] or ()) if parametros else ()
            self._filas = [(p, "PRODUCTO", self._previos[p]) for p in programas if p in self._previos]
        elif "COALESCE(MAX(id)" in texto:
            self._id += 1
            self._resultado = (self._id,)
        elif ".archivo WHERE enlace" in texto:
            patron = str(parametros[0]) if parametros else ""
            literal = patron.replace("%", "")
            self._resultado = next(
                ((7,) for e in self._existentes if e == patron or (literal and literal in e)),
                None,
            )
        # el resto (dimensiones) devuelve None: en esta prueba siempre son nuevas

    def fetchone(self):
        return self._resultado

    def fetchall(self):
        return list(self._filas)

    def archivos_insertados(self) -> list[str]:
        """Nombres de archivo de cada INSERT en la tabla archivo."""
        return [p[7] for sql, p in self.inserciones if ".archivo (" in sql]

    def programas_borrados(self) -> list[str]:
        return sorted({p for grupo in self.borrados for p in grupo})


class ConexionFalsa:
    def __init__(
        self,
        enlaces_existentes: tuple[str, ...] = (),
        archivos_previos: dict[str, int] | None = None,
    ):
        self.cursor_obj = CursorFalso(enlaces_existentes, archivos_previos)
        self.cerrada = self.confirmada = self.revertida = False

    def __enter__(self):
        return self

    def __exit__(self, tipo, *_a):
        self.confirmada = tipo is None
        self.revertida = tipo is not None
        return False

    def cursor(self):
        return self.cursor_obj

    def close(self):
        self.cerrada = True


# ---------------------------------------------------------------------------
# Gmail simulado
# ---------------------------------------------------------------------------
class _Envio:
    def __init__(self, respuesta: dict):
        self._respuesta = respuesta

    def execute(self, http=None, num_retries: int = 0):
        return self._respuesta


class GmailFalso:
    """Mínimo compatible con users().messages().send(...).execute()."""

    def __init__(self):
        self.enviados: list[dict] = []

    def users(self):
        return self

    def messages(self):
        return self

    def send(self, userId=None, body=None):
        self.enviados.append(body or {})
        return _Envio({"id": f"msg-{len(self.enviados)}"})

    def mensajes(self) -> list[email.message.Message]:
        return [
            email.message_from_bytes(base64.urlsafe_b64decode(b["raw"]))
            for b in self.enviados
        ]


# ---------------------------------------------------------------------------
# Escenario
# ---------------------------------------------------------------------------
class BaseExtremoAExtremo(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.dir_corridas = self.dir / "corridas"
        self.fake = FakeDrive()
        self.jpeg = _jpeg()

        # Lote A: estructura completa y correcta, con un JPG por convertir.
        self.fake.agregar_carpeta(NOMBRE_A, id=ORIGEN_A)
        sem_a = self.fake.agregar_carpeta("SEMESTRE I", ORIGEN_A)
        paq_a = self.fake.agregar_carpeta("NOTEBOOK", sem_a)
        mat_a = self.fake.agregar_carpeta("01. MATEMATICAS", paq_a)
        self.fake.agregar_archivo("G1001_intro.pdf", mat_a, contenido=b"intro-pdf")
        portada_a = self.fake.agregar_carpeta("PORTADA MATERIA", mat_a)
        self.fake.agregar_archivo(
            "G1002_portada.JPG", portada_a, contenido=self.jpeg, mime="image/jpeg"
        )
        moodle_a = self.fake.agregar_carpeta("ACTIVIDADES MOODLE", mat_a)
        self.fake.agregar_archivo(
            "01_Quiz.txt", moodle_a, contenido=b"quiz", mime="text/plain"
        )

        # Lote B: un PDF dentro de PORTADA MATERIA, que solo admite PNG.
        self.fake.agregar_carpeta(NOMBRE_B, id=ORIGEN_B)
        sem_b = self.fake.agregar_carpeta("SEMESTRE II", ORIGEN_B)
        paq_b = self.fake.agregar_carpeta("NOTEBOOK", sem_b)
        mat_b = self.fake.agregar_carpeta("02. FISICA", paq_b)
        self.fake.agregar_archivo("G2001_guia.pdf", mat_b, contenido=b"guia-pdf")
        portada_b = self.fake.agregar_carpeta("PORTADA MATERIA", mat_b)
        self.fake.agregar_archivo("G2002_portada.pdf", portada_b, contenido=b"portada-pdf")

        self.fake.agregar_carpeta("LMS_Carga", id=RAIZ)
        self.origen_antes = {
            ORIGEN_A: _rutas(self.fake, ORIGEN_A),
            ORIGEN_B: _rutas(self.fake, ORIGEN_B),
        }

        self.conexion = ConexionFalsa()
        self.gmail = GmailFalso()
        self.salida = io.StringIO()

        def espia_clonar(*a, **kw):
            kw.setdefault("dormir", SILENCIO)  # nunca esperar de verdad
            return clonacion.clonar_arbol(*a, **kw)

        parches = [
            mock.patch("flujo_lib.prevalidacion.drive.cargar_credenciales", return_value=CREDS),
            mock.patch("flujo_lib.prevalidacion.drive.construir_servicio", lambda _c: self.fake),
            mock.patch(
                "flujo_lib.prevalidacion._conectar_psycopg2",
                BaseFalsa(esquemas=("fabrica", "fabrica_pruebas")),
            ),
            mock.patch("run_flujo.clonar_arbol", espia_clonar),
            # Carga real, con una conexión simulada en lugar de Cloud SQL.
            mock.patch(
                "run_flujo.cargar_lote",
                functools.partial(gcp.cargar_lote, conectar=lambda: self.conexion),
            ),
            # Única parte de Google que no se ejercita: publicar la hoja.
            mock.patch("run_flujo.publicar_inventario", return_value=ENLACE_SHEET),
            mock.patch("flujo_lib.notificar.build", lambda *_a, **_k: self.gmail),
            mock.patch.dict(os.environ, ENV_OK),
        ]
        for parche in parches:
            parche.start()
            self.addCleanup(parche.stop)

    def tearDown(self):
        self._tmp.cleanup()

    # ----- ayudas ---------------------------------------------------------
    def excel(self, nombre: str = "RUTAS.xlsx") -> Path:
        libro = openpyxl.Workbook()
        hoja = libro.active
        for fila in (
            CABECERA,
            ["PRODUCTO", "Bogotá", _url(ORIGEN_A), _url(RAIZ)],
            ["TANIA", "Medellín", ORIGEN_B, RAIZ],
        ):
            hoja.append(fila)
        ruta = self.dir / nombre
        libro.save(ruta)
        libro.close()
        return ruta

    def correr(self, excel: Path, *extra: str) -> int:
        argv = ["--excel", str(excel), "--dir-corridas", str(self.dir_corridas), *extra]
        with contextlib.redirect_stdout(self.salida):
            return run_flujo.main(argv)

    def estado(self) -> dict:
        return json.loads((self.dir_corridas / "RUTAS.estado.json").read_text(encoding="utf-8"))

    def pasos(self, origen_id: str) -> dict[str, str]:
        lote = self.estado()["lotes"][f"{origen_id}|{RAIZ}"]
        return {paso: valor["estado"] for paso, valor in lote["pasos"].items()}

    def clon(self, nombre: str) -> dict:
        carpetas = [
            h
            for h in self.fake.hijos(RAIZ)
            if h["name"] == nombre and h["mimeType"] == MIME_FOLDER
        ]
        self.assertEqual(len(carpetas), 1, f"se esperaba una sola carpeta «{nombre}» en la raíz")
        return carpetas[0]


class TestRecorridoCompleto(BaseExtremoAExtremo):
    def test_lote_correcto_carga_y_lote_mal_ubicado_queda_retenido(self):
        codigo = self.correr(self.excel())
        self.assertEqual(codigo, 2, self.salida.getvalue())  # 2 = hay pendientes

        # Lote A: recorrido completo hasta la carga.
        self.assertEqual(
            self.pasos(ORIGEN_A),
            {
                "destino": "ok",
                "clonacion": "ok",
                "formato": "ok",
                "verificacion": "ok",
                "carga": "ok",
            },
        )
        # Lote B: la compuerta lo detiene justo antes de cargar.
        pasos_b = self.pasos(ORIGEN_B)
        self.assertEqual(pasos_b["clonacion"], "ok")
        self.assertEqual(pasos_b["formato"], "ok")
        self.assertEqual(pasos_b["verificacion"], "con_diferencias")
        self.assertEqual(pasos_b["carga"], "omitido")

    def test_el_clon_queda_en_png_y_el_origen_intacto(self):
        self.correr(self.excel())
        rutas = _rutas(self.fake, self.clon(NOMBRE_A)["id"])

        base = "SEMESTRE I/NOTEBOOK/01. MATEMATICAS"
        self.assertIn(f"{base}/PORTADA MATERIA/G1002_portada.png", rutas)
        self.assertNotIn(f"{base}/PORTADA MATERIA/G1002_portada.JPG", rutas)
        self.assertIn(f"{base}/G1001_intro.pdf", rutas)
        self.assertIn(f"{base}/ACTIVIDADES MOODLE/01_Quiz.txt", rutas)

        # El PNG es una imagen de verdad, no el JPG renombrado.
        png = next(
            h
            for h in self.fake.hijos(
                next(
                    c["id"]
                    for c in self.fake.hijos(self._carpeta_materia())
                    if c["name"] == "PORTADA MATERIA"
                )
            )
            if h["name"] == "G1002_portada.png"
        )
        contenido = self.fake.contenido(png["id"])
        self.assertTrue(contenido.startswith(b"\x89PNG"))
        with Image.open(io.BytesIO(contenido)) as imagen:
            self.assertEqual(imagen.format, "PNG")

        # El origen no se tocó en ningún paso.
        self.assertEqual(_rutas(self.fake, ORIGEN_A), self.origen_antes[ORIGEN_A])
        self.assertEqual(_rutas(self.fake, ORIGEN_B), self.origen_antes[ORIGEN_B])

    def _carpeta_materia(self) -> str:
        clon = self.clon(NOMBRE_A)["id"]
        sem = next(c["id"] for c in self.fake.hijos(clon) if c["name"] == "SEMESTRE I")
        paq = next(c["id"] for c in self.fake.hijos(sem) if c["name"] == "NOTEBOOK")
        return next(c["id"] for c in self.fake.hijos(paq) if c["name"] == "01. MATEMATICAS")

    def test_solo_se_cargan_los_archivos_del_lote_verificado(self):
        self.correr(self.excel())
        insertados = self.conexion.cursor_obj.archivos_insertados()
        self.assertCountEqual(
            insertados, ["G1001_intro.pdf", "G1002_portada.png", "01_Quiz.txt"]
        )
        self.assertNotIn("G2001_guia.pdf", insertados)
        self.assertTrue(self.conexion.confirmada)
        self.assertTrue(self.conexion.cerrada)

    def test_la_verificacion_explica_el_archivo_mal_ubicado(self):
        self.correr(self.excel())
        detalle = self.estado()["lotes"][f"{ORIGEN_B}|{RAIZ}"]["pasos"]["verificacion"]["detalle"]
        self.assertIn("PORTADA MATERIA", detalle)
        self.assertIn("G2002_portada.pdf", detalle)
        self.assertIn("png", detalle)

    def test_un_solo_correo_con_el_resumen_y_el_estado_adjunto(self):
        self.correr(self.excel())
        self.assertEqual(len(self.gmail.enviados), 1)
        mensaje = self.gmail.mensajes()[0]

        self.assertIn("pendientes", mensaje["Subject"].lower())
        self.assertIn("ana@cun.edu.co", mensaje["To"])
        cuerpo = next(
            p.get_payload(decode=True).decode("utf-8")
            for p in mensaje.walk()
            if p.get_content_type() == "text/html"
        )
        self.assertIn("Bogotá", cuerpo)
        self.assertIn("Medellín", cuerpo)
        self.assertIn("Con diferencias", cuerpo)
        self.assertIn(ENLACE_SHEET, cuerpo)
        self.assertIn("SELECT", cuerpo)  # consulta SQL para validar el lote

        adjuntos = [p.get_filename() for p in mensaje.walk() if p.get_filename()]
        self.assertEqual(adjuntos, ["RUTAS.estado.xlsx"])

    def test_deja_estado_inventario_y_log_en_la_carpeta_de_corridas(self):
        self.correr(self.excel())
        self.assertTrue((self.dir_corridas / "RUTAS.estado.json").is_file())
        self.assertTrue((self.dir_corridas / "RUTAS.estado.xlsx").is_file())
        self.assertTrue((self.dir_corridas / "RUTAS.inventario.xlsx").is_file())
        self.assertEqual(len(list((self.dir_corridas / "logs").glob("*.log"))), 1)
        self.assertEqual(self.estado()["inventario"]["sheet"], ENLACE_SHEET)


class TestCompuertaYReanudacion(BaseExtremoAExtremo):
    def test_forzar_carga_sube_tambien_el_lote_con_diferencias(self):
        codigo = self.correr(self.excel(), "--forzar-carga")
        self.assertEqual(codigo, 2, self.salida.getvalue())  # sigue habiendo diferencias
        self.assertEqual(self.pasos(ORIGEN_B)["verificacion"], "con_diferencias")
        self.assertEqual(self.pasos(ORIGEN_B)["carga"], "ok")
        self.assertIn("G2001_guia.pdf", self.conexion.cursor_obj.archivos_insertados())

    def test_simular_no_escribe_en_la_base_ni_envia_correo(self):
        codigo = self.correr(self.excel(), "--simular")
        self.assertEqual(codigo, 2, self.salida.getvalue())
        self.assertEqual(self.conexion.cursor_obj.inserciones, [])
        self.assertEqual(self.gmail.enviados, [])
        self.assertIn("Simulación", self.estado()["lotes"][f"{ORIGEN_A}|{RAIZ}"]["pasos"]["carga"]["detalle"])

    def test_segunda_corrida_no_duplica_nada(self):
        excel = self.excel()
        self.assertEqual(self.correr(excel), 2)
        rutas_primera = _rutas(self.fake, RAIZ)
        insertados_primera = list(self.conexion.cursor_obj.archivos_insertados())

        self.assertEqual(self.correr(excel), 2, self.salida.getvalue())
        self.assertEqual(_rutas(self.fake, RAIZ), rutas_primera)  # ni copias ni PNG de más
        self.assertEqual(self.conexion.cursor_obj.archivos_insertados(), insertados_primera)
        self.assertEqual(len(self.gmail.enviados), 2)  # un correo por corrida
        self.assertEqual(len(self.estado()["corridas"]), 2)


if __name__ == "__main__":
    unittest.main()
