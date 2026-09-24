"""Verificación del clon frente al origen, sin modificar Drive."""

from __future__ import annotations

import sys, unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import ROOT, drive
from .drive import MIME_FOLDER
from .nombres import es_jpg, extension, nombre_canonico

# Qué extensiones son válidas dentro de cada tipo de carpeta.
#
# Contrastado contra el material real de la fábrica (2026-09-24, programas de
# Derecho y Gerontología): en ACTIVIDADES MOODLE conviven txt y docx, y ambas
# formas son correctas. Los demás tipos no aparecieron en ese material; se
# mantienen porque pueden existir en otros programas, y una regla que nunca
# coincide no estorba.
#
# Si algún día aparece un tipo nuevo, es preferible dejarlo sin regla a ponerle
# una estrecha: una regla equivocada genera cientos de avisos falsos que tapan
# los problemas de verdad.
EXTENSIONES_POR_TIPO = {
    "ACTIVIDADES MOODLE": {"txt", "docx"}, "SCORM": {"zip"}, "PDF": {"pdf"},
    "FICHAS": {"pdf"}, "REVISTA": {"pdf"}, "GLOSARIO": {"pdf"},
    "PORTADA MATERIA": {"png"}, "PODCAST": {"mp3"},
}


@dataclass
class ResultadoVerificacion:
    estado: str = "ok"
    hallazgos: list[str] = field(default_factory=list)

    def agregar(self, texto: str) -> None:
        self.estado = "con_diferencias"
        self.hallazgos.append(texto)

    def texto(self) -> str:
        return "Verificación correcta." if self.estado == "ok" else f"{len(self.hallazgos)} diferencia(s)."


def _norm(texto: str) -> str:
    valor = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in valor if not unicodedata.combining(c)).strip().rstrip(".").upper()


def _tipo_material(nombre: str) -> str | None:
    normal = _norm(nombre)
    if "MOODLE" in normal:
        return "ACTIVIDADES MOODLE"
    return normal if normal in {"CONTENIDOS", "SCORM", "PDF", "FICHAS", "REVISTA", "GLOSARIO", "PORTADA MATERIA", "PODCAST", "GUION GRAFICO", "GUION PODCAST", "QA"} else None


def _inventariar(svc, raiz_id: str) -> tuple[dict[str, dict], dict[str, str]]:
    archivos, carpetas = {}, {"": ""}
    def rec(cid: str, ruta: str) -> None:
        for item in drive.listar_hijos(svc, cid):
            rel = f"{ruta}/{item['name']}" if ruta else item["name"]
            if item.get("mimeType") == MIME_FOLDER:
                carpetas[rel] = item["name"]
                rec(item["id"], rel)
            else:
                archivos[rel] = item
    rec(raiz_id, "")
    return archivos, carpetas


def _canon_ruta(ruta: str) -> str:
    partes = ruta.split("/")
    partes[-1] = nombre_canonico(partes[-1])
    return "/".join(partes)


def _es_indexable(ruta: str, programa: str, meta: dict[str, str], parser=None) -> bool:
    if parser is None:
        carpeta = str(ROOT / "LMS_Fabrica")
        if carpeta not in sys.path:
            sys.path.insert(0, carpeta)
        from generar_base_rutas import parsear_ruta, parsear_ruta_programa
        partes = [programa, *ruta.split("/")]
        return parsear_ruta_programa(partes, programa, meta) is not None or parsear_ruta(partes) is not None
    return parser([programa, *ruta.split("/")], programa, meta) is not None


def verificar_lote(svc, origen_id: str, clon_id: str, *, programa: str, meta: dict[str, str], parser=None, avance=None) -> ResultadoVerificacion:
    """
    Compara el clon con el origen. `avance` es un callable(hechos, total, mensaje)
    opcional que avisa por fases (total = 3: inventariar origen, inventariar clon
    y comparar). Con `avance=None` no cambia nada.
    """
    def informar(hechos: int, mensaje: str) -> None:
        if avance is not None:
            avance(hechos, 3, mensaje)

    resultado = ResultadoVerificacion()
    informar(0, "Inventariando origen")
    origen, _ = _inventariar(svc, origen_id)
    informar(1, "Inventariando clon")
    clon, _ = _inventariar(svc, clon_id)
    informar(2, "Comparando")
    ori = Counter(_canon_ruta(r) for r in origen)
    dst = Counter(_canon_ruta(r) for r in clon)
    for ruta, cantidad in sorted((ori - dst).items()):
        resultado.agregar(f"Falta en el clon: {ruta} ({cantidad}).")
    for ruta, cantidad in sorted((dst - ori).items()):
        resultado.agregar(f"Sobra en el clon: {ruta} ({cantidad}).")
    destino_por_canon = {}
    for ruta, item in clon.items():
        destino_por_canon.setdefault(_canon_ruta(ruta), []).append((ruta, item))
        if es_jpg(item["name"]):
            resultado.agregar(f"Quedó un JPG sin convertir: {ruta}.")
        padre = ruta.rsplit("/", 1)[0] if "/" in ruta else ""
        tipo = _tipo_material(padre.rsplit("/", 1)[-1]) if padre else None
        permitidas = EXTENSIONES_POR_TIPO.get(tipo or "")
        if permitidas and extension(item["name"]) not in permitidas:
            resultado.agregar(f"Archivo mal ubicado en {tipo}: {ruta}; se espera {', '.join(sorted(permitidas))}.")
        if not _es_indexable(ruta, programa, meta, parser):
            resultado.agregar(f"Archivo no indexable: {ruta}.")
    for ruta, item in origen.items():
        pares = destino_por_canon.get(_canon_ruta(ruta), [])
        if not pares:
            continue
        _, copia = pares.pop(0)
        if es_jpg(item["name"]):
            if int(copia.get("size") or 0) <= 0:
                resultado.agregar(f"PNG convertido sin contenido: {_canon_ruta(ruta)}.")
        elif item.get("md5Checksum") and (item.get("size"), item.get("md5Checksum")) != (copia.get("size"), copia.get("md5Checksum")):
            resultado.agregar(f"Contenido distinto al origen: {ruta}.")
    informar(3, resultado.texto())
    return resultado
