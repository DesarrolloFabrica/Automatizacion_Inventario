-- Amplía granulo.codigo para conservar sin recortes los códigos derivados
-- de nombres de archivo. Es idempotente y solo modifica esquemas existentes.
BEGIN;

DO $migration$
DECLARE
    schema_name text;
    view_definition text;
    view_owner oid;
    view_acl aclitem[];
    permission record;
    grantee_name text;
BEGIN
    FOREACH schema_name IN ARRAY ARRAY['fabrica', 'fabrica_pruebas', 'fabrica1']
    LOOP
        IF to_regclass(format('%I.granulo', schema_name)) IS NOT NULL THEN
            view_definition := NULL;
            view_owner := NULL;
            view_acl := NULL;

            SELECT pg_get_viewdef(c.oid, true), c.relowner, c.relacl
              INTO view_definition, view_owner, view_acl
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = schema_name
               AND c.relname = 'v_inventario_archivos'
               AND c.relkind = 'v';

            IF view_definition IS NOT NULL THEN
                EXECUTE format('DROP VIEW %I.v_inventario_archivos', schema_name);
            END IF;

            EXECUTE format(
                'ALTER TABLE %I.granulo ALTER COLUMN codigo TYPE varchar(200)',
                schema_name
            );

            IF view_definition IS NOT NULL THEN
                EXECUTE format(
                    'CREATE VIEW %I.v_inventario_archivos AS %s',
                    schema_name,
                    view_definition
                );
                EXECUTE format(
                    'ALTER VIEW %I.v_inventario_archivos OWNER TO %I',
                    schema_name,
                    pg_get_userbyid(view_owner)
                );

                -- Reponer permisos explícitos de otros roles (p. ej. Consulta_lms).
                IF view_acl IS NOT NULL THEN
                    FOR permission IN
                        SELECT * FROM aclexplode(view_acl)
                         WHERE grantee <> view_owner
                    LOOP
                        grantee_name := CASE
                            WHEN permission.grantee = 0 THEN 'PUBLIC'
                            ELSE quote_ident(pg_get_userbyid(permission.grantee))
                        END;
                        EXECUTE format(
                            'GRANT %s ON TABLE %I.v_inventario_archivos TO %s%s',
                            permission.privilege_type,
                            schema_name,
                            grantee_name,
                            CASE WHEN permission.is_grantable
                                 THEN ' WITH GRANT OPTION' ELSE '' END
                        );
                    END LOOP;
                END IF;
            END IF;
        END IF;
    END LOOP;
END
$migration$;

COMMIT;
