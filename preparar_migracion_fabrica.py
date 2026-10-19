"""Respalda y prepara el reemplazo transaccional fabrica1 -> fabrica.

No modifica la base. El SQL generado se ensaya con ROLLBACK antes de aplicarlo.
"""

from __future__ import annotations

import hashlib, json, os, subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from flujo_lib import gcp
from flujo_lib.excel import norm_text
from flujo_lib.prevalidacion import cargar_env

TABLAS = ("cliente", "destinatario", "escuela", "extension", "paquete", "periodo", "raiz",
          "programa", "materia", "granulo", "archivo", "recurso_moodle", "diplomados_costos")
EXTERNAS = ("archivo_docente", "pensum", "periodo", "programa")
BIN = Path(r"C:\Program Files\PostgreSQL\18\bin")


def ident(texto):
    return '"' + texto.replace('"', '""') + '"'


def literal(texto):
    return "'" + str(texto).replace("'", "''") + "'"


def nombre(esquema, tabla):
    return f"{ident(esquema)}.{ident(tabla)}"


def consulta_huella(esquema, tabla):
    return ("SELECT COUNT(*), md5(COALESCE(string_agg(md5(to_jsonb(t)::text), '' ORDER BY id), '')) "
            f"FROM {nombre(esquema, tabla)} t")


def exigir_huella(esquema, tabla, huella):
    cantidad, digest = huella
    return f"""DO $validar$ DECLARE n bigint; h text; BEGIN
  {consulta_huella(esquema, tabla)} INTO n, h;
  IF n <> {cantidad} OR h <> {literal(digest)} THEN
    RAISE EXCEPTION 'La tabla {esquema}.{tabla} no coincide con la instantanea esperada';
  END IF;
END $validar$;"""


