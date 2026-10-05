"""Verificación del clon frente al origen, sin modificar Drive."""

from __future__ import annotations

import sys, unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import ROOT, drive
from .drive import MIME_FOLDER
from .nombres import es_jpeg_real
from .normalizacion import extension_real, nombre_final_de, origen_de

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


def _ruta_final(ruta: str, item: dict) -> str:
    """Ruta con el NOMBRE FINAL del archivo (base limpia + extensión del formato real)."""
    carpeta = ruta.rsplit("/", 1)[0] if "/" in ruta else ""
    nombre = nombre_final_de(item)
    return f"{carpeta}/{nombre}" if carpeta else nombre


def _lms_en_path() -> None:
    carpeta = str(ROOT / "LMS_Fabrica")
    if carpeta not in sys.path:
        sys.path.insert(0, carpeta)


def _extension_real(item: dict) -> str:
    """Formato real del archivo (mimeType), la misma regla con la que se carga a GCP."""
    return extension_real(item)


def _describir(ext: str) -> str:
    return f"«{ext}»" if ext else "un documento de Google (sin extensión)"


def _es_indexable(ruta: str, programa: str, meta: dict[str, str], parser=None) -> bool:
    if parser is None:
        _lms_en_path()
        from generar_base_rutas import parsear_ruta, parsear_ruta_programa
        partes = [programa, *ruta.split("/")]
        return parsear_ruta_programa(partes, programa, meta) is not None or parsear_ruta(partes) is not None
    return parser([programa, *ruta.split("/")], programa, meta) is not None


def verificar_lote(
    svc, origen_id: str, clon_id: str, *, programa: str, meta: dict[str, str], parser=None, avance=None,
    exigir_vinculo: bool = False,
) -> ResultadoVerificacion:
    """
    Compara el clon con el origen por NOMBRE FINAL (formato real). `avance` es un
    callable(hechos, total, mensaje) opcional que avisa por fases (total = 3:
    inventariar origen, inventariar clon y comparar). Con `avance=None` no cambia nada.

    Con `exigir_vinculo` (carga limpia a fabrica1) cada archivo del clon debe
    traer en `properties` el ID de su archivo de origen, y ese ID debe ser el
    del archivo que le corresponde: sin eso no hay ID ni fecha de origen que cargar.
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
    ori = Counter(_ruta_final(r, i) for r, i in origen.items())
    dst = Counter(_ruta_final(r, i) for r, i in clon.items())
    for ruta, cantidad in sorted((ori - dst).items()):
        resultado.agregar(f"Falta en el clon: {ruta} ({cantidad}).")
    for ruta, cantidad in sorted((dst - ori).items()):
        resultado.agregar(f"Sobra en el clon: {ruta} ({cantidad}).")
    # Dos archivos distintos del origen que al normalizar quedan con el mismo nombre.
    nombres_por_final: dict[str, set[str]] = {}
    for ruta, item in origen.items():
        nombres_por_final.setdefault(_ruta_final(ruta, item), set()).add(item["name"])
    for final, originales in sorted(nombres_por_final.items()):
        if len(originales) > 1:
            resultado.agregar(
                f"Nombre repetido al normalizar: {', '.join(sorted(originales))} quedan como {final}. "
                "Renombra uno en el origen."
            )
    destino_por_canon = {}
    destino_por_origen = {}
    for ruta, item in clon.items():
        destino_por_canon.setdefault(_ruta_final(ruta, item), []).append((ruta, item))
        if origen_de(item):
            destino_por_origen.setdefault(origen_de(item), []).append((ruta, item))
        if es_jpeg_real(item["name"], item.get("mimeType")):
            resultado.agregar(f"Quedó un JPG sin convertir: {ruta}.")
        elif item["name"] != nombre_final_de(item):
            resultado.agregar(f"Nombre sin normalizar: {ruta}; debe quedar {nombre_final_de(item)}.")
        padre = ruta.rsplit("/", 1)[0] if "/" in ruta else ""
        tipo = _tipo_material(padre.rsplit("/", 1)[-1]) if padre else None
        permitidas = EXTENSIONES_POR_TIPO.get(tipo or "")
        if permitidas:
            # Se mira lo que el archivo ES, no cómo se llama: un PDF llamado .png
            # en PORTADA MATERIA es un error aunque el nombre parezca correcto.
            real = _extension_real(item)
            if real not in permitidas:
                resultado.agregar(
                    f"Archivo mal ubicado en {tipo}: {ruta}; el archivo es {_describir(real)} "
                    f"y se espera {', '.join(sorted(permitidas))}."
                )
        if not _es_indexable(ruta, programa, meta, parser):
            resultado.agregar(f"Archivo no indexable: {ruta}.")
    ids_origen = {item["id"] for item in origen.values()}
    if exigir_vinculo:
        for ruta, item in sorted(clon.items()):
            vinculo = origen_de(item)
            if not vinculo:
                resultado.agregar(f"Sin vínculo con el origen: {ruta}. Vuelve a clonar en una carpeta nueva.")
            elif vinculo not in ids_origen:
                resultado.agregar(f"El vínculo con el origen no corresponde a este lote: {ruta}.")
    for ruta, item in origen.items():
        # Primero por el ID guardado en la copia; si no lo tiene, por ruta y nombre final.
        vinculados = destino_por_origen.get(item["id"], [])
        pares = vinculados or destino_por_canon.get(_ruta_final(ruta, item), [])
        if not pares:
            continue
        ruta_copia, copia = pares.pop(0)
        if exigir_vinculo and vinculados and ruta_copia.rsplit("/", 1)[0] != _ruta_final(ruta, item).rsplit("/", 1)[0]:
            resultado.agregar(f"Copia en otra carpeta: {ruta_copia}; su origen está en {ruta}.")
        if es_jpeg_real(item["name"], item.get("mimeType")):
            # En el clon se convirtió: el contenido cambia a propósito, solo se exige que no esté vacío.
            if int(copia.get("size") or 0) <= 0:
                resultado.agregar(f"PNG convertido sin contenido: {_ruta_final(ruta, item)}.")
        elif item.get("md5Checksum") and (item.get("size"), item.get("md5Checksum")) != (copia.get("size"), copia.get("md5Checksum")):
            resultado.agregar(f"Contenido distinto al origen: {ruta}.")
    informar(3, resultado.texto())
    return resultado
