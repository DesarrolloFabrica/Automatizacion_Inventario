"""Verificación de solo lectura posterior al COMMIT de la promoción."""

from __future__ import annotations

import argparse, json
from datetime import datetime, timezone

from flujo_lib import gcp
from flujo_lib.prevalidacion import cargar_env
from preparar_migracion_fabrica import TABLAS, consulta_huella, nombre


def main():
    from pathlib import Path
    parser = argparse.ArgumentParser()
    parser.add_argument("carpeta",type=Path)
    args=parser.parse_args()
    manifest=json.loads((args.carpeta/"manifest.json").read_text(encoding="utf-8"))
    cargar_env()
    conn=gcp.conectar_desde_env()
    conn.set_session(readonly=True,isolation_level="REPEATABLE READ")
    informe={"fecha_utc":datetime.now(timezone.utc).isoformat(),"tablas":{},"secuencias":{}}
    try:
        with conn.cursor() as cur:
            for tabla in TABLAS:
                huellas=[]
                for esquema in ("fabrica","fabrica1"):
                    cur.execute(consulta_huella(esquema,tabla));huellas.append(list(cur.fetchone()))
                if huellas[0]!=huellas[1] or huellas[0]!=manifest["huellas"][f"fabrica1.{tabla}"]:
                    raise RuntimeError(f"Diferencia de contenido en {tabla}")
                informe["tablas"][tabla]={"filas":huellas[0][0],"huella":huellas[0][1],"coincide":True}
                cur.execute("SELECT pg_get_serial_sequence(%s,'id')",(f"fabrica.{tabla}",))
                secuencia=cur.fetchone()[0]
                if secuencia:
                    esquema_seq,tabla_seq=secuencia.split(".")
                    cur.execute(f"SELECT last_value,is_called FROM {nombre(esquema_seq,tabla_seq)}")
                    valor,llamada=cur.fetchone()
                    cur.execute(f"SELECT COALESCE(MAX(id),0) FROM {nombre('fabrica',tabla)}")
                    maximo=cur.fetchone()[0]
                    siguiente=valor+1 if llamada else valor
                    if siguiente<=maximo:
                        raise RuntimeError(f"Secuencia atrasada: {secuencia}")
                    informe["secuencias"][secuencia]={"maximo":maximo,"siguiente":siguiente}
            for anterior,nuevo,programa in manifest["mapa_programas"]:
                cur.execute("SELECT nombre FROM fabrica.programa WHERE id=%s",(nuevo,))
                if cur.fetchone()!=(programa,):
                    raise RuntimeError("Correspondencia de programa incorrecta")
            cur.execute("""SELECT COUNT(*),COUNT(*) FILTER (WHERE p.id IS NULL)
                FROM edu_content.programa ep LEFT JOIN fabrica.programa p ON p.id=ep.fabrica_programa_id
                WHERE ep.fabrica_programa_id IS NOT NULL""")
            referencias,huerfanas=cur.fetchone()
            if referencias!=manifest["referencias_programa"] or huerfanas:
                raise RuntimeError("Referencias externas inconsistentes")
            informe["referencias_externas"]={"programas":referencias,"huerfanas":huerfanas}
            cur.execute("""SELECT COUNT(*) FROM pg_constraint co JOIN pg_class c ON c.oid=co.conrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname IN ('fabrica','edu_content') AND NOT co.convalidated""")
            if cur.fetchone()[0]:
                raise RuntimeError("Hay restricciones sin validar")
            cur.execute("""SELECT t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='fabrica' AND c.relname='archivo' AND t.tgname='trg_recurso_moodle'""")
            if cur.fetchone()!=("O",):
                raise RuntimeError("El trigger Moodle no esta habilitado")
            cur.execute("""SELECT p.proname,pg_get_functiondef(p.oid) FROM pg_proc p
                JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='fabrica'
                AND p.proname IN ('fn_asignar_recurso_moodle','upsert_archivo')""")
            funciones=cur.fetchall()
            if len(funciones)!=2 or any("fabrica1" in definicion for _,definicion in funciones):
                raise RuntimeError("Las funciones oficiales apuntan al esquema temporal")
            cur.execute("SELECT has_schema_privilege('Consulta_lms','fabrica','USAGE'),has_table_privilege('Consulta_lms','fabrica.archivo','SELECT')")
            informe["permisos_consulta_lms"]=cur.fetchone()
            if not all(informe["permisos_consulta_lms"]):
                raise RuntimeError("Faltan permisos de consulta")
            cur.execute("SELECT COUNT(*) FROM fabrica.v_inventario_archivos")
            informe["filas_vista_inventario"]=cur.fetchone()[0]
    finally:
        conn.rollback();conn.close()
    (args.carpeta/"verificacion_final.json").write_text(json.dumps(informe,indent=2),encoding="utf-8")
    for tabla,estado in informe["tablas"].items():print(tabla,estado["filas"],"COINCIDE")
    print("Referencias externas:",informe["referencias_externas"])
    print("Permisos Consulta_lms:",informe["permisos_consulta_lms"])
    print("VERIFICACION FINAL CORRECTA")


if __name__ == "__main__":
    main()
