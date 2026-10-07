from pathlib import Path

import openpyxl

from flujo_lib import drive
from flujo_lib.gcp import escuela_oficial


ORIGEN = "1q9PVVctOkDCtR9aAWi8IdBJqpR8_bjgJ"
DESTINO = "1kyDbAyCs5FWDt9p4KVr2Ee5vI-CZyB7o"
SALIDA = Path("RUTAS_fabrica1_TANIA_2026-10-07.xlsx")


def main():
    svc = drive.construir_servicio(drive.cargar_credenciales())
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "RUTAS"
    ws.append(("cliente", "etiqueta", "origen", "destino", "escuela"))
    escuelas = [x for x in drive.listar_hijos(svc, ORIGEN, solo_carpetas=True)]
    total = 0
    for escuela_src in escuelas:
        nombre_oficial = escuela_oficial(escuela_src["name"])
        escuela_dst, creada = drive.crear_carpeta(svc, nombre_oficial, DESTINO)
        programas = drive.listar_hijos(svc, escuela_src["id"], solo_carpetas=True)
        print(f"{nombre_oficial}: destino={'creado' if creada else 'reutilizado'}, programas={len(programas)}", flush=True)
        for programa in programas:
            ws.append(("TANIA", programa["name"], programa["id"], escuela_dst["id"], nombre_oficial))
            total += 1
    wb.save(SALIDA)
    print(f"EXCEL={SALIDA} LOTES={total}", flush=True)


if __name__ == "__main__":
    main()
