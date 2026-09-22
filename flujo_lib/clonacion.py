"""
flujo_lib/clonacion.py — clonación reanudable origen → destino con nombre canónico.
-----------------------------------------------------------------------------------
Port de CLONACION_CARPETA/clone_carpeta_drive.py (indice_destino, _esperar_indice,
_quitar_duplicados_en_carpeta, _copiar_archivo_sin_duplicar, copiar_arbol,
igualar_arbol) hacia una sola entrada: clonar_arbol(). Ese código ya está
probado en producción, así que la lógica se conserva a propósito.

QUÉ SE CONSERVÓ (igual que el script original):
  - Dos pasadas: copiar_arbol (recursiva: reusa subcarpetas, omite lo que ya
    está, copia lo que falta y limpia duplicados) y luego igualar_arbol (espera a
    que Drive indexe, quita duplicados de más, completa archivos y carpetas
    faltantes y baja recursivamente).
  - Índice del destino nombre → lista de IDs; los "cupos" del origen son
    Counter por nombre; se conservan los primeros `keep` IDs y el resto va a la
    papelera (nunca borrado permanente).
  - Copia con files().copy(body={name, parents}, fields="id").execute(num_retries=0)
    en bucle de 12 intentos; tras un fallo transitorio se relee el índice y, si la
    cantidad con ese nombre creció, se cuenta como copiada sin duplicar.
    400/401/403/404 (y cualquier HttpError no transitorio) -> fallido sin reintentar.
  - Creación de subcarpetas con ejecutar(files().create(...)) (con reintentos).
  - _esperar_indice: hasta 8 lecturas con 1,5 s entre ellas.
  - Backoff min(2**i * (0.4..0.8), 90 s) entre reintentos de copia.
  - Las mismas líneas informativas: "  [carpeta] X", "  [carpeta] X (reanudar)",
    "  [archivo] Y", "  [archivo] Y (ya existe, omito)", "  [quitar duplicado] Y",
    "  [quitar carpeta duplicada] X", "  [completar archivo] Y",
    "  [completar carpeta] X", "  [ERROR] no se pudo copiar Y: ...".
  - Nunca se escribe nada bajo origen_id: solo se lista.

QUÉ CAMBIÓ (solo lo que pide el contrato del run único):
  - Los ARCHIVOS se agrupan por nombres.nombre_canonico (base intacta,
    extensión en minúscula, jpg/jpeg -> png) tanto en el índice del destino como
    en los cupos del origen y en los contadores visto/usados. Así "x.JPG" del
    origen se considera presente si el clon tiene "x.png" (ya convertido).
    Las CARPETAS siguen agrupándose por nombre exacto.
  - La copia sigue usando el nombre ORIGINAL del archivo (body name=nombre).
  - print(..., flush=True) -> log(...) inyectable; time.sleep -> dormir inyectable;
    listar_hijos/ejecutar/_enviar_a_papelera -> los de flujo_lib.drive, inyectables.
  - El dict contador -> dataclass ResumenClon con los mismos conteos más la lista
    `fallidos` de rutas relativas ("sub/carpeta/archivo.ext"). Para eso las
    funciones reciben la ruta relativa de la carpeta que están procesando.
  - Contabilidad de fallidos: `fallidos` guarda rutas únicas y archivos_fallidos
    == len(fallidos). Si un archivo falló en la primera pasada y la segunda lo
    completó, se quita de fallidos (el original contaba cada intento fallido; aquí
    ok() debe reflejar el estado final del clon).
  - Al quitar duplicados se registra en el log el nombre real del archivo (no
    solo el canónico) cuando se conoce.
"""

from __future__ import annotations

import functools, http.client, random, socket, ssl, time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from googleapiclient.errors import HttpError

from . import drive
from .drive import MIME_FOLDER
from .nombres import nombre_canonico

