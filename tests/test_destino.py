"""Pruebas de flujo_lib.destino (carpeta destino automática). Sin red."""

from __future__ import annotations

import functools, unittest

from flujo_lib import destino, drive
from flujo_lib.mensajes import ErrorFlujo
from tests.fake_drive import MIME_FOLDER, FakeDrive

SIN_ESPERA = functools.partial(drive.crear_carpeta, dormir=lambda *_: None)


def _carpeta(fake: FakeDrive, id: str) -> dict:
    reg = fake.obtener(id)
    return {"id": reg["id"], "name": reg["name"], "mimeType": reg["mimeType"]}


class TestNombresEquivalentes(unittest.TestCase):
    def test_equivalentes(self):
        for a, b in (("Lote A", "lote a"), ("  Lote A ", "Lote A"), ("Lote   A", "Lote A"),
                     ("Ñandú", "ñandú"), ("X\tY", "X Y")):
            with self.subTest(a=a, b=b):
                self.assertTrue(destino.nombres_equivalentes(a, b))

    def test_no_equivalentes(self):
        for a, b in (("Lote A", "Lote B"), ("Bogotá", "Bogota"), ("LoteA", "Lote A"), ("", "x")):
            with self.subTest(a=a, b=b):
                self.assertFalse(destino.nombres_equivalentes(a, b))


