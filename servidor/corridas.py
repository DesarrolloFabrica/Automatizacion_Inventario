"""
Gestor de corridas del servicio web.

Recibe peticiones de la página, las pone en una cola y las ejecuta **de una en
una** en un hilo de fondo. La ejecución es la misma de siempre: se reutiliza
`run_flujo.ejecutar_corrida`, así la web y el comando de terminal hacen
exactamente lo mismo y no pueden divergir.

Por qué de una en una: dos corridas a la vez se pisarían en el log, en las
transacciones de la base y, si tocaran el mismo programa, en el reemplazo por
programa. Para un equipo de dos o tres personas, que la segunda espere en la
cola es más simple y más seguro que coordinar accesos.
"""

from __future__ import annotations

import logging, queue, threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from flujo_lib.almacen import crear_almacen
from flujo_lib.drive import extraer_id_carpeta
from flujo_lib.estado import PASOS, EstadoCorrida
from flujo_lib.excel import CLASIFICACIONES, Lote, normalizar_clasificacion
from flujo_lib.mensajes import ErrorFlujo
from flujo_lib.progreso import Reporte

import run_flujo

# Tope de mensajes guardados por corrida. La página los muestra como consola y
# los va añadiendo, así que se mandan todos: recortar rompería el historial.
MAX_MENSAJES = 3000
ESTADOS_TERMINADOS = {"ok", "con_pendientes", "fallido", "fallido_prevalidacion", "cancelada"}
_CLIENTES_VALIDOS = "PRODUCTO, TANIA o LMS_correcciones"


def construir_lote(
    origen: str, destino: str, etiqueta: str, cliente: str, *, fila: int = 1
) -> Lote:
    """
    Arma el lote desde el formulario, con las mismas validaciones del Excel.
    `cliente` vacío deja el lote sin clasificar: la prevalidación lo deduce de Drive.
    """
    origen_id = extraer_id_carpeta(origen or "")
    destino_id = extraer_id_carpeta(destino or "")
    if not origen_id:
        raise ErrorFlujo(
            "El enlace de la carpeta de origen no se entiende.",
            "Copia el enlace desde la barra de direcciones de Google Drive, estando dentro de la carpeta.",
            paso="formulario",
        )
    if not destino_id:
        raise ErrorFlujo(
            "El enlace de la carpeta de destino no se entiende.",
            "Copia el enlace desde la barra de direcciones de Google Drive, estando dentro de la carpeta.",
            paso="formulario",
        )
    if origen_id == destino_id:
        raise ErrorFlujo(
            "El origen y el destino son la misma carpeta.",
            "El destino debe ser la carpeta que va a contener la copia, no la carpeta que se copia.",
            paso="formulario",
        )

    clasificacion = ""
    cliente_gcp = raiz_gcp = ""
    if (cliente or "").strip():
        clasificacion = normalizar_clasificacion(cliente) or ""
        if not clasificacion:
            raise ErrorFlujo(
                f"El cliente «{cliente}» no existe.",
                f"Elige {_CLIENTES_VALIDOS}, o deja que se detecte solo.",
                paso="formulario",
            )
        cliente_gcp, raiz_gcp = CLASIFICACIONES[clasificacion]

    return Lote(
        fila=fila,
        etiqueta=(etiqueta or "").strip() or "Lote sin nombre",
        cliente_excel=(cliente or "").strip(),
        clasificacion=clasificacion,
        cliente_gcp=cliente_gcp,
        raiz_gcp=raiz_gcp,
        origen_id=origen_id,
        destino_raiz_id=destino_id,
        origen_raw=origen,
        destino_raw=destino,
    )


@dataclass
class Trabajo:
    """Una corrida pedida desde la web, con lo que la página necesita mostrar."""

    id: str
    lotes: list[Lote]
    simular: bool
    forzar_carga: bool
    creado: str
    estado_general: str = "en_cola"
    inicio: str | None = None
    fin: str | None = None
    mensajes: list[dict] = field(default_factory=list)
    prevalidacion: list[dict] = field(default_factory=list)
    resumen: dict | None = None
    enlaces: dict = field(default_factory=dict)
    estado: EstadoCorrida | None = None
    cancelacion: threading.Event = field(default_factory=threading.Event)

    @property
    def terminado(self) -> bool:
        return self.estado_general in ESTADOS_TERMINADOS

    @property
    def lote(self) -> Lote:
        """Primer lote, conservado para los textos y clientes web antiguos."""
        return self.lotes[0]


