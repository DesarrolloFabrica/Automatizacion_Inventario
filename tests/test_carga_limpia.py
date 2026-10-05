"""
Carga limpia a fabrica1 (recarga desde cero, 2026-10-02).

Se ejercitan los ayudantes reales de LMS_Fabrica/cargar_base_gcp.py con un
cursor falso: IDs de secuencia, nada de IDs fijos de fabrica, programa por
escuela + nombre, columnas de origen y validación previa de las filas.
"""

from __future__ import annotations

import unittest

from flujo_lib import gcp
from flujo_lib.mensajes import ErrorFlujo
from flujo_lib.verificacion import verificar_lote
from tests.fake_drive import FakeDrive

FILA = {
    "raiz_nombre": "LMS_Carga", "destinatario_codigo": "MEN", "periodo_codigo": "Q2",
    "cliente_nombre": "PRODUCTO", "escuela_nombre": "ESCUELA_DE_INGENIERIA",
    "programa_nombre": "MATEMATICAS", "materia_semestre": "1", "paquete_nombre": "NOTEBOOK",
    "materia_nombre": "CALCULO", "granulo_codigo": "G1", "granulo_nombre": "G1",
    "archivo_nombre": "G1_soyunaimagen.png", "archivo_nombre_original": "G1_soyunaimagenpng",
    "archivo_enlace": "https://drive.google.com/file/d/clon1/view", "archivo_hash": "",
    "archivo_fecha_registro": "2026-10-02T00:00:00+00:00", "archivo_activo": "true",
    "extension_tipo": "png", "origen_id": "origen1", "fecha_origen": "2026-06-11T15:04:05.000Z",
}


class CursorLimpio:
    """Base vacía con secuencias: todo SELECT de dimensión no encuentra nada."""

    def __init__(self, columnas=("origen_id", "fecha_origen"), origenes_cargados=()):
        self.sql: list[tuple[str, tuple]] = []
        self._columnas = columnas
        self._cargados = set(origenes_cargados)
        self._id = 0
        self._uno = None
        self._todos: list = []

    def __enter__(self): return self
    def __exit__(self, *_a): return False

    def execute(self, sql, params=()):
        texto = " ".join(str(sql).split())
        self.sql.append((texto, tuple(params or ())))
        self._uno, self._todos = None, []
        if "information_schema.columns" in texto:
            self._todos = [(c,) for c in self._columnas]
        elif "RETURNING id" in texto:
            self._id += 1
            self._uno = (self._id,)
        elif "WHERE origen_id" in texto:
            self._uno = (99,) if params[0] in self._cargados else None

    def fetchone(self): return self._uno
    def fetchall(self): return list(self._todos)

    def inserts(self, tabla: str) -> list[tuple[str, tuple]]:
        return [(s, p) for s, p in self.sql if s.startswith(f"INSERT INTO fabrica1.{tabla} ")]


class Conexion:
    def __init__(self, cursor): self.cur, self.confirmada, self.cerrada = cursor, False, False
    def __enter__(self): return self
    def __exit__(self, tipo, *_a):
        self.confirmada = tipo is None
        return False
    def cursor(self): return self.cur
    def close(self): self.cerrada = True


def cargar(filas, cursor=None, **kw):
    cursor = cursor or CursorLimpio()
    conexion = Conexion(cursor)
    stats = gcp.cargar_lote(filas, schema="fabrica1", conectar=lambda: conexion, **kw)
    return stats, cursor, conexion


