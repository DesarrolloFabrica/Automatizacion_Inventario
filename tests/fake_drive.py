"""
tests/fake_drive.py — Google Drive falso en memoria para las pruebas.
---------------------------------------------------------------------
Imita el subconjunto de build("drive", "v3") que usa flujo_lib:
files().get / list / create / copy / update / get_media y about().get,
cada uno con .execute(num_retries=0). Nunca toca la red.

Uso típico:

    from tests.fake_drive import FakeDrive
    fake = FakeDrive()
    raiz = fake.agregar_carpeta("RAIZ")
    sub = fake.agregar_carpeta("Sub", raiz)
    fake.agregar_archivo("foto.JPG", sub, contenido=b"...", mime="image/jpeg")
    # La próxima creación se APLICA y luego lanza HttpError 500 (timeout tras crear):
    fake.fallar("create", status=500, veces=1, aplicar_efecto=True)
    # Los próximos dos listados lanzan ConnectionError:
    fake.fallar_red("list", veces=2)
    r = fake.files().list(q=f"'{raiz}' in parents and trashed = false").execute()
    print(fake.llamadas["list"], fake.hijos(raiz))

Consultas (q) soportadas, combinadas con " and " (espacios variables):
  'ID' in parents | trashed = false | trashed = true | mimeType = 'X' |
  mimeType != 'X' | name = 'X' | name != 'X'   (comillas escapadas con \\')
Cualquier otra cláusula lanza ValueError para que el error se vea en la prueba.
Igual que Drive: sin cláusula de trashed se devuelven también los elementos
en papelera; un padre inexistente en 'ID' in parents lanza HttpError 404; los
hijos de una carpeta en papelera se reportan como trashed; `fields` recorta la
respuesta (sin fields: id, name, mimeType, kind).
"""

from __future__ import annotations

import copy, hashlib, json, re
from collections import Counter

import httplib2
from googleapiclient.errors import HttpError

MIME_FOLDER = "application/vnd.google-apps.folder"
OPERACIONES = ("get", "list", "create", "copy", "update", "about", "get_media")
_CAMPOS_POR_DEFECTO = frozenset({"id", "name", "mimeType", "kind"})
_RX_CLAUSULAS = (
    (re.compile(r"^'((?:[^'\\]|\\.)*)'\s+in\s+parents$"), "parents"),
    (re.compile(r"^trashed\s*=\s*(true|false)$", re.I), "trashed"),
    (re.compile(r"^mimeType\s*(!=|=)\s*'((?:[^'\\]|\\.)*)'$"), "mime"),
    (re.compile(r"^name\s*(!=|=)\s*'((?:[^'\\]|\\.)*)'$"), "name"),
)


def hacer_http_error(status: int, mensaje: str = "", uri: str | None = None) -> HttpError:
    """HttpError real de googleapiclient con el código indicado (como lo lanza Drive)."""
    texto = mensaje or f"Fallo simulado {status}"
    contenido = json.dumps(
        {"error": {"code": status, "message": texto, "errors": [{"reason": "simulado", "message": texto}]}}
    ).encode("utf-8")
    return HttpError(httplib2.Response({"status": status, "reason": texto}), contenido, uri=uri)


def _desescapar(valor: str) -> str:
    return re.sub(r"\\(.)", r"\1", valor)


def _dividir_clausulas(q: str) -> list[str]:
    """Separa q por " and " respetando lo que va entre comillas simples."""
    partes: list[str] = []
    actual: list[str] = []
    en_comilla = False
    i = 0
    while i < len(q):
        ch = q[i]
        if en_comilla:
            actual.append(ch)
            if ch == "\\" and i + 1 < len(q):
                actual.append(q[i + 1])
                i += 2
                continue
            if ch == "'":
                en_comilla = False
            i += 1
            continue
        if ch == "'":
            en_comilla = True
            actual.append(ch)
            i += 1
            continue
        m = re.match(r"\s+and\s+", q[i:], flags=re.I)
        if m and "".join(actual).strip():
            partes.append("".join(actual))
            actual = []
            i += m.end()
            continue
        actual.append(ch)
        i += 1
    if actual:
        partes.append("".join(actual))
    return [p.strip() for p in partes if p.strip()]


def _parsear_q(q: str | None) -> dict:
    filtro: dict = {"parents": None, "trashed": None, "mime": [], "name": []}
    if q is None or not q.strip():
        return filtro
    for clausula in _dividir_clausulas(q):
        for rx, tipo in _RX_CLAUSULAS:
            m = rx.match(clausula)
            if not m:
                continue
            if tipo == "parents":
                filtro["parents"] = _desescapar(m.group(1))
            elif tipo == "trashed":
                filtro["trashed"] = m.group(1).lower() == "true"
            else:
                filtro[tipo].append((m.group(1), _desescapar(m.group(2))))
            break
        else:
            raise ValueError(f"FakeDrive no entiende la cláusula de q: {clausula!r}")
    return filtro


