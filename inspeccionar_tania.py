from collections import Counter

from flujo_lib import drive


ORIGEN = "1q9PVVctOkDCtR9aAWi8IdBJqpR8_bjgJ"
DESTINO = "1kyDbAyCs5FWDt9p4KVr2Ee5vI-CZyB7o"


def hijos_resumen(svc, folder_id, nivel=0, max_nivel=1):
    items = drive.listar_hijos(svc, folder_id)
    carpetas = [x for x in items if x.get("mimeType") == drive.MIME_FOLDER]
    archivos = [x for x in items if x.get("mimeType") != drive.MIME_FOLDER]
    print("  " * nivel + f"{folder_id}: carpetas={len(carpetas)} archivos={len(archivos)}", flush=True)
    for carpeta in carpetas:
        print("  " * nivel + f"- {carpeta['name']} [{carpeta['id']}]", flush=True)
        if nivel < max_nivel:
            hijos_resumen(svc, carpeta["id"], nivel + 1, max_nivel)


def contar(svc, folder_id):
    pendientes = [folder_id]
    carpetas = archivos = vinculados = 0
    mimes = Counter()
    while pendientes:
        for item in drive.listar_hijos(svc, pendientes.pop()):
            if item.get("mimeType") == drive.MIME_FOLDER:
                carpetas += 1
                pendientes.append(item["id"])
            else:
                archivos += 1
                vinculados += bool((item.get("properties") or {}).get("origen_id"))
                mimes[item.get("mimeType") or ""] += 1
    return carpetas, archivos, vinculados, mimes


def main():
    svc = drive.construir_servicio(drive.cargar_credenciales())
    for nombre, folder_id in (("ORIGEN", ORIGEN), ("DESTINO", DESTINO)):
        meta = drive.obtener_carpeta(svc, folder_id)
        print(f"=== {nombre}: {meta.get('name')} [{folder_id}] ===", flush=True)
        hijos_resumen(svc, folder_id)


if __name__ == "__main__":
    main()
