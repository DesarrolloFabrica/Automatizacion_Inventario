"""Ejecuta el ensayo con ROLLBACK o la migración preparada y respaldada."""

from __future__ import annotations

import argparse, hashlib, json, os, subprocess
from datetime import datetime, timezone
from pathlib import Path

from flujo_lib.prevalidacion import cargar_env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("carpeta", type=Path)
    parser.add_argument("--aplicar", action="store_true")
    args = parser.parse_args()
    carpeta = args.carpeta
    manifest = json.loads((carpeta/"manifest.json").read_text(encoding="utf-8"))
    if hashlib.sha256((carpeta/"antes.dump").read_bytes()).hexdigest() != manifest["respaldo_sha256"]:
        raise RuntimeError("La huella del respaldo no coincide")
    ensayo = (carpeta/"ensayo.sql").read_text(encoding="utf-8")
    aplicar = (carpeta/"aplicar.sql").read_text(encoding="utf-8")
    if ensayo.removesuffix("ROLLBACK;\n") != aplicar.removesuffix("COMMIT;\n"):
        raise RuntimeError("El ensayo y la migracion no contienen las mismas operaciones")
    huella = hashlib.sha256(aplicar.encode()).hexdigest()
    if args.aplicar:
        prueba = json.loads((carpeta/"ensayo_correcto.json").read_text(encoding="utf-8"))
        if prueba["sql_sha256"] != huella:
            raise RuntimeError("El SQL cambio despues del ensayo")
    cargar_env()
    entorno = os.environ.copy()
    entorno.update(PGHOST=os.environ["DB_HOST"], PGPORT=os.environ.get("DB_PORT","5432"),
                   PGDATABASE=os.environ["DB_NAME"], PGUSER=os.environ["DB_USER"],
                   PGPASSWORD=os.environ["DB_PASSWORD"], PGSSLMODE=os.environ.get("DB_SSLMODE","require"),
                   PGCONNECT_TIMEOUT="30", PGCLIENTENCODING="UTF8")
    print("Aplicando migracion..." if args.aplicar else "Ensayando con ROLLBACK...", flush=True)
    # psql transmite una sentencia a la vez y detiene el script al primer error.
    # Cerrar su conexion antes del COMMIT revierte la transaccion.
    subprocess.run([r"C:\Program Files\PostgreSQL\18\bin\psql.exe", "--no-psqlrc",
                    "--set=ON_ERROR_STOP=1", "--dbname=keepalives=1 keepalives_idle=20 keepalives_interval=10 keepalives_count=3",
                    "--file="+str(carpeta/("aplicar.sql" if args.aplicar else "ensayo.sql"))],
                   env=entorno, check=True)
    print("COMMIT confirmado." if args.aplicar else "Ensayo validado y revertido.", flush=True)
    informe = {"sql_sha256": huella, "fecha_utc": datetime.now(timezone.utc).isoformat(),
               "aplicado": args.aplicar}
    (carpeta/("aplicacion_correcta.json" if args.aplicar else "ensayo_correcto.json")).write_text(
        json.dumps(informe,indent=2),encoding="utf-8")


if __name__ == "__main__":
    main()