class TestCargaLimpia(unittest.TestCase):
    def tearDown(self):
        carga, _ = gcp._heredados()
        carga.USAR_SECUENCIAS = False  # no contaminar otras pruebas del módulo heredado

    def test_inserta_con_columnas_de_origen_y_sin_id(self):
        stats, cursor, conexion = cargar([FILA])
        self.assertEqual(stats["insertados"], 1)
        self.assertTrue(conexion.confirmada and conexion.cerrada)
        [(sql, params)] = cursor.inserts("archivo")
        self.assertIn("origen_id, fecha_origen", sql)
        self.assertNotIn("(id,", sql.replace(" ", ""))
        self.assertEqual(params[-2:], ("origen1", "2026-06-11T15:04:05.000Z"))
        self.assertEqual(params[6:8], ("G1_soyunaimagen.png", "G1_soyunaimagenpng"))

    def test_los_ids_salen_de_las_secuencias(self):
        _, cursor, _ = cargar([FILA])
        self.assertFalse(any("SELECT COALESCE(MAX(id), 0) + 1" in sql for sql, _ in cursor.sql))
        for tabla in ("escuela", "programa", "paquete", "materia", "granulo", "raiz",
                      "destinatario", "periodo", "cliente", "extension"):
            [(sql, _)] = cursor.inserts(tabla)
            self.assertTrue(sql.endswith("RETURNING id"), sql)
            self.assertNotIn("(id,", sql.replace(" ", ""))

    def test_realinea_las_secuencias_antes_de_insertar(self):
        # Una carga que falló a medias gastó números: se realinean para no dejar huecos.
        _, cursor, _ = cargar([FILA])
        textos = [s for s, _ in cursor.sql]
        primero_insert = next(i for i, s in enumerate(textos) if s.startswith("INSERT"))
        bloqueo = next(i for i, s in enumerate(textos) if s.startswith("LOCK TABLE fabrica1.archivo"))
        realineadas = [i for i, s in enumerate(textos) if s.startswith("SELECT setval(")]
        self.assertEqual(len(realineadas), len(gcp.TABLAS_CON_SECUENCIA))
        self.assertIn("pg_get_serial_sequence('fabrica1.archivo', 'id')", textos[realineadas[0]])
        self.assertTrue(bloqueo < realineadas[0] and realineadas[-1] < primero_insert)

    def test_no_usa_los_ids_fijos_de_fabrica_para_la_extension(self):
        # En fabrica png es el id 5; en fabrica1 la tabla extension arranca vacía.
        _, cursor, _ = cargar([FILA])
        [(_, params)] = cursor.inserts("extension")
        self.assertEqual(params, ("png",))

    def test_programa_se_busca_por_escuela_y_nombre(self):
        _, cursor, _ = cargar([FILA])
        consulta = next(s for s, _ in cursor.sql if s.startswith("SELECT id FROM fabrica1.programa"))
        self.assertIn("nombre = %s AND escuela_id = %s", consulta)

    def test_no_borra_nada(self):
        _, cursor, _ = cargar([FILA], reemplazar=True)
        self.assertFalse(any(s.upper().startswith("DELETE") for s, _ in cursor.sql))

    def test_archivo_de_origen_ya_cargado_se_omite(self):
        stats, cursor, _ = cargar([FILA], CursorLimpio(origenes_cargados={"origen1"}))
        self.assertEqual((stats["insertados"], stats["existentes"]), (0, 1))
        self.assertEqual(cursor.inserts("archivo"), [])

    def test_sin_columnas_nuevas_no_carga(self):
        with self.assertRaises(ErrorFlujo) as ctx:
            cargar([FILA], CursorLimpio(columnas=()))
        self.assertIn("fabrica1_001_origen_archivo.sql", str(ctx.exception))

    def test_fila_incompleta_no_conecta(self):
        def conectar():
            raise AssertionError("no debe conectar")
        with self.assertRaises(ErrorFlujo) as ctx:
            gcp.cargar_lote([{**FILA, "origen_id": ""}], schema="fabrica1", conectar=conectar)
        self.assertIn("no se sabe de qué archivo del origen salió", str(ctx.exception))

    def test_simular_tambien_revisa_las_filas(self):
        with self.assertRaises(ErrorFlujo):
            gcp.cargar_lote([{**FILA, "escuela_nombre": ""}], schema="fabrica1", simular=True)

    def test_el_esquema_anterior_sigue_igual(self):
        self.assertFalse(gcp.es_esquema_limpio("fabrica"))
        self.assertFalse(gcp.es_esquema_limpio("fabrica_pruebas"))
        self.assertTrue(gcp.es_esquema_limpio("fabrica1"))


class TestProblemasFilas(unittest.TestCase):
    def test_fila_completa_no_tiene_problemas(self):
        self.assertEqual(gcp.problemas_filas_limpias([FILA]), [])

    def test_detecta_cada_falta(self):
        casos = {
            "origen_id": "no se sabe de qué archivo del origen salió",
            "fecha_origen": "no se encontró la fecha de subida",
            "escuela_nombre": "falta escuela",
            "materia_nombre": "falta materia",
            "granulo_codigo": "falta código de gránulo",
        }
        for campo, texto in casos.items():
            with self.subTest(campo=campo):
                problemas = gcp.problemas_filas_limpias([{**FILA, campo: ""}])
                self.assertTrue(any(texto in p for p in problemas), problemas)

    def test_mismo_origen_dos_veces(self):
        problemas = gcp.problemas_filas_limpias([FILA, {**FILA, "archivo_nombre": "otro.png"}])
        self.assertTrue(any("el mismo archivo de origen ya viene" in p for p in problemas))

    def test_codigo_largo_se_conserva(self):
        problemas = gcp.problemas_filas_limpias([{**FILA, "granulo_codigo": "ACTIVIDADES_MOODLE"}])
        self.assertEqual(problemas, [])

    def test_codigo_mayor_de_200_no_cabe(self):
        problemas = gcp.problemas_filas_limpias([{**FILA, "granulo_codigo": "A" * 201}])
        self.assertTrue(any("pasa de 200 caracteres" in p for p in problemas))

    def test_extension_mayor_de_10_no_cabe(self):
        problemas = gcp.problemas_filas_limpias([{**FILA, "extension_tipo": "A" * 11}])
        self.assertTrue(any("pasa de 10 caracteres" in p for p in problemas))


