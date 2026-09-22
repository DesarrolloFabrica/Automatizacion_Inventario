"""Pruebas de flujo_lib.clonacion con el Drive falso. Sin red y sin esperas reales."""

from __future__ import annotations

import unittest

from googleapiclient.errors import HttpError

from flujo_lib import clonacion, drive
from tests.fake_drive import MIME_FOLDER, FakeDrive, hacer_http_error

SILENCIO = lambda *_a, **_k: None  # noqa: E731


def _arbol(fake: FakeDrive, raiz_id: str, prefijo: str = "") -> dict[str, str]:
    """ruta relativa -> md5 (archivos) o "carpeta". Solo lo que no está en papelera."""
    salida: dict[str, str] = {}
    for h in fake.hijos(raiz_id):
        ruta = f"{prefijo}/{h['name']}" if prefijo else h["name"]
        if h["mimeType"] == MIME_FOLDER:
            salida[ruta + "/"] = "carpeta"
            salida.update(_arbol(fake, h["id"], ruta))
        else:
            salida[ruta] = h["md5Checksum"]
    return salida


def _foto(fake: FakeDrive, raiz_id: str) -> list[dict]:
    """Snapshot completo (registros con id, nombre, padres, trashed, md5) de un subárbol."""
    salida = []
    for h in fake.hijos(raiz_id, incluir_papelera=True):
        salida.append(h)
        if h["mimeType"] == MIME_FOLDER:
            salida.extend(_foto(fake, h["id"]))
    return sorted(salida, key=lambda r: r["id"])


def _en_papelera(fake: FakeDrive, raiz_id: str) -> set[str]:
    return {r["id"] for r in _foto(fake, raiz_id) if r["trashed"]}


def _clonar(fake: FakeDrive, origen: str, destino: str, **kw):
    kw.setdefault("log", SILENCIO)
    kw.setdefault("dormir", SILENCIO)
    return clonacion.clonar_arbol(fake, origen, destino, **kw)


def _armar_origen(fake: FakeDrive) -> tuple[str, int, int]:
    """
    Árbol de 3 niveles con carpetas vacías y nombres repetidos en carpetas distintas.
    Devuelve (id_origen, n_carpetas, n_archivos).
    """
    origen = fake.agregar_carpeta("ORIGEN")
    fake.agregar_archivo("readme.pdf", origen, contenido=b"raiz")
    fake.agregar_archivo("pieza_01.JPG", origen, contenido=b"jpg-raiz", mime="image/jpeg")
    a = fake.agregar_carpeta("A", origen)
    fake.agregar_archivo("readme.pdf", a, contenido=b"nivel-a")
    b = fake.agregar_carpeta("B", a)
    fake.agregar_archivo("readme.pdf", b, contenido=b"nivel-b")
    fake.agregar_archivo("Guía final.docx", b, contenido=b"docx")
    fake.agregar_carpeta("C vacía", b)
    fake.agregar_carpeta("Vacía", origen)
    return origen, 4, 5


