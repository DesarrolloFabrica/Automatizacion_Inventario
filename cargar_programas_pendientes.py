"""Carga puntual autorizada: tres programas de Drive a fabrica, sin correos."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from flujo_lib import drive, gcp
from flujo_lib.prevalidacion import cargar_env

CARPETA = Path('corridas/carga_tres_programas_20261022')
PROGRAMAS = [
    ('DISENO_DE_ESPACIOS', '1dJ8ayQjVaNfEsX_UO0s2kWETHQ-WEKWD', '1RFFfIpnUuhB-FAeEma2c1bvcjN-ldAWg', 'ESCUELA_DE_DISENO_Y_COMUNICACION'),
    ('GERONTOLOGIA', '1UlMlpYflCGLZypO5SO9qIzl0jCxtZCuI', '1QcQgmF3F2ov6T5Fk1b7L3NUXPP1jsvAI', 'ESCUELA_DE_SALUD_Y_BIENESTAR'),
    ('QUIMICA_FARMACEUTICA', '1HwN_ZqSgbOfst_6Z6Fsx5foLaur-3xbD', '1G1le_ESmV-lJX-Ononx0qCqUH9aNDBzc', 'ESCUELA_DE_SALUD_Y_BIENESTAR'),
]


def main():
    cargar_env()
    CARPETA.mkdir(exist_ok=True)
    creds = drive.cargar_credenciales(interactivo=False)
    svc = drive.construir_servicio(creds)
    svc._http.timeout = 30
    _, rutas = gcp._heredados()
    listar_original = drive.listar_hijos
    contador = 0
    cache = {}
    local = threading.local()

    def leer_carpeta(fid):
        if not hasattr(local, 'svc'):
            local.svc = drive.construir_servicio(creds)
            local.svc._http.timeout = 30
        return fid, listar_original(local.svc, fid)

    def listar(*args, **kwargs):
        nonlocal contador
        fid = args[1]
        if fid not in cache:
            pendientes = [fid]
            with ThreadPoolExecutor(max_workers=8) as pool:
                while pendientes:
                    siguientes = []
                    for carpeta, hijos in pool.map(leer_carpeta, pendientes):
                        cache[carpeta] = hijos
                        contador += 1
                        siguientes.extend(h['id'] for h in hijos if h['mimeType'] == drive.MIME_FOLDER)
                    pendientes = siguientes
                    print(f'Carpetas leidas: {contador}', flush=True)
        return cache[fid]

    drive.listar_hijos = listar
    rutas.listar_hijos = listar
    todas = []
    for nombre, origen, destino, escuela in PROGRAMAS:
        archivo = CARPETA / f'{nombre}.filas.json'
        if archivo.exists():
            filas = json.loads(archivo.read_text(encoding='utf-8'))
        else:
            print(f'ESCANEO {nombre}', flush=True)
            lote = SimpleNamespace(cliente_gcp='PRODUCTO', raiz_gcp='LMS_Carga', escuela_gcp=escuela, origen_id=origen)
            filas = gcp.escanear_lote(svc, destino, lote, nombre, con_origen=True)
            archivo.write_text(json.dumps(filas, ensure_ascii=False, indent=2), encoding='utf-8')
        problemas = gcp.problemas_filas_limpias(filas)
        print(f'VALIDACION {nombre}: {len(filas)} archivos, {len(problemas)} problemas', flush=True)
        if problemas:
            (CARPETA / f'{nombre}.problemas.json').write_text(json.dumps(problemas, ensure_ascii=False, indent=2), encoding='utf-8')
            raise RuntimeError(f'Validacion fallida: {problemas[:3]}')
        if not filas:
            raise RuntimeError(f'Programa vacio: {nombre}')
        todas.extend(filas)
    print(f'CARGA fabrica: {len(todas)} archivos', flush=True)
    def avance(hechos, total, mensaje):
        if hechos % 250 == 0 or hechos == total:
            print(f'Carga {hechos}/{total}', flush=True)
    resultado = gcp.cargar_lote(todas, schema='fabrica', avance=avance)
    (CARPETA / 'resultado.json').write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(resultado, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
