from __future__ import annotations

import tempfile, unittest
from pathlib import Path
from unittest import mock

from flujo_lib import inventario
from tests.fake_drive import FakeDrive


class TestInventario(unittest.TestCase):
    def test_generador_heredado_recibe_nombres_canonicos_solo_en_archivos(self):
        fake = FakeDrive()
        raiz = fake.agregar_carpeta("Raíz")
        sub = fake.agregar_carpeta("Fotos.JPG", raiz)
        fake.agregar_archivo("pieza.JPEG", raiz, contenido=b"x", mime="image/jpeg")
        vistos = {}

        def generar(svc, listar, trabajos, salida):
            vistos["hijos"] = listar(svc, raiz)
            vistos["trabajos"] = trabajos
            return salida

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            inventario, "_cargar_heredados", return_value=(generar, mock.Mock())
        ):
            salida = Path(tmp) / "inventario.xlsx"
            self.assertEqual(inventario.generar_inventario(fake, [{"destino_id": raiz}], salida), salida)
        nombres = {h["mimeType"]: h["name"] for h in vistos["hijos"]}
        self.assertEqual(nombres["image/jpeg"], "pieza.png")
        self.assertEqual(nombres["application/vnd.google-apps.folder"], "Fotos.JPG")


if __name__ == "__main__":
    unittest.main()
