-- fabrica1 · migración 002 · alta del cliente JARVEY
-- ---------------------------------------------------------------------------
-- JARVEY es un cliente más, igual que PRODUCTO y TANIA: cuelga de LMS_Carga en
-- Drive y se guarda tal cual en fabrica1.cliente.
--
-- La carga lo crearía sola (get_or_create en LMS_Fabrica/cargar_base_gcp.py),
-- pero se da de alta aquí para que exista antes de la primera corrida y para
-- dejar constancia de cuándo y por qué entró.
--
-- El id sale de la secuencia de la tabla, como el resto de fabrica1. Antes se
-- realinea la secuencia con el MAX(id) real: si quedó atrasada por un ROLLBACK
-- previo (las secuencias no retroceden), el INSERT chocaría con una fila viva.
--
-- Solo toca fabrica1. Es idempotente: se puede ejecutar más de una vez.
-- Ejecutar con un usuario dueño de las tablas de fabrica1, por ejemplo:
--   psql "host=127.0.0.1 port=5432 dbname=planner_db user=planner_user" -f fabrica1_002_cliente_jarvey.sql
-- ---------------------------------------------------------------------------
BEGIN;

-- Misma fórmula que _realinear_secuencias en flujo_lib/gcp.py.
SELECT setval(
    pg_get_serial_sequence('fabrica1.cliente', 'id'),
    COALESCE((SELECT MAX(id) FROM fabrica1.cliente), 0) + 1,
    false
);

INSERT INTO fabrica1.cliente (nombre)
SELECT 'JARVEY'
 WHERE NOT EXISTS (
    SELECT 1 FROM fabrica1.cliente WHERE nombre = 'JARVEY'
 );

COMMIT;