class _Bitacora(logging.Handler):
    """Guarda en el trabajo las líneas del log para pintarlas como consola."""

    _NIVELES = {logging.WARNING: "aviso", logging.ERROR: "error", logging.CRITICAL: "error"}

    def __init__(self, trabajo: Trabajo, ahora):
        super().__init__(level=logging.INFO)
        self._trabajo = trabajo
        self._ahora = ahora

    def emit(self, registro: logging.LogRecord) -> None:
        try:
            texto = registro.getMessage()
        except Exception:  # un mensaje mal formado no puede tumbar la corrida
            return
        if not texto.strip() or set(texto.strip()) == {"="}:
            return  # las líneas de separación del log no aportan nada en pantalla
        mensajes = self._trabajo.mensajes
        mensajes.append(
            {
                "hora": self._ahora().strftime("%H:%M:%S"),
                "nivel": self._NIVELES.get(registro.levelno, "info"),
                "texto": texto,
            }
        )
        if len(mensajes) > MAX_MENSAJES:
            del mensajes[: len(mensajes) - MAX_MENSAJES]


class Gestor:
    """Cola de corridas y consulta de su avance."""

    def __init__(self, cfg, *, correr=None, ahora=None, automatico: bool = True):
        self.cfg = cfg
        self._correr = correr or self._correr_de_verdad
        self._ahora = ahora or datetime.now
        self._trabajos: dict[str, Trabajo] = {}
        self._orden: list[str] = []
        self._cola: queue.Queue[str] = queue.Queue()
        self._lock = threading.RLock()
        self._hilo: threading.Thread | None = None
        self._parar = threading.Event()
        if automatico:
            self.iniciar()

    # ----- ciclo de vida --------------------------------------------------
    def iniciar(self) -> None:
        if self._hilo is None or not self._hilo.is_alive():
            self._parar.clear()
            self._hilo = threading.Thread(target=self._bucle, name="corridas", daemon=True)
            self._hilo.start()

    def detener(self, espera: float = 5.0) -> None:
        self._parar.set()
        self._cola.put("")  # despierta al hilo para que vea la bandera
        if self._hilo is not None and self._hilo.is_alive():
            self._hilo.join(timeout=espera)

    def _bucle(self) -> None:
        while not self._parar.is_set():
            corrida_id = self._cola.get()
            if self._parar.is_set() or not corrida_id:
                continue
            self.procesar(corrida_id)

    # ----- API que usa la web ---------------------------------------------
    def lanzar(
        self,
        *,
        origen: str = "",
        destino: str = "",
        etiqueta: str = "",
        cliente: str = "",
        simular: bool | None = None,
        forzar_carga: bool | None = None,
        lotes: list[Lote] | None = None,
    ) -> Trabajo:
        """Encola una corrida nueva. Lanza ErrorFlujo si los datos no sirven."""
        lotes = list(lotes) if lotes is not None else [
            construir_lote(origen, destino, etiqueta, cliente)
        ]
        if not lotes:
            raise ErrorFlujo(
                "No hay carpetas para procesar.",
                "Agrega por lo menos una fila con origen y destino.",
                paso="formulario",
            )

        origenes_vistos: set[str] = set()
        for lote in lotes:
            if lote.origen_id in origenes_vistos:
                raise ErrorFlujo(
                    "Una carpeta de origen aparece más de una vez en la misma corrida.",
                    "Elimina la fila repetida y vuelve a pulsar Ejecutar.",
                    paso="formulario",
                )
            origenes_vistos.add(lote.origen_id)

        with self._lock:
            for lote in lotes:
                encurso = self._en_curso_para(lote.origen_id)
                if encurso is not None:
                    raise ErrorFlujo(
                        f"Ya hay una corrida en marcha para esa carpeta de origen "
                        f"(«{encurso.lote.etiqueta}»).",
                        "Espera a que termine o ábrela para ver cómo va.",
                        paso="formulario",
                        detalle=encurso.id,
                    )
            trabajo = Trabajo(
                id=self._nuevo_id(),
                lotes=lotes,
                simular=self.cfg.simular_por_defecto if simular is None else bool(simular),
                forzar_carga=(
                    self.cfg.forzar_carga_por_defecto if forzar_carga is None else bool(forzar_carga)
                ),
                creado=self._ahora().isoformat(timespec="seconds"),
            )
            self._trabajos[trabajo.id] = trabajo
            self._orden.insert(0, trabajo.id)
        self._cola.put(trabajo.id)
        return trabajo

    def obtener(self, corrida_id: str) -> Trabajo | None:
        with self._lock:
            return self._trabajos.get(corrida_id)

    def cancelar(self, corrida_id: str) -> bool:
        """Pide parar. Si aún no empezó se marca cancelada de una vez."""
        trabajo = self.obtener(corrida_id)
        if trabajo is None or trabajo.terminado:
            return False
        trabajo.cancelacion.set()
        if trabajo.estado_general == "en_cola":
            trabajo.estado_general = "cancelada"
            trabajo.fin = self._ahora().isoformat(timespec="seconds")
        return True

    def listar(self, limite: int = 10) -> list[dict]:
        with self._lock:
            ids = self._orden[:limite]
            return [self._resumen_corto(self._trabajos[i]) for i in ids]

    def ver(self, corrida_id: str) -> dict | None:
        trabajo = self.obtener(corrida_id)
        return None if trabajo is None else self._como_json(trabajo)

    def salud(self, *, cuenta=None) -> dict:
        return {
            "ok": True,
            "cuenta": cuenta,
            "esquema": self.cfg.schema,
            "produccion": self.cfg.es_produccion,
            "simular_por_defecto": self.cfg.simular_por_defecto,
            "almacen": self.cfg.almacen or str(self.cfg.dir_corridas),
        }

    # ----- ejecución -------------------------------------------------------
    def procesar(self, corrida_id: str) -> None:
        """Ejecuta una corrida encolada (el hilo de fondo llama aquí)."""
        trabajo = self.obtener(corrida_id)
        if trabajo is None or trabajo.terminado:
            return
        trabajo.estado_general = "en_curso"
        trabajo.inicio = self._ahora().isoformat(timespec="seconds")
        bitacora = _Bitacora(trabajo, self._ahora)
        bitacora.setFormatter(logging.Formatter("%(message)s"))
        raiz = logging.getLogger()
        nivel_previo = raiz.level
        raiz.setLevel(min(nivel_previo or logging.INFO, logging.INFO))
        raiz.addHandler(bitacora)
        try:
            self._correr(trabajo)
        except Exception as e:  # nada puede tumbar el hilo de corridas
            from flujo_lib.mensajes import traducir_excepcion

            err = traducir_excepcion(e, paso="corrida")
            logging.getLogger().error("%s", err)
            trabajo.estado_general = "fallido"
            trabajo.resumen = {"motivo": err.motivo, "accion": err.accion}
        finally:
            raiz.removeHandler(bitacora)
            raiz.setLevel(nivel_previo)
            if not trabajo.terminado:
                trabajo.estado_general = "fallido"
            trabajo.fin = self._ahora().isoformat(timespec="seconds")

    def _correr_de_verdad(self, trabajo: Trabajo) -> None:
        """La corrida real: el mismo camino que el comando de terminal."""
        from servidor import configuracion as config_mod

        almacen = crear_almacen(self.cfg.almacen) if self.cfg.almacen else None
        nombre = Path(trabajo.id)
        estado = EstadoCorrida.abrir(nombre, self.cfg.dir_corridas, almacen=almacen)
        trabajo.estado = estado

        args = SimpleNamespace(
            excel=None,
            schema=self.cfg.schema,
            simular=trabajo.simular,
            forzar_carga=trabajo.forzar_carga,
            solo_prevalidar=False,
            rehacer=None,
            no_interactivo=True,
            dir_corridas=self.cfg.dir_corridas,
        )
        estado.iniciar_corrida(run_flujo.argumentos_corrida(args, nombre))
        reporte = Reporte(destino=lambda clave, paso, avance: estado.avanzar(clave, paso, avance))
        ruta_log = Path(self.cfg.dir_corridas) / "logs" / f"{trabajo.id}.log"

        codigo = run_flujo.ejecutar_corrida(
            args, nombre, estado, ruta_log,
            lotes=trabajo.lotes,
            reporte=reporte,
            cargar_credenciales=lambda **_kw: config_mod.cargar_credenciales(self.cfg),
            cancelado=trabajo.cancelacion.is_set,
        )
        estado.forzar_guardado()
        trabajo.prevalidacion = self._prevalidacion_de(estado)
        trabajo.enlaces = self._enlaces_de(estado, trabajo)
        trabajo.estado_general = (
            "cancelada" if trabajo.cancelacion.is_set() else self._estado_general(estado, codigo)
        )
        trabajo.resumen = self._resumen_final(estado, trabajo)

    # ----- ayudas ----------------------------------------------------------
    def _nuevo_id(self) -> str:
        base = self._ahora().strftime("%Y%m%d_%H%M%S")
        corrida_id, sufijo = base, 1
        while corrida_id in self._trabajos:
            sufijo += 1
            corrida_id = f"{base}_{sufijo}"
        return corrida_id

    def _en_curso_para(self, origen_id: str) -> Trabajo | None:
        for corrida_id in self._orden:
            trabajo = self._trabajos[corrida_id]
            if not trabajo.terminado and any(
                lote.origen_id == origen_id for lote in trabajo.lotes
            ):
                return trabajo
        return None

    @staticmethod
    def _prevalidacion_de(estado: EstadoCorrida) -> list[dict]:
        return list(estado.datos.get("prevalidacion") or [])

    @staticmethod
    def _enlaces_de(estado: EstadoCorrida, trabajo: Trabajo) -> dict:
        inventario = estado.datos.get("inventario") or {}
        destino_id = None
        if len(trabajo.lotes) == 1:
            destino_id, _ = estado.destino_de(trabajo.lote.clave)
        return {
            "destino": f"https://drive.google.com/drive/folders/{destino_id}" if destino_id else None,
            "sheet": inventario.get("sheet") or None,
            "estado_xlsx": estado.ruta_visible_excel,
        }

    @staticmethod
    def _estado_general(estado: EstadoCorrida, codigo: int) -> str:
        corridas = estado.datos.get("corridas") or []
        resultado = (corridas[-1] or {}).get("resultado") if corridas else None
        if resultado in {"ok", "con_pendientes", "fallido", "fallido_prevalidacion"}:
            return resultado
        return {0: "ok", 2: "con_pendientes"}.get(codigo, "fallido")

    def _resumen_final(self, estado: EstadoCorrida, trabajo: Trabajo) -> dict:
        general = trabajo.estado_general
        datos_lotes = estado.datos.get("lotes", {})
        errores = [
            (datos_lotes.get(lote.clave) or {}).get("ultimo_error") or {}
            for lote in trabajo.lotes
        ]
        error = next((item for item in errores if item), {})
        cantidad = len(trabajo.lotes)
        if general == "ok":
            return {
                "motivo": (
                    "La carpeta quedó clonada, verificada y registrada."
                    if cantidad == 1
                    else f"Las {cantidad} carpetas quedaron clonadas, verificadas y registradas."
                ),
                "accion": "Revisa el inventario si quieres el detalle por materia.",
            }
        if general == "con_pendientes":
            return {
                "motivo": "Una o más carpetas terminaron con diferencias y no se cargaron a la base.",
                "accion": "Abre el inventario para ver qué falta y vuelve a ejecutarlas cuando esté corregido.",
            }
        if general == "fallido_prevalidacion":
            return {
                "motivo": "La corrida no llegó a empezar.",
                "accion": "Corrige lo que aparece arriba y vuelve a intentarlo.",
            }
        return {
            "motivo": error.get("motivo") or "La corrida no terminó correctamente.",
            "accion": error.get("accion") or "Revisa los mensajes y vuelve a intentarlo.",
        }

    def _resumen_corto(self, trabajo: Trabajo) -> dict:
        etiqueta = trabajo.lote.etiqueta
        if len(trabajo.lotes) > 1:
            etiqueta = f"{etiqueta} · {len(trabajo.lotes)} carpetas"
        return {
            "id": trabajo.id,
            "inicio": trabajo.inicio or trabajo.creado,
            "fin": trabajo.fin,
            "estado": trabajo.estado_general,
            "etiqueta": etiqueta,
        }

    def _como_json(self, trabajo: Trabajo) -> dict:
        return {
            "id": trabajo.id,
            "estado": trabajo.estado_general,
            "inicio": trabajo.inicio or trabajo.creado,
            "fin": trabajo.fin,
            "simular": trabajo.simular,
            "esquema": self.cfg.schema,
            "prevalidacion": trabajo.prevalidacion,
            "lotes": self._lotes_json(trabajo),
            "mensajes": list(trabajo.mensajes),
            "resumen": trabajo.resumen,
            "enlaces": trabajo.enlaces,
        }

    def _lotes_json(self, trabajo: Trabajo) -> list[dict]:
        vacio = {"estado": "pendiente", "detalle": "",
                 "progreso": {"hechos": 0, "total": 0, "mensaje": "", "porcentaje": 0}}
        if trabajo.estado is None:
            return [
                {
                    "clave": lote.clave,
                    "etiqueta": lote.etiqueta,
                    "cliente": lote.cliente_excel or lote.clasificacion or "",
                    "destino_nombre": None,
                    "destino_enlace": None,
                    "pasos": {p: dict(vacio) for p in PASOS},
                }
                for lote in trabajo.lotes
            ]
        salida = []
        etiquetas = {lote.clave: lote.etiqueta for lote in trabajo.lotes}
        for fila in trabajo.estado.resumen():
            destino_id = fila.get("destino_id")
            salida.append({
                "clave": fila["clave"],
                "etiqueta": fila.get("etiqueta") or etiquetas.get(fila["clave"], "Carpeta"),
                "cliente": fila.get("cliente") or "",
                "destino_nombre": fila.get("destino_nombre"),
                "destino_enlace": (
                    f"https://drive.google.com/drive/folders/{destino_id}" if destino_id else None
                ),
                "pasos": {
                    paso: {
                        "estado": fila["pasos"].get(paso, "pendiente"),
                        "detalle": fila.get("detalles", {}).get(paso, ""),
                        "progreso": fila.get("progreso", {}).get(paso) or dict(vacio["progreso"]),
                    }
                    for paso in PASOS
                },
            })
        return salida