def _campos_simples(fields: str | None) -> frozenset | None:
    """Conjunto de campos pedidos en `fields` (get/create/copy/update); None = todos."""
    if fields is None:
        return _CAMPOS_POR_DEFECTO
    texto = fields.strip()
    if texto == "*" or not texto:
        return None
    return frozenset(c.strip() for c in re.split(r"[,\s]+", texto) if c.strip())


def _campos_lista(fields: str | None) -> frozenset | None:
    """Campos pedidos dentro de files(...) en un list; None = todos."""
    if fields is None:
        return _CAMPOS_POR_DEFECTO
    m = re.search(r"files\s*\(([^)]*)\)", fields)
    if not m:
        return None if "*" in fields else _CAMPOS_POR_DEFECTO
    return _campos_simples(m.group(1))


class _Peticion:
    """Equivalente de HttpRequest: la acción se aplica al llamar execute()."""

    def __init__(self, fake: "FakeDrive", operacion: str, accion):
        self._fake = fake
        self._operacion = operacion
        self._accion = accion

    def execute(self, http=None, num_retries: int = 0):
        fake = self._fake
        fake.llamadas[self._operacion] += 1
        fallo = fake._sacar_fallo(self._operacion)
        if fallo is None:
            return self._accion()
        if fallo["aplicar_efecto"]:
            self._accion()
        if fallo["tipo"] == "red":
            raise ConnectionError(f"Fallo de red simulado en {self._operacion}")
        raise hacer_http_error(fallo["status"], uri=f"fake://drive/{self._operacion}")


class _Files:
    def __init__(self, fake: "FakeDrive"):
        self._fake = fake

    def get(self, fileId, fields=None, supportsAllDrives=None, **kw):
        return _Peticion(self._fake, "get", lambda: self._fake._api_get(fileId, fields))

    def list(self, q=None, pageToken=None, pageSize=100, fields=None, **kw):
        return _Peticion(
            self._fake, "list", lambda: self._fake._api_list(q, pageToken, pageSize, fields)
        )

    def create(self, body=None, fields=None, media_body=None, supportsAllDrives=None, **kw):
        return _Peticion(
            self._fake, "create", lambda: self._fake._api_create(body, fields, media_body)
        )

    def copy(self, fileId, body=None, fields=None, supportsAllDrives=None, **kw):
        return _Peticion(self._fake, "copy", lambda: self._fake._api_copy(fileId, body, fields))

    def update(
        self,
        fileId,
        body=None,
        supportsAllDrives=None,
        fields=None,
        addParents=None,
        removeParents=None,
        **kw,
    ):
        return _Peticion(
            self._fake,
            "update",
            lambda: self._fake._api_update(fileId, body, fields, addParents, removeParents),
        )

    def get_media(self, fileId, supportsAllDrives=None, **kw):
        return _Peticion(self._fake, "get_media", lambda: self._fake._api_get_media(fileId))


class _About:
    def __init__(self, fake: "FakeDrive"):
        self._fake = fake

    def get(self, fields=None, **kw):
        return _Peticion(self._fake, "about", self._fake._api_about)