_ERRORES_RED = (
    http.client.IncompleteRead,
    http.client.RemoteDisconnected,
    socket.timeout,
    socket.gaierror,
    socket.error,
    ConnectionError,
    OSError,
    ssl.SSLError,
    TimeoutError,
)


@dataclass
class ResumenClon:
    """Conteos de una clonación (equivalen al dict `contador` del script original)."""

    carpetas_nuevas: int = 0
    carpetas_reusadas: int = 0
    archivos_copiados: int = 0
    archivos_omitidos: int = 0
    archivos_fallidos: int = 0
    carpetas_duplicadas_quitadas: int = 0
    archivos_duplicados_quitados: int = 0
    fallidos: list[str] = field(default_factory=list)  # rutas relativas "sub/carpeta/archivo.ext"

    def ok(self) -> bool:
        """True si ningún archivo quedó sin copiar."""
        return self.archivos_fallidos == 0

    def texto(self) -> str:
        """Resumen en una línea para el log, el estado y el correo."""
        partes = [
            f"{self.carpetas_nuevas} carpeta(s) nueva(s)",
            f"{self.carpetas_reusadas} carpeta(s) reutilizada(s)",
            f"{self.archivos_copiados} archivo(s) copiado(s)",
            f"{self.archivos_omitidos} archivo(s) ya existente(s)",
        ]
        if self.archivos_fallidos:
            partes.append(f"{self.archivos_fallidos} archivo(s) sin copiar")
        if self.carpetas_duplicadas_quitadas:
            partes.append(f"{self.carpetas_duplicadas_quitadas} carpeta(s) duplicada(s) a la papelera")
        if self.archivos_duplicados_quitados:
            partes.append(f"{self.archivos_duplicados_quitados} archivo(s) duplicado(s) a la papelera")
        return ", ".join(partes) + "."


@dataclass
class _Sesion:
    """Lo que comparten todas las funciones de una clonación (en vez de globales)."""

    svc: object
    log: object
    listar: object
    ejecutar: object
    dormir: object
    resumen: ResumenClon
    nombres: dict = field(default_factory=dict)  # id -> nombre real (para el log)

    def registrar_copia(self, ruta: str) -> None:
        self.resumen.archivos_copiados += 1
        if ruta in self.resumen.fallidos:
            # Falló en la primera pasada y la segunda lo completó: ya no está fallido.
            self.resumen.fallidos.remove(ruta)
            self.resumen.archivos_fallidos = len(self.resumen.fallidos)

    def registrar_fallo(self, ruta: str) -> None:
        if ruta not in self.resumen.fallidos:
            self.resumen.fallidos.append(ruta)
        self.resumen.archivos_fallidos = len(self.resumen.fallidos)


def _espera(intento: int) -> float:
    return min(2**intento * (0.4 + random.random() * 0.4), 90.0)


def _unir(ruta: str, nombre: str) -> str:
    return f"{ruta}/{nombre}" if ruta else nombre


def indice_destino(s: _Sesion, parent_id: str) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """
    Índice en memoria de lo que YA hay en una carpeta destino.

    Devuelve dos mapas nombre → lista de IDs:
      - carpetas: por nombre exacto (para reanudar o detectar duplicados)
      - archivos: por nombre CANÓNICO (para omitir si ya está o contar cuántos hay)

    Varios IDs con el mismo nombre = posibles duplicados de corridas previas.
    """
    carpetas: dict[str, list[str]] = {}
    archivos: dict[str, list[str]] = {}
    for h in s.listar(s.svc, parent_id):
        n, mid = h["name"], h["mimeType"]
        s.nombres[h["id"]] = n
        if mid == MIME_FOLDER:
            carpetas.setdefault(n, []).append(h["id"])
        else:
            archivos.setdefault(nombre_canonico(n), []).append(h["id"])
    return carpetas, archivos