class TestEscuelaOficial(unittest.TestCase):
    """En fabrica1 solo hay 5 escuelas, todas ESCUELA_DE_…; las variantes de Drive van a esas."""

    def escanear(self, escuela: str, con_origen: bool) -> list[dict]:
        import types
        from unittest import mock
        fake = FakeDrive()
        origen = fake.agregar_carpeta("PROGRAMA")
        rutas = types.SimpleNamespace(escanear_ruta_drive=lambda *_a: [
            {"escuela": escuela, "archivo_nombre": "G1.pdf", "archivo_enlace": "https://drive/1"}])
        lote = types.SimpleNamespace(cliente_gcp="TANIA", raiz_gcp="LMS_Carga", escuela_gcp="", origen_id=origen)
        with mock.patch.object(gcp, "_heredados", return_value=(None, rutas)):
            return gcp.escanear_lote(fake, "DEST", lote, "PROGRAMA", con_origen=con_origen)

    # Nombres reales del Drive origen y de la fabrica anterior (ya pasados por norm_text).
    VARIANTES = {
        "ESCUELA_CIENCIAS_SOCIALES_JURIDICAS_Y_GOBIERNO": "ESCUELA_DE_CIENCIAS_SOCIALES_JURIDICAS_Y_GOBIERNO",
        "ESCUELA_DE_CIENCIAS_SOCIALES_JURIDICAS_Y_DE_GOBIERNO": "ESCUELA_DE_CIENCIAS_SOCIALES_JURIDICAS_Y_GOBIERNO",
        "ESCUELA_DE_DISENO_Y_COMUNICACION": "ESCUELA_DE_DISENO_Y_COMUNICACION",
        "DISENO_Y_COMUNICACION": "ESCUELA_DE_DISENO_Y_COMUNICACION",
        "ESCUELA_DE_INGENIERIA": "ESCUELA_DE_INGENIERIA",
        "ESCUELA_SALUD_Y_BIENESTAR": "ESCUELA_DE_SALUD_Y_BIENESTAR",
        "ESCUELA_TRANSFORMACION_EMPRESARIAL": "ESCUELA_DE_TRANSFORMACION_EMPRESARIAL",
        "ESCUELA_DE_TRANSFORMACION_EMPRESARIAL": "ESCUELA_DE_TRANSFORMACION_EMPRESARIAL",
    }

    def test_cada_variante_va_a_su_nombre_oficial(self):
        for variante, oficial in self.VARIANTES.items():
            with self.subTest(variante=variante):
                self.assertEqual(gcp.escuela_oficial(variante), oficial)

    def test_quedan_exactamente_cinco(self):
        self.assertEqual(len({gcp.escuela_oficial(v) for v in self.VARIANTES}), 5)
        self.assertEqual(set(gcp.ESCUELAS_OFICIALES), set(self.VARIANTES.values()))

    def test_el_escaneo_limpio_usa_el_nombre_oficial(self):
        filas = self.escanear("ESCUELA_SALUD_Y_BIENESTAR", con_origen=True)
        self.assertEqual(filas[0]["escuela_nombre"], "ESCUELA_DE_SALUD_Y_BIENESTAR")

    def test_el_esquema_anterior_no_se_toca(self):
        filas = self.escanear("ESCUELA_SALUD_Y_BIENESTAR", con_origen=False)
        self.assertEqual(filas[0]["escuela_nombre"], "ESCUELA_SALUD_Y_BIENESTAR")

    def test_escuela_desconocida_no_se_carga(self):
        problemas = gcp.problemas_filas_limpias([{**FILA, "escuela_nombre": "ESCUELA_DE_ARTES"}])
        self.assertTrue(any("no es una de las oficiales" in p for p in problemas), problemas)


class TestInventarioOrigen(unittest.TestCase):
    def test_recorre_subcarpetas_y_trae_la_fecha_de_subida(self):
        fake = FakeDrive()
        raiz = fake.agregar_carpeta("PROGRAMA")
        sub = fake.agregar_carpeta("SEMESTRE I", raiz)
        a = fake.agregar_archivo("G1_x.pdf", raiz, creado="2026-06-11T15:04:05.000Z")
        b = fake.agregar_archivo("G2_ypng", sub, mime="image/png")
        inventario = gcp.inventario_origen(fake, raiz)
        self.assertEqual(set(inventario), {a, b})
        self.assertEqual(inventario[a][gcp.CAMPO_FECHA_ORIGEN], "2026-06-11T15:04:05.000Z")
        self.assertEqual(inventario[b]["name"], "G2_ypng")


