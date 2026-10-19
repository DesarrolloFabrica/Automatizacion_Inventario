"""Auditoría de solo lectura previa a la promoción de fabrica1."""

from __future__ import annotations

import json
from pathlib import Path

from psycopg2 import sql

from flujo_lib import gcp
from flujo_lib.prevalidacion import cargar_env


def main():
    cargar_env()
    conn = gcp.conectar_desde_env()
    conn.set_session(readonly=True, isolation_level="REPEATABLE READ")
    informe = {}
    consultas = {
        "tablas": """SELECT n.nspname, c.relname, c.relkind, pg_get_userbyid(c.relowner),
            c.relrowsecurity, c.relacl::text
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname IN ('fabrica','fabrica1') ORDER BY 1,2""",
        "columnas": """SELECT table_schema, table_name, column_name, data_type,
            character_maximum_length, is_nullable, column_default, is_identity, identity_generation
            FROM information_schema.columns WHERE table_schema IN ('fabrica','fabrica1')
            ORDER BY table_schema, table_name, ordinal_position""",
        "restricciones": """SELECT n.nspname, c.relname, co.conname, co.contype,
            pg_get_constraintdef(co.oid), co.convalidated
            FROM pg_constraint co JOIN pg_class c ON c.oid=co.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname IN ('fabrica','fabrica1') ORDER BY 1,2,3""",
        "referencias_externas": """SELECT ns.nspname, src.relname, co.conname,
            nt.nspname, dst.relname, pg_get_constraintdef(co.oid)
            FROM pg_constraint co JOIN pg_class src ON src.oid=co.conrelid
            JOIN pg_namespace ns ON ns.oid=src.relnamespace
            JOIN pg_class dst ON dst.oid=co.confrelid
            JOIN pg_namespace nt ON nt.oid=dst.relnamespace
            WHERE co.contype='f' AND nt.nspname IN ('fabrica','fabrica1')
            AND ns.nspname<>nt.nspname ORDER BY 1,2,3""",
        "vistas": """SELECT schemaname, viewname, definition FROM pg_views
            WHERE schemaname IN ('fabrica','fabrica1') OR definition ~ 'fabrica[1]?[.]'
            ORDER BY 1,2""",
        "triggers": """SELECT n.nspname,c.relname,t.tgname,pg_get_triggerdef(t.oid),t.tgenabled
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname IN ('fabrica','fabrica1') AND NOT t.tgisinternal ORDER BY 1,2,3""",
        "funciones": """SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid),
            pg_get_functiondef(p.oid),p.proconfig,pg_get_userbyid(p.proowner),p.proacl::text
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE p.prokind IN ('f','p') AND (n.nspname IN ('fabrica','fabrica1')
            OR p.prosrc ~ 'fabrica[1]?[.]') ORDER BY 1,2,3""",
        "esquemas": """SELECT nspname,pg_get_userbyid(nspowner),nspacl::text
            FROM pg_namespace WHERE nspname IN ('fabrica','fabrica1')""",
        "indices": """SELECT schemaname,tablename,indexname,indexdef FROM pg_indexes
            WHERE schemaname IN ('fabrica','fabrica1') ORDER BY 1,2,3""",
        "sesiones": """SELECT application_name,state,wait_event_type,wait_event
            FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()""",
        "version": "SELECT version(),current_user,current_database()",
    }
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout='30s'")
            for clave, consulta in consultas.items():
                cur.execute(consulta)
                informe[clave] = cur.fetchall()
            informe["conteos"] = []
            for esquema, tabla, tipo, *_ in informe["tablas"]:
                if tipo not in ("r", "p"):
                    continue
                cur.execute(sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                    sql.Identifier(esquema), sql.Identifier(tabla)))
                informe["conteos"].append([esquema, tabla, cur.fetchone()[0]])
    finally:
        conn.rollback()
        conn.close()
    destino = Path("corridas/auditoria_fabrica_fabrica1.json")
    destino.parent.mkdir(exist_ok=True)
    destino.write_text(json.dumps(informe, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print("Auditoria guardada:", destino)
    for fila in informe["conteos"]:
        print(*fila)
    print("Referencias entre esquemas:", len(informe["referencias_externas"]))


if __name__ == "__main__":
    main()
