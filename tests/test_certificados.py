"""
Pruebas del paquete de certificados de confianza.

No sale a la red: se comprueba qué archivo se escribe y qué se le indica a cada
librería. El almacén del equipo se simula cuando hace falta, para que las
pruebas den lo mismo en cualquier máquina.
"""

from __future__ import annotations

import os, ssl, tempfile, unittest
from pathlib import Path
from unittest import mock

import certifi

from flujo_lib import certificados

# Un certificado cualquiera de la lista de Python, para simular el almacén.
PEM_EJEMPLO = certifi.contents().split("-----END CERTIFICATE-----")[0] + "-----END CERTIFICATE-----\n"


class BaseCertificados(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.destino = self.dir / "certificados_confianza.pem"
        self.mensajes: list[str] = []
        # Ni las variables de entorno ni httplib2 deben quedar tocados al salir.
        parche = mock.patch.dict(os.environ, {}, clear=False)
        parche.start()
        self.addCleanup(parche.stop)
        try:
            import httplib2

            original = httplib2.CA_CERTS
            self.addCleanup(setattr, httplib2, "CA_CERTS", original)
        except ImportError:  # pragma: no cover
            pass
        self.addCleanup(self._tmp.cleanup)

    def log(self, mensaje: str) -> None:
        self.mensajes.append(mensaje)


class TestGenerar(BaseCertificados):
    def test_suma_la_lista_de_python_y_la_del_equipo(self):
        with mock.patch.object(
            certificados, "certificados_del_equipo", return_value=[PEM_EJEMPLO, PEM_EJEMPLO]
        ):
            ruta, cuantos = certificados.generar(self.destino)

        self.assertEqual(ruta, self.destino)
        self.assertEqual(cuantos, 2)
        contenido = self.destino.read_text(encoding="ascii")
        self.assertIn(certifi.contents()[:200], contenido)
        self.assertGreater(
            contenido.count("-----BEGIN CERTIFICATE-----"),
            certifi.contents().count("-----BEGIN CERTIFICATE-----"),
        )

    def test_crea_la_carpeta_si_no_existe(self):
        destino = self.dir / "sub" / "carpeta" / "paquete.pem"
        with mock.patch.object(certificados, "certificados_del_equipo", return_value=[]):
            certificados.generar(destino)
        self.assertTrue(destino.is_file())

    @unittest.skipUnless(hasattr(ssl, "enum_certificates"), "el almacén solo existe en Windows")
    def test_en_windows_el_equipo_aporta_certificados_reales(self):
        propios = certificados.certificados_del_equipo()
        self.assertTrue(propios, "el almacén de Windows no devolvió ningún certificado")
        self.assertTrue(all(p.startswith("-----BEGIN CERTIFICATE-----") for p in propios))

    def test_un_almacen_inaccesible_no_impide_leer_los_demas(self):
        def enum(almacen):
            if almacen == "ROOT":
                raise PermissionError("sin acceso")
            return [(b"\x30\x82", "x509_asn", True)]

        with mock.patch.object(ssl, "enum_certificates", enum, create=True):
            with mock.patch.object(ssl, "DER_cert_to_PEM_cert", lambda _d: PEM_EJEMPLO):
                self.assertEqual(certificados.certificados_del_equipo(), [PEM_EJEMPLO])


class TestAplicar(BaseCertificados):
    def test_indica_el_paquete_a_cada_libreria(self):
        self.destino.write_text(PEM_EJEMPLO, encoding="ascii")
        self.assertTrue(certificados.aplicar(self.destino))

        for variable in certificados.VARIABLES:
            self.assertEqual(os.environ[variable], str(self.destino), variable)
        import httplib2

        self.assertEqual(httplib2.CA_CERTS, str(self.destino))

    def test_sin_archivo_no_cambia_nada(self):
        os.environ.pop("SSL_CERT_FILE", None)
        self.assertFalse(certificados.aplicar(self.dir / "no_existe.pem"))
        self.assertNotIn("SSL_CERT_FILE", os.environ)


class TestAsegurar(BaseCertificados):
    def test_genera_aplica_y_avisa(self):
        with mock.patch.object(certificados, "certificados_del_equipo", return_value=[PEM_EJEMPLO]):
            with mock.patch.object(certificados, "hay_almacen_windows", return_value=True):
                self.assertTrue(certificados.asegurar(self.destino, log=self.log))

        self.assertTrue(self.destino.is_file())
        self.assertEqual(os.environ["REQUESTS_CA_BUNDLE"], str(self.destino))
        self.assertIn("1 del almacén de Windows", self.mensajes[0])

    def test_se_rehace_en_cada_corrida(self):
        """Un cambio de certificados del antivirus no debe dejar el flujo caído."""
        self.destino.write_text("contenido viejo", encoding="ascii")
        with mock.patch.object(certificados, "certificados_del_equipo", return_value=[PEM_EJEMPLO]):
            with mock.patch.object(certificados, "hay_almacen_windows", return_value=True):
                certificados.asegurar(self.destino, log=self.log)
        self.assertNotIn("contenido viejo", self.destino.read_text(encoding="ascii"))

    def test_fuera_de_windows_no_hace_nada(self):
        with mock.patch.object(certificados, "hay_almacen_windows", return_value=False):
            self.assertFalse(certificados.asegurar(self.destino, log=self.log))
        self.assertFalse(self.destino.exists())
        self.assertEqual(self.mensajes, [])

    def test_un_fallo_al_preparar_no_tumba_la_corrida(self):
        with mock.patch.object(certificados, "hay_almacen_windows", return_value=True):
            with mock.patch.object(certificados, "generar", side_effect=OSError("disco lleno")):
                self.assertFalse(certificados.asegurar(self.destino, log=self.log))
        self.assertIn("disco lleno", self.mensajes[0])
        self.assertIn("lista de Python", self.mensajes[0])


if __name__ == "__main__":
    unittest.main()