class TestClonacionCompleta(unittest.TestCase):
    def test_a_arbol_de_tres_niveles_queda_identico(self):
        fake = FakeDrive()
        origen, n_carpetas, n_archivos = _armar_origen(fake)
        destino = fake.agregar_carpeta("DESTINO")
        foto_antes = _foto(fake, origen)
        lineas: list[str] = []

        resumen = _clonar(fake, origen, destino, log=lineas.append)

        self.assertEqual(_arbol(fake, destino), _arbol(fake, origen))
        self.assertEqual(_foto(fake, origen), foto_antes)  # el origen no cambió
        self.assertTrue(resumen.ok())
        self.assertEqual(resumen.carpetas_nuevas, n_carpetas)
        self.assertEqual(resumen.carpetas_reusadas, 0)
        self.assertEqual(resumen.archivos_copiados, n_archivos)
        self.assertEqual(resumen.archivos_omitidos, 0)
        self.assertEqual(resumen.archivos_fallidos, 0)
        self.assertEqual(resumen.fallidos, [])
        self.assertEqual(resumen.carpetas_duplicadas_quitadas, 0)
        self.assertEqual(resumen.archivos_duplicados_quitados, 0)
        self.assertEqual(fake.llamadas["copy"], n_archivos)
        self.assertEqual(fake.llamadas["create"], n_carpetas)
        self.assertEqual(fake.llamadas["update"], 0)
        # Mismas líneas informativas del script original.
        self.assertIn("  [carpeta] A", lineas)
        self.assertIn("  [archivo] readme.pdf", lineas)
        self.assertIn("  [carpeta] C vacía", lineas)
        self.assertNotIn("  [carpeta] A (reanudar)", lineas)
        # Los archivos copiados conservan nombre y contenido.
        clon_a = [h for h in fake.hijos(destino) if h["name"] == "A"][0]
        readme_a = [h for h in fake.hijos(clon_a["id"]) if h["name"] == "readme.pdf"][0]
        self.assertEqual(fake.contenido(readme_a["id"]), b"nivel-a")
        self.assertIn("5 archivo(s) copiado(s)", resumen.texto())

    def test_b_segunda_pasada_no_copia_nada(self):
        fake = FakeDrive()
        origen, n_carpetas, n_archivos = _armar_origen(fake)
        destino = fake.agregar_carpeta("DESTINO")
        _clonar(fake, origen, destino)
        copias_antes = fake.llamadas["copy"]
        creaciones_antes = fake.llamadas["create"]
        lineas: list[str] = []

        resumen = _clonar(fake, origen, destino, log=lineas.append)

        self.assertEqual(resumen.archivos_copiados, 0)
        self.assertEqual(resumen.archivos_omitidos, n_archivos)
        self.assertEqual(resumen.carpetas_reusadas, n_carpetas)
        self.assertEqual(resumen.carpetas_nuevas, 0)
        self.assertEqual(resumen.archivos_duplicados_quitados, 0)
        self.assertEqual(resumen.carpetas_duplicadas_quitadas, 0)
        self.assertTrue(resumen.ok())
        self.assertEqual(fake.llamadas["copy"], copias_antes)
        self.assertEqual(fake.llamadas["create"], creaciones_antes)
        self.assertEqual(fake.llamadas["update"], 0)
        self.assertEqual(_arbol(fake, destino), _arbol(fake, origen))
        self.assertIn("  [archivo] readme.pdf (ya existe, omito)", lineas)
        self.assertIn("  [carpeta] A (reanudar)", lineas)

    def test_c_duplicados_de_mas_van_a_la_papelera_por_cupo(self):
        fake = FakeDrive()
        origen = fake.agregar_carpeta("ORIGEN")
        fake.agregar_archivo("a.pdf", origen, contenido=b"a")
        sub = fake.agregar_carpeta("Sub", origen)
        fake.agregar_archivo("b.pdf", sub, contenido=b"b")
        destino = fake.agregar_carpeta("DESTINO")
        a1 = fake.agregar_archivo("a.pdf", destino, contenido=b"a")
        a2 = fake.agregar_archivo("a.pdf", destino, contenido=b"a")  # copia de más
        sub1 = fake.agregar_carpeta("Sub", destino)
        sub2 = fake.agregar_carpeta("Sub", destino)  # subcarpeta de más
        lineas: list[str] = []

        resumen = _clonar(fake, origen, destino, log=lineas.append)

        self.assertEqual(_en_papelera(fake, destino), {a2, sub2})
        self.assertFalse(fake.obtener(a1)["trashed"])
        self.assertFalse(fake.obtener(sub1)["trashed"])
        self.assertEqual(resumen.archivos_duplicados_quitados, 1)
        self.assertEqual(resumen.carpetas_duplicadas_quitadas, 1)
        self.assertEqual(resumen.archivos_omitidos, 1)
        self.assertEqual(resumen.archivos_copiados, 1)  # b.pdf dentro de la Sub conservada
        self.assertEqual(resumen.carpetas_reusadas, 1)
        self.assertEqual([h["name"] for h in fake.hijos(sub1)], ["b.pdf"])
        self.assertEqual(_arbol(fake, destino), _arbol(fake, origen))
        self.assertIn("  [quitar duplicado] a.pdf", lineas)
        self.assertIn("  [quitar carpeta duplicada] Sub", lineas)
        self.assertEqual(_en_papelera(fake, origen), set())

    def test_d_equivalencia_jpg_png_no_copia_ni_borra(self):
        fake = FakeDrive()
        origen = fake.agregar_carpeta("ORIGEN")
        fake.agregar_archivo("pieza_01.JPG", origen, contenido=b"jpg1", mime="image/jpeg")
        fake.agregar_archivo("foto.jpeg", origen, contenido=b"jpg2", mime="image/jpeg")
        destino = fake.agregar_carpeta("DESTINO")
        png1 = fake.agregar_archivo("pieza_01.png", destino, contenido=b"png1", mime="image/png")
        png2 = fake.agregar_archivo("foto.png", destino, contenido=b"png2", mime="image/png")
        lineas: list[str] = []

        resumen = _clonar(fake, origen, destino, log=lineas.append)

        self.assertEqual(resumen.archivos_copiados, 0)
        self.assertEqual(resumen.archivos_omitidos, 2)
        self.assertEqual(resumen.archivos_duplicados_quitados, 0)
        self.assertTrue(resumen.ok())
        self.assertEqual(fake.llamadas["copy"], 0)
        self.assertEqual(fake.llamadas["update"], 0)
        self.assertEqual({h["id"] for h in fake.hijos(destino)}, {png1, png2})
        self.assertEqual(_en_papelera(fake, destino), set())
        self.assertIn("  [archivo] pieza_01.JPG (ya existe, omito)", lineas)
        self.assertIn("  [archivo] foto.jpeg (ya existe, omito)", lineas)

    def test_d2_jpg_pendiente_se_copia_con_su_nombre_original(self):
        # Solo uno de los dos JPG ya tiene PNG en el clon: se copia el otro tal cual.
        fake = FakeDrive()
        origen = fake.agregar_carpeta("ORIGEN")
        fake.agregar_archivo("pieza_01.JPG", origen, mime="image/jpeg")
        fake.agregar_archivo("pieza_02.JPG", origen, mime="image/jpeg")
        destino = fake.agregar_carpeta("DESTINO")
        fake.agregar_archivo("pieza_01.png", destino, mime="image/png")

        resumen = _clonar(fake, origen, destino)

        self.assertEqual(resumen.archivos_copiados, 1)
        self.assertEqual(resumen.archivos_omitidos, 1)
        self.assertEqual(sorted(h["name"] for h in fake.hijos(destino)), ["pieza_01.png", "pieza_02.JPG"])
        self.assertEqual(_en_papelera(fake, destino), set())


