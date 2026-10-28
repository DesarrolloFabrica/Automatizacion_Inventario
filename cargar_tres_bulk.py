"""Carga transaccional por conjuntos de las filas previamente validadas."""
import json
from pathlib import Path
from psycopg2.extras import execute_values, Json
from flujo_lib import gcp
from flujo_lib.prevalidacion import cargar_env

DIR = Path('corridas/carga_tres_programas_20261022')


def main():
    cargar_env()
    filas = []
    for p in sorted(DIR.glob('*.filas.json')):
        filas.extend(json.loads(p.read_text(encoding='utf-8')))
    assert len(filas) == 5299
    assert not gcp.problemas_filas_limpias(filas)
    carga, _ = gcp._heredados()
    for orden, f in enumerate(filas):
        f['_orden'] = orden
        f['extension_tipo'] = carga.normalizar_extension(f['extension_tipo'])
        for k in ('archivo_nombre', 'archivo_nombre_original'):
            f[k] = carga.trunc(f[k])
    conn = gcp.conectar_desde_env()
    try:
        with conn:
            with conn.cursor() as q:
                q.execute("SET LOCAL lock_timeout='30s'; SET LOCAL statement_timeout='120s'")
                gcp._exigir_columnas_origen(q, 'fabrica')
                gcp._realinear_secuencias(q, 'fabrica')
                q.execute('CREATE TEMP TABLE carga_puntual (d jsonb) ON COMMIT DROP')
                execute_values(q, 'INSERT INTO carga_puntual VALUES %s', [(Json(f),) for f in filas], page_size=1000)
                q.execute("""DELETE FROM carga_puntual s WHERE s.d->>'origen_id' IN (SELECT origen_id FROM fabrica.archivo WHERE origen_id IS NOT NULL) OR s.d->>'archivo_enlace' IN (SELECT enlace FROM fabrica.archivo)""")
                existentes = q.rowcount
                for tabla, columna, campo in [('escuela','nombre','escuela_nombre'),('paquete','nombre','paquete_nombre'),('raiz','nombre','raiz_nombre'),('destinatario','codigo','destinatario_codigo'),('periodo','codigo','periodo_codigo'),('cliente','nombre','cliente_nombre'),('extension','tipo','extension_tipo')]:
                    q.execute(f"INSERT INTO fabrica.{tabla} ({columna}) SELECT DISTINCT d->>'{campo}' FROM carga_puntual s WHERE NOT EXISTS (SELECT 1 FROM fabrica.{tabla} t WHERE t.{columna}=s.d->>'{campo}')")
                q.execute("""INSERT INTO fabrica.programa (escuela_id,nombre) SELECT DISTINCT e.id,s.d->>'programa_nombre' FROM carga_puntual s JOIN fabrica.escuela e ON e.nombre=s.d->>'escuela_nombre' WHERE NOT EXISTS (SELECT 1 FROM fabrica.programa p WHERE p.escuela_id=e.id AND p.nombre=s.d->>'programa_nombre')""")
                q.execute("""CREATE TEMP VIEW carga_programas AS SELECT s.d,p.id programa_id,pa.id paquete_id FROM carga_puntual s JOIN fabrica.escuela e ON e.nombre=s.d->>'escuela_nombre' JOIN fabrica.programa p ON p.escuela_id=e.id AND p.nombre=s.d->>'programa_nombre' JOIN fabrica.paquete pa ON pa.nombre=s.d->>'paquete_nombre'""")
                q.execute("""INSERT INTO fabrica.materia (programa_id,paquete_id,semestre,nombre) SELECT DISTINCT programa_id,paquete_id,(d->>'materia_semestre')::int,d->>'materia_nombre' FROM carga_programas s WHERE NOT EXISTS (SELECT 1 FROM fabrica.materia m WHERE m.programa_id=s.programa_id AND m.paquete_id=s.paquete_id AND m.semestre=(s.d->>'materia_semestre')::int AND m.nombre=s.d->>'materia_nombre')""")
                q.execute("""CREATE TEMP VIEW carga_materias AS SELECT s.d,m.id materia_id FROM carga_programas s JOIN fabrica.materia m ON m.programa_id=s.programa_id AND m.paquete_id=s.paquete_id AND m.semestre=(s.d->>'materia_semestre')::int AND m.nombre=s.d->>'materia_nombre'""")
                q.execute("""INSERT INTO fabrica.granulo (materia_id,codigo,nombre) SELECT DISTINCT ON (materia_id,d->>'granulo_codigo') materia_id,d->>'granulo_codigo',d->>'granulo_nombre' FROM carga_materias s WHERE NOT EXISTS (SELECT 1 FROM fabrica.granulo g WHERE g.materia_id=s.materia_id AND g.codigo=s.d->>'granulo_codigo') ORDER BY materia_id,d->>'granulo_codigo',(d->>'_orden')::int""")
                q.execute("""INSERT INTO fabrica.archivo (granulo_id,raiz_id,destinatario_id,periodo_id,cliente_id,extension_id,nombre,nombre_original,enlace,hash_sha256,fecha_registro,activo,origen_id,fecha_origen)
                SELECT g.id,r.id,de.id,pe.id,c.id,ex.id,d->>'archivo_nombre',d->>'archivo_nombre_original',d->>'archivo_enlace',NULLIF(d->>'archivo_hash',''),(d->>'archivo_fecha_registro')::timestamptz,(d->>'archivo_activo')::boolean,d->>'origen_id',(d->>'fecha_origen')::timestamptz
                FROM carga_materias s JOIN fabrica.granulo g ON g.materia_id=s.materia_id AND g.codigo=s.d->>'granulo_codigo'
                JOIN fabrica.raiz r ON r.nombre=s.d->>'raiz_nombre'
                JOIN fabrica.destinatario de ON de.codigo=s.d->>'destinatario_codigo'
                JOIN fabrica.periodo pe ON pe.codigo=s.d->>'periodo_codigo'
                JOIN fabrica.cliente c ON c.nombre=s.d->>'cliente_nombre'
                JOIN fabrica.extension ex ON ex.tipo=s.d->>'extension_tipo'""")
                insertados = q.rowcount
                assert insertados + existentes == len(filas), (insertados, existentes)
                print(f'Insertados {insertados}; existentes {existentes}', flush=True)
        resultado = dict(total=len(filas), insertados=insertados, existentes=existentes, esquema='fabrica')
        (DIR/'resultado.json').write_text(json.dumps(resultado,indent=2),encoding='utf-8')
        print('COMMIT confirmado',flush=True)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