def preparar():
    cargar_env()
    sello = datetime.now(ZoneInfo("America/Bogota")).strftime("%Y%m%d_%H%M%S")
    carpeta = Path("corridas") / f"migracion_fabrica_{sello}"
    carpeta.mkdir()
    conn = gcp.conectar_desde_env()
    conn.set_session(readonly=True, isolation_level="REPEATABLE READ")
    informe = {"origen": "fabrica1", "destino": "fabrica", "huellas": {}, "mapa_programas": []}
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout='60s'")
            cur.execute("SELECT pg_export_snapshot()")
            snapshot = cur.fetchone()[0]
            cur.execute("SELECT table_schema,table_name,column_name FROM information_schema.columns "
                        "WHERE table_schema IN ('fabrica','fabrica1') ORDER BY table_schema,table_name,ordinal_position")
            columnas = {}
            for esquema, tabla, columna in cur.fetchall():
                columnas.setdefault(esquema, {}).setdefault(tabla, []).append(columna)
            for esquema in ("fabrica", "fabrica1"):
                if set(columnas[esquema]) != set(TABLAS) | {"v_inventario_archivos"}:
                    raise RuntimeError(f"Objetos inesperados en {esquema}; revisar inventario")
                for tabla in TABLAS:
                    cur.execute(consulta_huella(esquema, tabla))
                    informe["huellas"][f"{esquema}.{tabla}"] = cur.fetchone()
            for tabla in EXTERNAS:
                cur.execute(consulta_huella("edu_content", tabla))
                informe["huellas"][f"edu_content.{tabla}"] = cur.fetchone()
            cur.execute("""SELECT ns.nspname,src.relname,co.conname,pg_get_constraintdef(co.oid),
                nt.nspname,dst.relname FROM pg_constraint co
                JOIN pg_class src ON src.oid=co.conrelid JOIN pg_namespace ns ON ns.oid=src.relnamespace
                JOIN pg_class dst ON dst.oid=co.confrelid JOIN pg_namespace nt ON nt.oid=dst.relnamespace
                WHERE co.contype='f' AND nt.nspname='fabrica' AND ns.nspname<>'fabrica'
                ORDER BY 1,2,3""")
            fks = cur.fetchall()
            if {(r[0],r[1]) for r in fks} != {("edu_content",t) for t in EXTERNAS} or len(fks)!=4:
                raise RuntimeError("Cambiaron las referencias externas; revisar antes de migrar")
            cur.execute("SELECT id,nombre,escuela_id FROM fabrica1.programa")
            nuevos = cur.fetchall()
            cur.execute("SELECT id,nombre FROM fabrica1.escuela")
            escuelas = dict(cur.fetchall())
            cur.execute("""SELECT DISTINCT p.id,p.nombre,e.nombre FROM fabrica.programa p
                JOIN fabrica.escuela e ON e.id=p.escuela_id
                JOIN edu_content.programa ep ON ep.fabrica_programa_id=p.id ORDER BY p.id""")
            for viejo, programa, escuela in cur.fetchall():
                candidatos = [n for n in nuevos if n[1] == programa and
                    gcp.escuela_oficial(norm_text(escuelas[n[2]])) == gcp.escuela_oficial(norm_text(escuela))]
                if len(candidatos) != 1:
                    raise RuntimeError(f"Correspondencia ambigua o inexistente: {programa}")
                informe["mapa_programas"].append([viejo,candidatos[0][0],programa])
            for tabla, campo in (("archivo_docente","archivo_id"),("pensum","fabrica_paquete_id"),("periodo","fabrica_periodo_id")):
                cur.execute(f"SELECT COUNT(*) FROM {nombre('edu_content',tabla)} WHERE {ident(campo)} IS NOT NULL")
                if cur.fetchone()[0] != 0:
                    raise RuntimeError(f"Se necesita mapeo adicional para edu_content.{tabla}")
            cur.execute("SELECT COUNT(*) FROM edu_content.programa WHERE fabrica_programa_id IS NOT NULL")
            informe["referencias_programa"] = cur.fetchone()[0]
            cur.execute("""SELECT p.proname,pg_get_functiondef(p.oid) FROM pg_proc p
                JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='fabrica1'
                AND p.proname IN ('upsert_archivo','fn_asignar_recurso_moodle') ORDER BY p.proname""")
            funciones = cur.fetchall()
            if len(funciones)!=2:
                raise RuntimeError("Funciones inesperadas")
            cur.execute("SELECT pg_get_serial_sequence('fabrica.' || tablename,'id'),tablename "
                        "FROM pg_tables WHERE schemaname='fabrica' ORDER BY tablename")
            secuencias = cur.fetchall()
            cur.execute("""SELECT sequencename,increment_by,cache_size FROM pg_sequences
                WHERE schemaname='fabrica'""")
            if any(r[1:] != (1,1) for r in cur.fetchall()):
                raise RuntimeError("Revisar secuencias con cache o incremento especial")
            # pg_dump comparte la misma instantanea que las huellas y el mapa.
            entorno = os.environ.copy()
            entorno.update(PGHOST=os.environ["DB_HOST"], PGPORT=os.environ.get("DB_PORT","5432"),
                           PGDATABASE=os.environ["DB_NAME"], PGUSER=os.environ["DB_USER"],
                           PGPASSWORD=os.environ["DB_PASSWORD"], PGSSLMODE=os.environ.get("DB_SSLMODE","require"),
                           PGCONNECT_TIMEOUT="30")
            respaldo = carpeta / "antes.dump"
            comando = [str(BIN/"pg_dump.exe"),"--format=custom",f"--snapshot={snapshot}",
                       "--schema=fabrica","--schema=fabrica1","--schema=edu_content",f"--file={respaldo}"]
            print("Creando respaldo restaurable de fabrica, fabrica1 y edu_content...", flush=True)
            subprocess.run(comando, env=entorno, check=True)
            # Lee y descomprime todo el archivo, ademas de validar su catalogo.
            subprocess.run([str(BIN/"pg_restore.exe"),"--file="+str(carpeta/"restauracion.sql"),str(respaldo)], check=True)
            informe["respaldo_sha256"] = hashlib.sha256(respaldo.read_bytes()).hexdigest()
            informe["respaldo_bytes"] = respaldo.stat().st_size
            lineas = ["BEGIN;", "SET LOCAL lock_timeout='10s';", "SET LOCAL statement_timeout='120s';",
                      "SELECT pg_advisory_xact_lock(hashtext('promocion_fabrica1_fabrica'));",
                      "LOCK TABLE " + ", ".join(nombre("fabrica1",t) for t in TABLAS) + " IN SHARE MODE;",
                      "LOCK TABLE " + ", ".join(nombre("fabrica",t) for t in TABLAS) + " IN ACCESS EXCLUSIVE MODE;",
                      "LOCK TABLE " + ", ".join(nombre("edu_content",t) for t in EXTERNAS) + " IN ACCESS EXCLUSIVE MODE;"]
            for clave,huella in informe["huellas"].items():
                esquema,tabla=clave.split(".")
                lineas.append(exigir_huella(esquema,tabla,huella))
            lineas += ["ALTER TABLE fabrica.archivo ADD COLUMN origen_id varchar(128), ADD COLUMN fecha_origen timestamptz;",
                       "COMMENT ON COLUMN fabrica.archivo.origen_id IS 'ID del archivo original en Google Drive.';",
                       "COMMENT ON COLUMN fabrica.archivo.fecha_origen IS 'Fecha de creacion del archivo original en Drive.';"]
            for esquema,tabla,constraint,*_ in fks:
                lineas.append(f"ALTER TABLE {nombre(esquema,tabla)} DROP CONSTRAINT {ident(constraint)};")
            lineas += ["ALTER TABLE fabrica.archivo DISABLE TRIGGER trg_recurso_moodle;",
                       "TRUNCATE TABLE " + ", ".join(nombre("fabrica",t) for t in TABLAS) + " CONTINUE IDENTITY RESTRICT;"]
            for tabla in TABLAS:
                campos=", ".join(ident(c) for c in columnas["fabrica1"][tabla])
                lineas.append(f"INSERT INTO {nombre('fabrica',tabla)} ({campos}) SELECT {campos} FROM {nombre('fabrica1',tabla)};")
            if informe["mapa_programas"]:
                casos=" ".join(f"WHEN {viejo} THEN {nuevo}" for viejo,nuevo,_ in informe["mapa_programas"])
                ids=", ".join(str(v) for v,_,_ in informe["mapa_programas"])
                lineas.append(f"UPDATE edu_content.programa SET fabrica_programa_id=CASE fabrica_programa_id {casos} END WHERE fabrica_programa_id IN ({ids});")
            lineas += [definicion.replace("fabrica1", "fabrica") + ";" for _,definicion in funciones]
            lineas += ["ALTER TABLE fabrica.archivo ENABLE TRIGGER trg_recurso_moodle;",
                       "CREATE UNIQUE INDEX archivo_origen_id_key ON fabrica.archivo(origen_id);"]
            for secuencia,tabla in secuencias:
                if secuencia:
                    cur.execute(f"SELECT COALESCE(MAX(id),0)+1 FROM {nombre('fabrica1',tabla)}")
                    siguiente=cur.fetchone()[0]
                    esquema_seq,nombre_seq=secuencia.split(".")
                    lineas.append(f"ALTER SEQUENCE {nombre(esquema_seq,nombre_seq)} RESTART WITH {siguiente};")
            for esquema,tabla,constraint,definicion,*_ in fks:
                lineas.append(f"ALTER TABLE {nombre(esquema,tabla)} ADD CONSTRAINT {ident(constraint)} {definicion};")
            for tabla in TABLAS:
                lineas.append(exigir_huella("fabrica",tabla,informe["huellas"][f"fabrica1.{tabla}"]))
                lineas.append(exigir_huella("fabrica1",tabla,informe["huellas"][f"fabrica1.{tabla}"]))
            for tabla in EXTERNAS:
                if tabla!="programa":
                    lineas.append(exigir_huella("edu_content",tabla,informe["huellas"][f"edu_content.{tabla}"]))
            # Verifica todas las columnas de edu_content.programa, salvo el ID remapeado.
            cur.execute("""SELECT COUNT(*),md5(COALESCE(string_agg(md5((to_jsonb(t)-'fabrica_programa_id')::text),'' ORDER BY id),''))
                FROM edu_content.programa t""")
            cantidad,digest=cur.fetchone()
            lineas.append(f"""DO $validar$ BEGIN
  IF (SELECT md5(COALESCE(string_agg(md5((to_jsonb(t)-'fabrica_programa_id')::text),'' ORDER BY id),'')) FROM edu_content.programa t) <> {literal(digest)}
    OR (SELECT COUNT(*) FROM edu_content.programa) <> {cantidad}
    OR (SELECT COUNT(*) FROM edu_content.programa WHERE fabrica_programa_id IS NOT NULL) <> {informe['referencias_programa']}
  THEN RAISE EXCEPTION 'Cambios inesperados en edu_content.programa'; END IF;
END $validar$;""")
            for viejo,nuevo,programa in informe["mapa_programas"]:
                cur.execute("SELECT id FROM edu_content.programa WHERE fabrica_programa_id=%s ORDER BY id",(viejo,))
                for (id_externo,) in cur.fetchall():
                    lineas.append(f"DO $validar$ BEGIN IF (SELECT fabrica_programa_id FROM edu_content.programa WHERE id={id_externo}) IS DISTINCT FROM {nuevo} THEN RAISE EXCEPTION 'Referencia de programa incorrecta'; END IF; END $validar$;")
            lineas.append("SELECT 'VALIDACION_CORRECTA' AS resultado;")
            base="\n\n".join(lineas)+"\n\n"
            (carpeta/"ensayo.sql").write_text(base+"ROLLBACK;\n",encoding="utf-8")
            (carpeta/"aplicar.sql").write_text(base+"COMMIT;\n",encoding="utf-8")
            (carpeta/"manifest.json").write_text(json.dumps(informe,ensure_ascii=False,indent=2),encoding="utf-8")
            print("Preparacion completa:",carpeta,flush=True)
            print("Respaldo:",informe["respaldo_bytes"],"bytes; SHA256:",informe["respaldo_sha256"])
            print("Programas remapeados:",informe["mapa_programas"])
    finally:
        conn.rollback()
        conn.close()


if __name__ == "__main__":
    preparar()