class TestClonacionConFallos(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDrive()
        self.origen = self.fake.agregar_carpeta("ORIGEN")
        self.destino = self.fake.agregar_carpeta("DESTINO")

    def test_e_timeout_tras_copiar_no_duplica(self):
        self.fake.agregar_archivo("doc.pdf", self.origen, contenido=b"doc")
        self.fake.fallar_red("copy", aplicar_efecto=True)
        esperas: list[float] = []

        resumen = _clonar(self.fake, self.origen, self.destino, dormir=esperas.append)

        self.assertEqual([h["name"] for h in self.fake.hijos(self.destino)], ["doc.pdf"])
        self.assertEqual(resumen.archivos_copiados, 1)
        self.assertEqual(resumen.archivos_fallidos, 0)
        self.assertEqual(resumen.archivos_duplicados_quitados, 0)
        self.assertEqual(self.fake.llamadas["copy"], 1)  # no se reintentó la copia
        self.assertEqual(esperas, [])  # detectó la copia al releer el índice, sin backoff

    def test_e2_500_tras_copiar_no_duplica(self):
        self.fake.agregar_archivo("doc.pdf", self.origen, contenido=b"doc")
        self.fake.fallar("copy", status=500, aplicar_efecto=True)
        resumen = _clonar(self.fake, self.origen, self.destino)
        self.assertEqual(len(self.fake.hijos(self.destino)), 1)
        self.assertEqual(resumen.archivos_copiados, 1)
        self.assertEqual(self.fake.llamadas["copy"], 1)

    def test_e3_fallo_de_red_sin_efecto_reintenta_con_backoff(self):
        self.fake.agregar_archivo("doc.pdf", self.origen)
        self.fake.fallar_red("copy", veces=2)
        esperas: list[float] = []
        resumen = _clonar(self.fake, self.origen, self.destino, dormir=esperas.append)
        self.assertEqual(len(self.fake.hijos(self.destino)), 1)
        self.assertEqual(resumen.archivos_copiados, 1)
        self.assertEqual(self.fake.llamadas["copy"], 3)
        self.assertEqual(len(esperas), 2)
        self.assertTrue(all(0 < e <= 90 for e in esperas))

    def test_f_403_en_copy_deja_el_archivo_en_fallidos(self):
        sub = self.fake.agregar_carpeta("Sub", self.origen)
        prohibido = self.fake.agregar_archivo("secreto.pdf", sub, contenido=b"s")
        self.fake.agregar_archivo("a.pdf", self.origen, contenido=b"a")
        self.fake.agregar_archivo("b.pdf", self.origen, contenido=b"b")
        lineas: list[str] = []

        resumen = _clonar(
            _DriveConCopiaProhibida(self.fake, prohibido, 403),
            self.origen,
            self.destino,
            log=lineas.append,
        )

        self.assertFalse(resumen.ok())
        self.assertEqual(resumen.archivos_fallidos, 1)
        self.assertEqual(resumen.fallidos, ["Sub/secreto.pdf"])
        self.assertEqual(resumen.archivos_copiados, 2)
        arbol = _arbol(self.fake, self.destino)
        self.assertEqual(set(arbol), {"a.pdf", "b.pdf", "Sub/"})
        self.assertTrue(any(l.startswith("  [ERROR] no se pudo copiar secreto.pdf") for l in lineas))
        self.assertIn("1 archivo(s) sin copiar", resumen.texto())

    def test_f2_fallo_solo_en_la_primera_pasada_se_completa_y_no_queda_fallido(self):
        self.fake.agregar_archivo("a.pdf", self.origen, contenido=b"a")
        self.fake.fallar("copy", status=404)  # solo la primera copia falla
        lineas: list[str] = []
        resumen = _clonar(self.fake, self.origen, self.destino, log=lineas.append)
        self.assertIn("  [completar archivo] a.pdf", lineas)
        self.assertTrue(resumen.ok())
        self.assertEqual(resumen.fallidos, [])
        self.assertEqual(resumen.archivos_copiados, 1)
        self.assertEqual([h["name"] for h in self.fake.hijos(self.destino)], ["a.pdf"])

    def test_g_listado_atrasado_no_duplica(self):
        self.fake.agregar_archivo("doc.pdf", self.origen, contenido=b"doc")
        destino = self.destino
        pendientes = {"ocultar": 2}  # las 2 primeras lecturas del destino tras la copia no la muestran

        def listar_atrasado(svc, parent_id, **kw):
            hijos = drive.listar_hijos(svc, parent_id, **kw)
            if parent_id == destino and hijos and pendientes["ocultar"] > 0:
                pendientes["ocultar"] -= 1
                return []  # Drive aún no indexó la copia
            return hijos

        esperas: list[float] = []
        lineas: list[str] = []
        resumen = _clonar(
            self.fake, self.origen, destino, listar=listar_atrasado, dormir=esperas.append, log=lineas.append
        )

        self.assertEqual(pendientes["ocultar"], 0)  # el retraso sí se ejerció
        self.assertEqual([h["name"] for h in self.fake.hijos(destino)], ["doc.pdf"])
        self.assertEqual(self.fake.llamadas["copy"], 1)
        self.assertEqual(resumen.archivos_copiados, 1)
        self.assertEqual(resumen.archivos_duplicados_quitados, 0)
        self.assertIn(1.5, esperas)  # _esperar_indice esperó a que apareciera
        self.assertNotIn("  [completar archivo] doc.pdf", lineas)

    def test_error_duro_al_listar_el_origen_se_propaga(self):
        with self.assertRaises(HttpError) as cm:
            _clonar(self.fake, "no_existe", self.destino)
        self.assertEqual(cm.exception.resp.status, 404)
        self.assertEqual(self.fake.hijos(self.destino), [])

    def test_reintento_de_listado_usa_el_dormir_inyectado(self):
        self.fake.agregar_archivo("doc.pdf", self.origen)
        self.fake.fallar("list", status=503)
        esperas: list[float] = []
        resumen = _clonar(self.fake, self.origen, self.destino, dormir=esperas.append)
        self.assertEqual(resumen.archivos_copiados, 1)
        self.assertEqual(len(esperas), 1)

    def test_timeout_al_crear_carpeta_termina_con_una_sola_visible(self):
        sub = self.fake.agregar_carpeta("Sub", self.origen)
        self.fake.agregar_archivo("x.pdf", sub, contenido=b"x")
        self.fake.fallar("create", status=500, aplicar_efecto=True)

        resumen = _clonar(self.fake, self.origen, self.destino)

        visibles = [h for h in self.fake.hijos(self.destino) if h["mimeType"] == MIME_FOLDER]
        self.assertEqual(len(visibles), 1)
        self.assertEqual(_arbol(self.fake, self.destino), _arbol(self.fake, self.origen))
        self.assertEqual(resumen.carpetas_duplicadas_quitadas, 1)  # como el script original

    def test_nunca_escribe_bajo_el_origen(self):
        # Aun con timeouts que obligan a releer y reintentar, el origen queda intacto.
        origen, _, _ = _armar_origen(self.fake)
        foto_antes = _foto(self.fake, origen)
        self.fake.fallar_red("copy", aplicar_efecto=True)
        self.fake.fallar("create", status=500, aplicar_efecto=True)
        _clonar(self.fake, origen, self.destino)
        self.assertEqual(_foto(self.fake, origen), foto_antes)


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


if __name__ == "__main__":
    unittest.main()
