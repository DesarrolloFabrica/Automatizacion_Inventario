# Promoción de fabrica1 a fabrica · 19 de octubre de 2026

Se reemplazaron los datos antiguos de `planner_db.fabrica` por el contenido de
`fabrica1`, conforme a la indicación de Camilo. Se conservan las tablas oficiales,
sus permisos y sus dependencias; `fabrica1` permanece disponible sin cambios.

El ensayo ejecutó el mismo SQL con `ROLLBACK`. Después se aplicó con `COMMIT`
y se verificó el resultado desde otra conexión de solo lectura.

| Tabla | Filas finales |
|---|---:|
| archivo | 25.382 |
| recurso_moodle | 10.444 |
| programa | 89 |
| materia | 904 |
| granulo | 8.839 |
| escuela | 6 |
| cliente | 4 |
| extension | 30 |
| paquete | 2 |
| destinatario | 1 |
| periodo | 1 |
| raiz | 1 |
| diplomados_costos | 0 |

La tabla `diplomados_costos` quedó vacía para coincidir con el origen.
Las 164 filas antiguas están en el respaldo, junto con los demás datos anteriores.

## Relaciones conservadas

Las 35 referencias de `edu_content.programa` se remapearon al mismo programa
y escuela en la estructura normalizada:

| Programa | ID anterior | ID oficial nuevo |
|---|---:|---:|
| CONTADURIA_PUBLICA | 520 | 37 |
| MARKETING_DIGITAL | 21684 | 88 |
| NEGOCIOS_INTERNACIONALES | 21703 | 79 |
| ADMINISTRACION_DE_EMPRESAS | 21724 | 35 |
| INGENIERIA_DE_SOFTWARE | 21728 | 82 |

Las otras tres relaciones externas (`archivo_docente`, `pensum`, `periodo`)
no tenían referencias no nulas. Sus tablas se conservaron completas y se
recrearon las cuatro claves foráneas validadas. No se encontraron referencias huérfanas.

## Comprobaciones

- Conteos y huellas de contenido coinciden para las 13 tablas, no solo los totales.
- `fabrica1` coincide con su instantánea anterior: no se modificó el origen.
- Se conservaron los IDs, enlaces, nombres originales y vínculos de origen.
- Las secuencias oficiales apuntan a un valor mayor que el máximo de su tabla.
- El trigger de Moodle quedó habilitado. Los recursos se copiaron con sus IDs,
  sin disparar el trigger durante la copia y duplicarlos.
- Las funciones oficiales tienen `search_path` en `fabrica`, sin apuntar a `fabrica1`.
- La vista de inventario funciona y `Consulta_lms` conserva uso del esquema y lectura de archivos.
- Suite del proyecto: 568 pruebas aprobadas antes del cambio.

## Respaldo y trazabilidad

Directorio local ignorado por Git: `corridas/migracion_fabrica_20261019_163104/`.

- `antes.dump`: respaldo PostgreSQL en formato custom de `fabrica`, `fabrica1`
  y `edu_content`, tomado en la misma instantánea usada por las validaciones.
- SHA-256: `ce82ca2d16b1cd41a7530daad75278902e8d1917ad104461e96b6b2f3be5b908`.
- `restauracion.sql`: el respaldo leído y descomprimido por `pg_restore`.
- `manifest.json`: huellas, conteos y correspondencias anteriores.
- `ensayo.sql` y `ensayo_correcto.json`: ensayo revertido y huella del SQL ensayado.
- `aplicar.sql` y `aplicacion_correcta.json`: SQL aplicado y confirmación del COMMIT.
- `verificacion_final.json`: verificación independiente después del COMMIT.

El SQL contiene validaciones de la instantánea previa: no está pensado para
repetirse después de la migración. Si se necesita recuperar el estado anterior,
usar el respaldo completo y el mapa de referencias, restaurando también las
relaciones de `edu_content`; no restaurar solo `archivo` ni ejecutar un borrado
en cascada. Preparar la restauración en una base aislada antes de cualquier
reversión sobre el esquema oficial.

## Próximas cargas

`run_flujo.py` y el servidor usan `fabrica` por defecto. El `.env` local tiene
`LMS_SCHEMA=fabrica` y `SCHEMA_POR_DEFECTO=fabrica`.

`fabrica` ahora usa el flujo normalizado: secuencias, `origen_id`, `fecha_origen`,
deduplicación por origen/enlace y sin borrado por programa. Solo `fabrica_pruebas`
conserva el comportamiento anterior. Para las cargas oficiales usar el run único,
no los scripts CSV heredados.

En otros equipos o despliegues se deben instalar estos cambios y ajustar sus
variables; el cambio local no publica una nueva revisión en Cloud Run.

Consulta de comprobación:

```sql
SELECT 'fabrica' AS esquema, COUNT(*) AS archivos FROM fabrica.archivo
UNION ALL
SELECT 'fabrica1', COUNT(*) FROM fabrica1.archivo;
```

La promoción utiliza una transacción y secuencias reiniciadas con `ALTER SEQUENCE
RESTART`, que se revierte con la transacción. Véanse las referencias oficiales de
[TRUNCATE](https://www.postgresql.org/docs/current/sql-truncate.html) y
[ALTER SEQUENCE](https://www.postgresql.org/docs/current/sql-altersequence.html).
