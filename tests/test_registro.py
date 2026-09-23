"""
Pruebas del registro del servicio.

Lo que se comprueba, sobre todo, es que el detalle técnico de un error llegue a
la salida estándar: en el servidor no hay archivo de corrida donde mirarlo, y
sin él un fallo en el despliegue solo dice «no hay conexión».
"""

from __future__ import annotations

import io, logging, unittest

from flujo_lib.mensajes import ErrorFlujo
from flujo_lib.prevalidacion import Hallazgo, ResultadoPrevalidacion
from servidor import registro

import run_flujo


class BaseRegistro(unittest.TestCase):
    def setUp(self):
        self.salida = io.StringIO()
        raiz = logging.getLogger()
        previos = list(raiz.handlers)
        nivel_previo = raiz.level
        niveles = {n: logging.getLogger(n).level for n in registro.LOGGERS_RUIDOSOS}

        def restaurar():
            registro.quitar()
            raiz.handlers[:] = previos
            raiz.setLevel(nivel_previo)
            for nombre, nivel in niveles.items():
                logging.getLogger(nombre).setLevel(nivel)

        self.addCleanup(restaurar)
        raiz.handlers[:] = []  # se parte de un registro limpio


class TestConfigurar(BaseRegistro):
    def test_escribe_en_la_salida(self):
        registro.configurar(flujo=self.salida)
        logging.getLogger().info("hola mundo")
        self.assertIn("hola mundo", self.salida.getvalue())
        self.assertIn("INFO", self.salida.getvalue())

    def test_es_idempotente(self):
        registro.configurar(flujo=self.salida)
        registro.configurar(flujo=self.salida)
        registro.configurar(flujo=self.salida)
        logging.getLogger().info("una sola vez")
        self.assertEqual(self.salida.getvalue().count("una sola vez"), 1)
        propios = [h for h in logging.getLogger().handlers if getattr(h, "name", "") == registro.NOMBRE]
        self.assertEqual(len(propios), 1)

    def test_escucha_el_detalle_tecnico(self):
        """El nivel depuración es justo donde se emite la causa real."""
        registro.configurar(flujo=self.salida)
        logging.getLogger().debug("    detalle técnico: %s", "OSError('sin ruta')")
        self.assertIn("OSError('sin ruta')", self.salida.getvalue())

    def test_calla_a_las_librerias_habladoras(self):
        registro.configurar(flujo=self.salida)
        for nombre in registro.LOGGERS_RUIDOSOS:
            logging.getLogger(nombre).debug("ruido que no aporta")
            self.assertEqual(logging.getLogger(nombre).level, logging.WARNING, nombre)
        self.assertNotIn("ruido que no aporta", self.salida.getvalue())

        logging.getLogger("googleapiclient").error("esto sí importa")
        self.assertIn("esto sí importa", self.salida.getvalue())

    def test_quitar_deja_de_escribir(self):
        registro.configurar(flujo=self.salida)
        registro.quitar()
        # Sin ningún handler, Python imprime por su cuenta en la salida de errores;
        # este handler mudo evita que la prueba ensucie la terminal.
        logging.getLogger().addHandler(logging.NullHandler())
        logging.getLogger().error("ya no debería salir")
        self.assertNotIn("ya no debería salir", self.salida.getvalue())


class TestDetalleDeLosHallazgos(BaseRegistro):
    """Con el registro del servidor, la causa real de un fallo tiene que verse."""

    def test_el_hallazgo_con_error_muestra_su_causa(self):
        registro.configurar(flujo=self.salida)
        res = ResultadoPrevalidacion()
        res.agregar(
            "error", "token",
            "No hay conexión con Google o con la red.",
            "Revisa la conexión a internet y vuelve a ejecutar.",
            "TransportError('Failed to resolve oauth2.googleapis.com')",
        )
        run_flujo.imprimir_hallazgos(res)

        texto = self.salida.getvalue()
        self.assertIn("No hay conexión con Google o con la red.", texto)
        self.assertIn("detalle técnico (token)", texto)
        self.assertIn("Failed to resolve oauth2.googleapis.com", texto)

    def test_un_hallazgo_correcto_no_ensucia(self):
        registro.configurar(flujo=self.salida)
        res = ResultadoPrevalidacion()
        res.agregar("ok", "db", "Base de datos disponible.")
        run_flujo.imprimir_hallazgos(res)
        self.assertIn("Base de datos disponible.", self.salida.getvalue())
        self.assertNotIn("detalle técnico", self.salida.getvalue())

    def test_sin_el_registro_del_servidor_el_detalle_no_sale_por_consola(self):
        """En la terminal el detalle sigue yendo solo al archivo de la corrida."""
        consola = logging.StreamHandler(self.salida)
        consola.setLevel(logging.INFO)  # como lo deja el comando de terminal
        raiz = logging.getLogger()
        raiz.addHandler(consola)
        raiz.setLevel(logging.DEBUG)

        res = ResultadoPrevalidacion()
        res.agregar("error", "db", "No se pudo conectar a la base de datos.", "Revisa.", "OperationalError")
        run_flujo.imprimir_hallazgos(res)

        texto = self.salida.getvalue()
        self.assertIn("No se pudo conectar a la base de datos.", texto)
        self.assertNotIn("OperationalError", texto)


if __name__ == "__main__":
    unittest.main()