class FakeDrive:
    """Servicio de Drive en memoria compatible con el uso que hace flujo_lib."""

    def __init__(self, cuenta: str = "fabrica.contenidos@cun.edu.co"):
        self.cuenta = cuenta
        self.llamadas: Counter = Counter()
        self._registros: dict[str, dict] = {}
        self._contenidos: dict[str, bytes] = {}
        self._fallos: dict[str, list[dict]] = {op: [] for op in OPERACIONES}
        self._secuencia = 0

    # ----- armado del árbol -------------------------------------------------
    def agregar_carpeta(self, nombre: str, parent_id: str | None = None, *, id: str | None = None) -> str:
        parents = [parent_id] if parent_id else []
        self._exigir_padres(parents, como_404=False)
        return self._nueva_carpeta(nombre, parents, id)["id"]

    def agregar_archivo(
        self,
        nombre: str,
        parent_id: str,
        *,
        contenido: bytes = b"x",
        mime: str = "application/pdf",
        id: str | None = None,
    ) -> str:
        self._exigir_padres([parent_id], como_404=False)
        return self._nuevo_archivo(nombre, [parent_id], contenido, mime, id)["id"]

    def obtener(self, id: str) -> dict:
        """Copia del registro interno (trashed explícito, sin proyección de campos)."""
        if id not in self._registros:
            raise KeyError(f"FakeDrive: no existe el id {id!r}")
        return copy.deepcopy(self._registros[id])

    def hijos(self, parent_id: str, *, incluir_papelera: bool = False) -> list[dict]:
        return [
            copy.deepcopy(r)
            for r in self._registros.values()
            if parent_id in r["parents"] and (incluir_papelera or not self._en_papelera(r))
        ]

    def contenido(self, id: str) -> bytes:
        """Bytes de un archivo (para comprobar copias o conversiones)."""
        if id not in self._contenidos:
            raise KeyError(f"FakeDrive: no existe archivo con id {id!r}")
        return self._contenidos[id]

    # ----- fallos inyectables ----------------------------------------------
    def fallar(self, operacion: str, *, status: int = 500, veces: int = 1, aplicar_efecto: bool = False) -> None:
        """Las próximas `veces` llamadas a `operacion` lanzan HttpError(status)."""
        self._programar_fallo(operacion, {"tipo": "http", "status": int(status), "aplicar_efecto": aplicar_efecto}, veces)

    def fallar_red(self, operacion: str, *, veces: int = 1, aplicar_efecto: bool = False) -> None:
        """Las próximas `veces` llamadas a `operacion` lanzan ConnectionError."""
        self._programar_fallo(operacion, {"tipo": "red", "status": None, "aplicar_efecto": aplicar_efecto}, veces)

    def _programar_fallo(self, operacion: str, fallo: dict, veces: int) -> None:
        if operacion not in OPERACIONES:
            raise ValueError(f"Operación desconocida {operacion!r}; usa una de {OPERACIONES}")
        if veces < 1:
            raise ValueError("veces debe ser >= 1")
        self._fallos[operacion].extend(dict(fallo) for _ in range(veces))

    def _sacar_fallo(self, operacion: str) -> dict | None:
        cola = self._fallos[operacion]
        return cola.pop(0) if cola else None

    # ----- superficie de la API --------------------------------------------
    def files(self) -> _Files:
        return _Files(self)

    def about(self) -> _About:
        return _About(self)

    # ----- registros internos ----------------------------------------------
    def _nuevo_id(self, prefijo: str) -> str:
        self._secuencia += 1
        return f"{prefijo}{self._secuencia:04d}"

    def _registrar(self, reg: dict, id: str | None, prefijo: str) -> dict:
        nuevo_id = id or self._nuevo_id(prefijo)
        if nuevo_id in self._registros:
            raise ValueError(f"FakeDrive: el id {nuevo_id!r} ya existe")
        reg["id"] = nuevo_id
        self._registros[nuevo_id] = reg
        return reg

    def _nueva_carpeta(self, nombre: str, parents: list[str], id: str | None = None) -> dict:
        reg = {
            "id": None,
            "kind": "drive#file",
            "name": nombre,
            "mimeType": MIME_FOLDER,
            "parents": list(parents),
            "trashed": False,
            "size": None,
            "md5Checksum": None,
            "fileExtension": None,
            "webViewLink": None,
        }
        self._registrar(reg, id, "carp")
        reg["webViewLink"] = f"https://drive.google.com/drive/folders/{reg['id']}"
        return reg

    def _nuevo_archivo(
        self, nombre: str, parents: list[str], contenido: bytes, mime: str, id: str | None = None
    ) -> dict:
        contenido = bytes(contenido or b"")
        extension = nombre.rsplit(".", 1)[1] if "." in nombre.strip(".") else None
        reg = {
            "id": None,
            "kind": "drive#file",
            "name": nombre,
            "mimeType": mime or "application/octet-stream",
            "parents": list(parents),
            "trashed": False,
            "size": str(len(contenido)),
            "md5Checksum": hashlib.md5(contenido).hexdigest(),
            "fileExtension": extension,
            "webViewLink": None,
        }
        self._registrar(reg, id, "arch")
        reg["webViewLink"] = f"https://drive.google.com/file/d/{reg['id']}/view"
        self._contenidos[reg["id"]] = contenido
        return reg

    def _en_papelera(self, reg: dict, vistos: set | None = None) -> bool:
        """trashed efectivo: explícito o heredado de un padre en papelera."""
        if reg["trashed"]:
            return True
        vistos = vistos or set()
        for pid in reg["parents"]:
            padre = self._registros.get(pid)
            if padre is None or pid in vistos:
                continue
            vistos.add(pid)
            if self._en_papelera(padre, vistos):
                return True
        return False

    def _exigir_padres(self, parents: list[str], *, como_404: bool = True) -> None:
        for pid in parents:
            if pid not in self._registros:
                if como_404:
                    raise hacer_http_error(404, f"File not found: {pid}.")
                raise KeyError(f"FakeDrive: no existe la carpeta padre {pid!r}")

    def _exigir(self, file_id: str) -> dict:
        reg = self._registros.get(file_id)
        if reg is None:
            raise hacer_http_error(404, f"File not found: {file_id}.")
        return reg

    def _proyectar(self, reg: dict, campos: frozenset | None) -> dict:
        salida = {}
        for clave, valor in reg.items():
            if valor is None or (campos is not None and clave not in campos):
                continue
            salida[clave] = copy.deepcopy(valor)
        if "trashed" in salida:
            salida["trashed"] = self._en_papelera(reg)
        return salida

    def _cumple(self, reg: dict, filtro: dict) -> bool:
        if filtro["parents"] is not None and filtro["parents"] not in reg["parents"]:
            return False
        if filtro["trashed"] is not None and self._en_papelera(reg) != filtro["trashed"]:
            return False
        for op, valor in filtro["mime"]:
            if (reg["mimeType"] == valor) != (op == "="):
                return False
        for op, valor in filtro["name"]:
            if (reg["name"] == valor) != (op == "="):
                return False
        return True

    # ----- operaciones de la API -------------------------------------------
    def _api_get(self, file_id: str, fields) -> dict:
        return self._proyectar(self._exigir(file_id), _campos_simples(fields))

    def _api_list(self, q, page_token, page_size, fields) -> dict:
        filtro = _parsear_q(q)
        if filtro["parents"] is not None:
            self._exigir_padres([filtro["parents"]])
        candidatos = [r for r in self._registros.values() if self._cumple(r, filtro)]
        inicio = int(page_token) if page_token else 0
        tamano = max(1, min(int(page_size or 100), 1000))
        campos = _campos_lista(fields)
        pagina = candidatos[inicio : inicio + tamano]
        respuesta = {
            "kind": "drive#fileList",
            "incompleteSearch": False,
            "files": [self._proyectar(r, campos) for r in pagina],
        }
        if inicio + tamano < len(candidatos):
            respuesta["nextPageToken"] = str(inicio + tamano)
        return respuesta

    def _api_create(self, body, fields, media_body) -> dict:
        body = dict(body or {})
        nombre = body.get("name") or "Sin título"
        mime = body.get("mimeType")
        parents = list(body.get("parents") or [])
        self._exigir_padres(parents)
        contenido = b""
        if media_body is not None:
            try:
                contenido = media_body.getbytes(0, media_body.size())
                mime = mime or media_body.mimetype()
            except Exception:
                contenido = b""
        if mime == MIME_FOLDER:
            reg = self._nueva_carpeta(nombre, parents)
        else:
            reg = self._nuevo_archivo(nombre, parents, contenido, mime or "application/octet-stream")
        return self._proyectar(reg, _campos_simples(fields))

    def _api_copy(self, file_id: str, body, fields) -> dict:
        original = self._exigir(file_id)
        if original["mimeType"] == MIME_FOLDER:
            raise hacer_http_error(400, "Files cannot be copied: folders are not supported.")
        body = dict(body or {})
        parents = list(body.get("parents") or original["parents"])
        self._exigir_padres(parents)
        reg = self._nuevo_archivo(
            body.get("name") or original["name"],
            parents,
            self._contenidos.get(file_id, b""),
            original["mimeType"],
        )
        return self._proyectar(reg, _campos_simples(fields))

    def _api_update(self, file_id: str, body, fields, add_parents, remove_parents) -> dict:
        reg = self._exigir(file_id)
        body = dict(body or {})
        if "trashed" in body:
            reg["trashed"] = bool(body["trashed"])
        if body.get("name"):
            reg["name"] = body["name"]
        if add_parents:
            nuevos = [p for p in str(add_parents).split(",") if p]
            self._exigir_padres(nuevos)
            reg["parents"].extend(p for p in nuevos if p not in reg["parents"])
        if remove_parents:
            quitar = set(str(remove_parents).split(","))
            reg["parents"] = [p for p in reg["parents"] if p not in quitar]
        return self._proyectar(reg, _campos_simples(fields))

    def _api_get_media(self, file_id: str) -> bytes:
        reg = self._exigir(file_id)
        if reg["mimeType"] == MIME_FOLDER:
            raise hacer_http_error(403, "Only files with binary content can be downloaded.")
        return self._contenidos.get(file_id, b"")

    def _api_about(self) -> dict:
        return {
            "kind": "drive#about",
            "user": {"kind": "drive#user", "emailAddress": self.cuenta, "displayName": "Fábrica"},
        }
