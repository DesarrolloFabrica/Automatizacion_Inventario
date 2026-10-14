"""
Arma el RUTAS.xlsx del cargue LMS_CORRECCIONES a partir de Drive.

Origen: LMS_CORRECCIONES / ESCUELA_… / PROGRAMA / …
Un lote por programa. En el destino se crea (o reutiliza) la carpeta de la
escuela con su nombre oficial; la carpeta del programa la crea el run único.
"""

from pathlib import Path

import openpyxl

from flujo_lib import drive
from flujo_lib.excel import norm_text
from flujo_lib.gcp import ESCUELAS_OFICIALES, escuela_oficial


ORIGEN = "1LexISrqWxiC3tAgfq3SQjyL6Pxh_0-GM"
DESTINO = "1q-BHftdlUizV0-swrlZcmzYXsATjYR4l"
CLIENTE = "LMS_CORRECCIONES"
SALIDA = Path("RUTAS_fabrica1_LMS_CORRECCIONES_2026-10-09.xlsx")


def main():
    svc = drive.construir_servicio(drive.cargar_credenciales())
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "RUTAS"
    ws.append(("cliente", "etiqueta", "origen", "destino", "escuela"))
    total = 0
    for escuela_src in drive.listar_hijos(svc, ORIGEN, solo_carpetas=True):
        nombre_oficial = escuela_oficial(norm_text(escuela_src["name"]))
        if nombre_oficial not in ESCUELAS_OFICIALES:
            raise SystemExit(
                f"La carpeta «{escuela_src['name']}» no corresponde a ninguna escuela oficial."
            )
        escuela_dst, creada = drive.crear_carpeta(svc, nombre_oficial, DESTINO)
        programas = drive.listar_hijos(svc, escuela_src["id"], solo_carpetas=True)
        print(
            f"{nombre_oficial}: destino={'creado' if creada else 'reutilizado'}, "
            f"programas={len(programas)}",
            flush=True,
        )
        for programa in programas:
            ws.append(
                (CLIENTE, programa["name"], programa["id"], escuela_dst["id"], nombre_oficial)
            )
            total += 1
    wb.save(SALIDA)
    print(f"EXCEL={SALIDA} LOTES={total}", flush=True)


if __name__ == "__main__":
    main()