class TestResolverDestino(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDrive()
        self.origen_id = self.fake.agregar_carpeta("Lote Bogotá 2026")
        self.fake.agregar_archivo("pieza.JPG", self.origen_id)
        self.raiz_id = self.fake.agregar_carpeta("DESTINO RAIZ")

    def _resolver(self, **kw):
        kw.setdefault("crear_carpeta", SIN_ESPERA)
        return destino.resolver_destino(
            self.fake, _carpeta(self.fake, self.origen_id), _carpeta(self.fake, self.raiz_id), **kw
        )

    def _subcarpetas(self):
        return [h for h in self.fake.hijos(self.raiz_id) if h["mimeType"] == MIME_FOLDER]

    def test_1_mismo_nombre_usa_la_raiz_directamente(self):
        raiz_igual = self.fake.agregar_carpeta("lote bogotá 2026")  # equivalente (mayúsculas)
        res = destino.resolver_destino(
            self.fake, _carpeta(self.fake, self.origen_id), _carpeta(self.fake, raiz_igual)
        )
        self.assertTrue(res.directa)
        self.assertFalse(res.creada)
        self.assertEqual(res.id, raiz_igual)
        self.assertEqual(res.nombre, "lote bogotá 2026")
        self.assertEqual(res.raiz_id, raiz_igual)
        self.assertEqual(len(res.avisos), 1)
        self.assertIn("se usa directamente sin crear subcarpeta", res.avisos[0])
        self.assertEqual(self.fake.llamadas["create"], 0)
        self.assertEqual(self.fake.llamadas["list"], 0)

    def test_2_nombre_distinto_crea_subcarpeta(self):
        res = self._resolver()
        self.assertTrue(res.creada)
        self.assertFalse(res.directa)
        self.assertEqual(res.nombre, "Lote Bogotá 2026")
        self.assertEqual(res.raiz_id, self.raiz_id)
        self.assertEqual(res.raiz_nombre, "DESTINO RAIZ")
        self.assertEqual(res.avisos, ())
        subs = self._subcarpetas()
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["id"], res.id)
        self.assertEqual(subs[0]["name"], "Lote Bogotá 2026")
        self.assertEqual(subs[0]["parents"], [self.raiz_id])
        self.assertEqual(self.fake.llamadas["create"], 1)

    def test_3_existente_exacta_se_reutiliza_sin_create(self):
        existente = self.fake.agregar_carpeta("Lote Bogotá 2026", self.raiz_id)
        self.fake.agregar_carpeta("Otra", self.raiz_id)
        res = self._resolver()
        self.assertEqual(res.id, existente)
        self.assertFalse(res.creada)
        self.assertFalse(res.directa)
        self.assertEqual(res.avisos, ())
        self.assertEqual(self.fake.llamadas["create"], 0)
        self.assertEqual(len(self._subcarpetas()), 2)

    def test_4_equivalente_por_mayusculas_se_reutiliza_con_aviso(self):
        parecida = self.fake.agregar_carpeta("LOTE  BOGOTÁ 2026", self.raiz_id)
        res = self._resolver()
        self.assertEqual(res.id, parecida)
        self.assertEqual(res.nombre, "LOTE  BOGOTÁ 2026")
        self.assertFalse(res.creada)
        self.assertEqual(len(res.avisos), 1)
        self.assertIn("casi igual", res.avisos[0])
        self.assertEqual(self.fake.llamadas["create"], 0)

    def test_5_duplicadas_usa_la_primera_y_avisa_sin_borrar(self):
        primera = self.fake.agregar_carpeta("Lote Bogotá 2026", self.raiz_id)
        segunda = self.fake.agregar_carpeta("Lote Bogotá 2026", self.raiz_id)
        res = self._resolver()
        self.assertEqual(res.id, primera)
        self.assertFalse(res.creada)
        self.assertEqual(len(res.avisos), 1)
        self.assertIn("2 carpetas", res.avisos[0])
        self.assertFalse(self.fake.obtener(primera)["trashed"])
        self.assertFalse(self.fake.obtener(segunda)["trashed"])
        self.assertEqual(self.fake.llamadas["update"], 0)
        self.assertEqual(self.fake.llamadas["create"], 0)

    def test_6_crear_false_y_ausente_lanza_destino_no_encontrado(self):
        with self.assertRaises(destino.DestinoNoEncontrado) as cm:
            self._resolver(crear=False)
        self.assertIsInstance(cm.exception, ErrorFlujo)
        self.assertEqual(
            cm.exception.motivo, "Dentro de «DESTINO RAIZ» no existe la carpeta «Lote Bogotá 2026»."
        )
        self.assertEqual(cm.exception.accion, "Ejecuta primero la clonación del lote.")
        self.assertEqual(self.fake.llamadas["create"], 0)
        self.assertEqual(self._subcarpetas(), [])

    def test_6b_crear_false_pero_existente_se_reutiliza(self):
        existente = self.fake.agregar_carpeta("Lote Bogotá 2026", self.raiz_id)
        res = self._resolver(crear=False)
        self.assertEqual(res.id, existente)

    def test_7_origen_igual_a_raiz_lanza_value_error(self):
        misma = _carpeta(self.fake, self.origen_id)
        with self.assertRaises(ValueError):
            destino.resolver_destino(self.fake, misma, dict(misma))
        self.assertEqual(self.fake.llamadas["list"], 0)

    def test_8_fallo_transitorio_en_create_con_efecto_no_duplica(self):
        self.fake.fallar("create", status=500, aplicar_efecto=True)
        res = self._resolver()
        self.assertTrue(res.creada)
        subs = self._subcarpetas()
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["id"], res.id)
        self.assertEqual(self.fake.llamadas["create"], 1)

    def test_9_describir_tres_variantes(self):
        base = dict(id="d1", nombre="Lote", raiz_id="r1", raiz_nombre="RAIZ", directa=False, avisos=())
        creada = destino.DestinoResuelto(creada=True, **base)
        reutilizada = destino.DestinoResuelto(creada=False, **base)
        directa = destino.DestinoResuelto(**{**base, "creada": False, "directa": True})
        self.assertEqual(destino.describir(creada), "Carpeta destino creada «Lote» dentro de «RAIZ»")
        self.assertEqual(
            destino.describir(reutilizada), "Carpeta destino reutilizada «Lote» dentro de «RAIZ»"
        )
        self.assertEqual(
            destino.describir(directa), "Carpeta destino directa «Lote» (ya se llama como el origen)"
        )

    def test_listar_solo_carpetas_e_ignora_archivos_homonimos(self):
        # Un ARCHIVO llamado igual que el origen no cuenta como carpeta destino.
        self.fake.agregar_archivo("Lote Bogotá 2026", self.raiz_id)
        res = self._resolver()
        self.assertTrue(res.creada)
        self.assertEqual(self.fake.obtener(res.id)["mimeType"], MIME_FOLDER)

    def test_no_toca_el_origen(self):
        antes = self.fake.hijos(self.origen_id)
        self._resolver()
        self.assertEqual(self.fake.hijos(self.origen_id), antes)
        self.assertFalse(self.fake.obtener(self.origen_id)["trashed"])


if __name__ == "__main__":
    unittest.main()
