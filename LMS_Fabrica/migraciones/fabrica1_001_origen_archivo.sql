-- fabrica1 · migración 001 · columnas del archivo de ORIGEN en archivo
-- ---------------------------------------------------------------------------
-- Recarga desde cero (2026-10-02). Cada fila de archivo guarda de qué archivo
-- del Drive origen salió su copia:
--   origen_id    ID de Drive del archivo original (el del Drive origen).
--   fecha_origen cuándo se subió ese archivo al Drive origen (createdTime).
--                Distinta de fecha_registro, que es cuándo se cargó a la base.
--
-- Solo toca fabrica1. Es idempotente: se puede ejecutar más de una vez.
-- Ejecutar con un usuario dueño de las tablas de fabrica1, por ejemplo:
--   psql "host=127.0.0.1 port=5432 dbname=planner_db user=planner_user" -f fabrica1_001_origen_archivo.sql
-- ---------------------------------------------------------------------------
BEGIN;

ALTER TABLE fabrica1.archivo
    ADD COLUMN IF NOT EXISTS origen_id    varchar(128),
    ADD COLUMN IF NOT EXISTS fecha_origen timestamptz;

-- Un archivo del origen se carga una sola vez.
CREATE UNIQUE INDEX IF NOT EXISTS archivo_origen_id_key
    ON fabrica1.archivo (origen_id);

COMMENT ON COLUMN fabrica1.archivo.origen_id IS
    'ID de Google Drive del archivo original en el Drive ORIGEN (el clon queda en enlace).';
COMMENT ON COLUMN fabrica1.archivo.fecha_origen IS
    'Fecha en que el archivo original se subió al Drive origen (createdTime de Drive).';

COMMIT;