class TestVerificacionVinculo(unittest.TestCase):
    """En fabrica1 cada copia del clon debe saber de qué archivo del origen salió."""

    def setUp(self):
        self.fake = FakeDrive()
        self.origen = self.fake.agregar_carpeta("Programa")
        self.clon = self.fake.agregar_carpeta("Programa clon")

    def verificar(self, **kw):
        return verificar_lote(self.fake, self.origen, self.clon, programa="Programa", meta={},
                              parser=lambda *_a: {"ok": True}, **kw)

    def copiar(self, origen_id: str, nombre: str, *, vincular: bool = True) -> str:
        cuerpo = {"name": nombre, "parents": [self.clon]}
        if vincular:
            cuerpo["properties"] = {"origen_id": origen_id}
        return self.fake.files().copy(fileId=origen_id, body=cuerpo, fields="id").execute()["id"]

    def test_copia_vinculada_y_normalizada_es_ok(self):
        o = self.fake.agregar_archivo("G1_soyunaimagenpng", self.origen, mime="image/png")
        self.copiar(o, "G1_soyunaimagen.png")
        resultado = self.verificar(exigir_vinculo=True)
        self.assertEqual(resultado.estado, "ok", resultado.hallazgos)

    def test_copia_sin_vinculo_es_diferencia(self):
        o = self.fake.agregar_archivo("G1_x.pdf", self.origen)
        self.copiar(o, "G1_x.pdf", vincular=False)
        self.assertEqual(self.verificar().estado, "ok")  # esquema anterior: no se exige
        resultado = self.verificar(exigir_vinculo=True)
        self.assertTrue(any("Sin vínculo con el origen" in h for h in resultado.hallazgos), resultado.hallazgos)

    def test_vinculo_a_otro_lote_es_diferencia(self):
        o = self.fake.agregar_archivo("G1_x.pdf", self.origen)
        ajeno = self.fake.agregar_archivo("G1_x.pdf", self.fake.agregar_carpeta("Otro"))
        self.copiar(ajeno, "G1_x.pdf")
        self.fake.files().update(fileId=o, body={}, fields="id").execute()
        resultado = self.verificar(exigir_vinculo=True)
        self.assertTrue(any("no corresponde a este lote" in h for h in resultado.hallazgos), resultado.hallazgos)

    def test_nombre_sin_normalizar_es_diferencia(self):
        o = self.fake.agregar_archivo("G1_x.png", self.origen, mime="application/pdf")
        self.copiar(o, "G1_x.png")  # quedó con el nombre viejo
        resultado = self.verificar()
        self.assertTrue(any("Nombre sin normalizar" in h and "G1_x.pdf" in h for h in resultado.hallazgos),
                        resultado.hallazgos)

    def test_dos_archivos_iguales_con_el_mismo_nombre_en_el_origen(self):
        # Caso real (DIPLOMADO_EN_CONSTRUCCION_DE_PAZ, 2026-10-05): el mismo banner subido dos veces.
        a = self.fake.agregar_archivo("P_MODULO_3.png", self.origen, contenido=b"banner", mime="image/png")
        b = self.fake.agregar_archivo("P_MODULO_3.png", self.origen, contenido=b"banner", mime="image/png")
        self.copiar(a, "P_MODULO_3.png")
        self.copiar(b, "P_MODULO_3.png")
        resultado = self.verificar(exigir_vinculo=True)
        self.assertEqual(resultado.estado, "ok", resultado.hallazgos)

    def test_falta_una_de_dos_copias_iguales(self):
        a = self.fake.agregar_archivo("P_MODULO_3.png", self.origen, contenido=b"banner", mime="image/png")
        self.fake.agregar_archivo("P_MODULO_3.png", self.origen, contenido=b"banner", mime="image/png")
        self.copiar(a, "P_MODULO_3.png")
        resultado = self.verificar(exigir_vinculo=True)
        self.assertTrue(any("Falta en el clon: P_MODULO_3.png (1)" in h for h in resultado.hallazgos), resultado.hallazgos)

    def test_dos_archivos_que_quedan_con_el_mismo_nombre(self):
        self.fake.agregar_archivo("G1_x.png", self.origen, mime="image/png")
        self.fake.agregar_archivo("G1_xpng", self.origen, mime="image/png")
        resultado = self.verificar()
        self.assertTrue(any("Nombre repetido al normalizar" in h for h in resultado.hallazgos), resultado.hallazgos)


if __name__ == "__main__":
    unittest.main()