def _esperar_indice(
    s: _Sesion, parent_id: str, min_archivos: int, min_carpetas: int, intentos: int = 8
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """
    Drive a veces tarda en indexar lo recién copiado.
    Relee el destino hasta ver al menos min_archivos / min_carpetas
    (o agotar intentos). Evita crear/copiar de más por un listado atrasado.
    """
    ultimo: tuple[dict[str, list[str]], dict[str, list[str]]] | None = None
    for i in range(intentos):
        carpetas, archivos = indice_destino(s, parent_id)
        n_arc = sum(len(v) for v in archivos.values())
        n_car = sum(len(v) for v in carpetas.values())
        ultimo = (carpetas, archivos)
        if n_arc >= min_archivos and n_car >= min_carpetas:
            return ultimo
        if i < intentos - 1:
            s.dormir(1.5)
    return ultimo if ultimo is not None else indice_destino(s, parent_id)


def _quitar_duplicados_en_carpeta(
    s: _Sesion,
    destino_parent_id: str,
    cupo_archivos: Counter,
    cupo_carpetas: Counter,
    dest_carpetas: dict[str, list[str]],
    dest_archivos: dict[str, list[str]],
) -> None:
    """
    Deja en destino exactamente las cantidades del origen (cupo_*).

    Flujo:
      1) Refresca el índice real de Drive en dest_carpetas / dest_archivos.
      2) Por cada nombre: conserva los primeros ``keep`` IDs; el resto → papelera.
      3) Actualiza los contadores de duplicados quitados.

    Así una reanudación no acumula copias de más del mismo nombre.
    """
    # Refresco: lo que Drive muestra ahora (puede haber cambiado tras copias).
    ahora_c, ahora_a = indice_destino(s, destino_parent_id)
    dest_carpetas.clear()
    dest_carpetas.update(ahora_c)
    dest_archivos.clear()
    dest_archivos.update(ahora_a)
    for nombre, ids in list(dest_carpetas.items()):
        keep = cupo_carpetas.get(nombre, 0)
        extras = ids[keep:]
        if extras:
            dest_carpetas[nombre] = ids[:keep]
        for fid in extras:
            s.log(f"  [quitar carpeta duplicada] {nombre}")
            drive.enviar_a_papelera(s.svc, fid, ejecutar=s.ejecutar)
            s.resumen.carpetas_duplicadas_quitadas += 1
    for nombre, ids in list(dest_archivos.items()):
        keep = cupo_archivos.get(nombre, 0)
        extras = ids[keep:]
        if extras:
            dest_archivos[nombre] = ids[:keep]
        for fid in extras:
            s.log(f"  [quitar duplicado] {s.nombres.get(fid, nombre)}")
            drive.enviar_a_papelera(s.svc, fid, ejecutar=s.ejecutar)
            s.resumen.archivos_duplicados_quitados += 1


def _copiar_archivo_sin_duplicar(
    s: _Sesion,
    origen_file_id: str,
    nombre: str,
    destino_parent_id: str,
    dest_archivos: dict[str, list[str]],
    ruta: str,
) -> None:
    """
    Copia un archivo al destino evitando duplicados tras timeouts.

    Idea clave: si la API falla por red/timeout, Drive a veces YA creó
    la copia. Antes de reintentar se relee el índice; si la cantidad con
    ese nombre canónico aumentó, se cuenta éxito y NO se vuelve a copiar.
    Errores duros (400/401/403/404) → fallido sin reintentar.
    `ruta` es la ruta relativa del archivo (para la lista de fallidos).
    """
    clave = nombre_canonico(nombre)
    # Cuántos había con este nombre antes de intentar (para detectar "ya está").
    antes = len(dest_archivos.get(clave) or [])
    s.log(f"  [archivo] {nombre}")
    ultimo: Exception | None = None
    for intento in range(12):
        try:
            creado = (
                s.svc.files()
                .copy(
                    fileId=origen_file_id,
                    body={"name": nombre, "parents": [destino_parent_id]},
                    fields="id",
                    supportsAllDrives=True,
                )
                .execute(num_retries=0)
            )
            nid = creado.get("id")
            dest_archivos.setdefault(clave, []).append(nid or "ok")
            if nid:
                s.nombres[nid] = nombre
            s.registrar_copia(ruta)
            return
        except HttpError as e:
            # 400/401/403/404 y cualquier otro código no transitorio: fallido de una.
            if not drive.es_error_transitorio(e):
                s.log(f"  [ERROR] no se pudo copiar {nombre}: {e}")
                s.registrar_fallo(ruta)
                return
            ultimo = e
        except _ERRORES_RED as e:
            ultimo = e
        # Tras fallo temporal: ¿Drive ya tiene el archivo? Entonces no duplicar.
        _, ahora = indice_destino(s, destino_parent_id)
        dest_archivos.clear()
        dest_archivos.update(ahora)
        if len(dest_archivos.get(clave) or []) > antes:
            s.log(f"  [archivo] {nombre} (Drive ya lo tenía tras el timeout; no duplico)")
            s.registrar_copia(ruta)
            return
        if intento < 11:
            s.dormir(_espera(intento))
    s.log(f"  [ERROR] no se pudo copiar {nombre}: {ultimo}")
    s.registrar_fallo(ruta)


def _crear_subcarpeta(s: _Sesion, nombre: str, destino_parent_id: str) -> str:
    """files().create de una subcarpeta con reintentos (como el original). Devuelve el id."""
    creada = s.ejecutar(
        s.svc.files().create(
            body={"name": nombre, "parents": [destino_parent_id], "mimeType": MIME_FOLDER},
            fields="id",
            supportsAllDrives=True,
        )
    )
    s.nombres[creada["id"]] = nombre
    return creada["id"]


def copiar_arbol(s: _Sesion, origen_id: str, destino_parent_id: str, ruta: str = "") -> None:
    """
    Copia recursiva origen → destino.
    - Si la subcarpeta ya existe en destino: reanuda dentro de ella.
    - Si el archivo ya existe (mismo nombre canónico): omite.
    - Si falta: crea carpeta o copia archivo.
    Al final limpia duplicados de más respecto al origen.
    `ruta` es la ruta relativa de la carpeta que se está copiando ("" en la raíz).
    """
    origen_hijos = s.listar(s.svc, origen_id)
    dest_carpetas, dest_archivos = indice_destino(s, destino_parent_id)
    cupo_a = Counter(
        nombre_canonico(h["name"]) for h in origen_hijos if h.get("mimeType") != MIME_FOLDER
    )
    cupo_c = Counter(h["name"] for h in origen_hijos if h.get("mimeType") == MIME_FOLDER)
    visto: dict[str, int] = defaultdict(int)
    for item in origen_hijos:
        nombre = item["name"]
        mid = item["mimeType"]
        if mid == MIME_FOLDER:
            existentes = dest_carpetas.get(nombre) or []
            if existentes:
                s.log(f"  [carpeta] {nombre} (reanudar)")
                s.resumen.carpetas_reusadas += 1
                copiar_arbol(s, item["id"], existentes[0], _unir(ruta, nombre))
            else:
                s.log(f"  [carpeta] {nombre}")
                nueva_id = _crear_subcarpeta(s, nombre, destino_parent_id)
                dest_carpetas.setdefault(nombre, []).append(nueva_id)
                s.resumen.carpetas_nuevas += 1
                copiar_arbol(s, item["id"], nueva_id, _unir(ruta, nombre))
        else:
            clave = nombre_canonico(nombre)
            if visto[clave] < len(dest_archivos.get(clave) or []):
                s.log(f"  [archivo] {nombre} (ya existe, omito)")
                s.resumen.archivos_omitidos += 1
                visto[clave] += 1
                continue
            _copiar_archivo_sin_duplicar(
                s, item["id"], nombre, destino_parent_id, dest_archivos, _unir(ruta, nombre)
            )
            visto[clave] += 1
    _quitar_duplicados_en_carpeta(s, destino_parent_id, cupo_a, cupo_c, dest_carpetas, dest_archivos)


def igualar_arbol(s: _Sesion, origen_id: str, destino_parent_id: str, ruta: str = "") -> None:
    """
    Segunda pasada tras copiar_arbol.
    Espera a que Drive indexe el destino, quita duplicados de más y completa
    archivos/carpetas que aún falten respecto al origen.
    """
    origen_hijos = s.listar(s.svc, origen_id)
    n_ori_a = sum(1 for h in origen_hijos if h.get("mimeType") != MIME_FOLDER)
    n_ori_c = sum(1 for h in origen_hijos if h.get("mimeType") == MIME_FOLDER)
    dest_carpetas, dest_archivos = _esperar_indice(s, destino_parent_id, n_ori_a, n_ori_c)
    cupo_a = Counter(
        nombre_canonico(h["name"]) for h in origen_hijos if h.get("mimeType") != MIME_FOLDER
    )
    cupo_c = Counter(h["name"] for h in origen_hijos if h.get("mimeType") == MIME_FOLDER)
    _quitar_duplicados_en_carpeta(s, destino_parent_id, cupo_a, cupo_c, dest_carpetas, dest_archivos)
    usados: dict[str, int] = defaultdict(int)
    for item in origen_hijos:
        nombre = item["name"]
        if item.get("mimeType") == MIME_FOLDER:
            continue
        clave = nombre_canonico(nombre)
        if usados[clave] < len(dest_archivos.get(clave) or []):
            usados[clave] += 1
            continue
        s.log(f"  [completar archivo] {nombre}")
        _copiar_archivo_sin_duplicar(
            s, item["id"], nombre, destino_parent_id, dest_archivos, _unir(ruta, nombre)
        )
        usados[clave] += 1
    _quitar_duplicados_en_carpeta(s, destino_parent_id, cupo_a, cupo_c, dest_carpetas, dest_archivos)
    for item in origen_hijos:
        if item.get("mimeType") != MIME_FOLDER:
            continue
        nombre = item["name"]
        existentes = dest_carpetas.get(nombre) or []
        if existentes:
            ex_id = existentes[0]
        else:
            s.log(f"  [completar carpeta] {nombre}")
            ex_id = _crear_subcarpeta(s, nombre, destino_parent_id)
            dest_carpetas.setdefault(nombre, []).append(ex_id)
            s.resumen.carpetas_nuevas += 1
        igualar_arbol(s, item["id"], ex_id, _unir(ruta, nombre))


def clonar_arbol(
    svc,
    origen_id: str,
    destino_id: str,
    *,
    log=print,
    listar=drive.listar_hijos,
    ejecutar=drive.ejecutar,
    dormir=time.sleep,
) -> ResumenClon:
    """
    Clona (o resincroniza) el contenido de origen_id dentro de destino_id.

    1) copiar_arbol recursivo y 2) igualar_arbol, exactamente como el script
    original, comparando archivos por nombre canónico. Nunca escribe bajo el origen.
    Si se dejan `ejecutar` y `listar` por defecto, usan el mismo `dormir` (así las
    pruebas no esperan de verdad). Devuelve ResumenClon; no lanza por archivos
    que no se pudieron copiar (quedan en `fallidos`), sí por errores de Drive al
    listar o crear carpetas (400/401/403/404 o red agotada).
    """
    if ejecutar is drive.ejecutar:
        ejecutar = functools.partial(drive.ejecutar, dormir=dormir)
    if listar is drive.listar_hijos:
        listar = functools.partial(drive.listar_hijos, ejecutar=ejecutar)
    s = _Sesion(
        svc=svc, log=log, listar=listar, ejecutar=ejecutar, dormir=dormir, resumen=ResumenClon()
    )
    copiar_arbol(s, origen_id, destino_id)
    s.log("Igualando con el origen (huecos y duplicados)…")
    igualar_arbol(s, origen_id, destino_id)
    return s.resumen
