# LMS_Fabrica

Escanea las carpetas destino definidas en el Excel de rutas, genera un CSV
intermedio y registra los archivos en Cloud SQL (`planner_db`). Al finalizar
envía la notificación asociada (correo 2).

Esquema operativo por defecto: `fabrica_pruebas`.  
El esquema `fabrica` corresponde a producción y solo se utiliza cuando así se
defina expresamente.

## Requisitos

- Python 3.10 o superior
- Dependencias: `pip install -r requirements.txt`
- Archivo `.env` (a partir de `.env.example`) con:
  - `CORREOS_AVISO`
  - `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`
  - `LMS_SCHEMA=fabrica_pruebas`
- Credenciales OAuth en esta carpeta:
  - `credenciales.json`
  - `token.json`
- Acceso de red autorizado a Cloud SQL

Los archivos `credenciales.json`, `token.json`, `.env` y CSV de corrida no
forman parte del repositorio.

## Ejecución

```powershell
cd LMS_Fabrica
python generar_base_rutas.py --excel "<RUTA>\RUTAS.xlsx" -o lms_base_rutas.csv
python cargar_base_gcp.py -i lms_base_rutas.csv --schema fabrica_pruebas
```

1. Escaneo de Drive (columna **destino** del Excel) y generación del CSV  
2. Carga a Cloud SQL y envío del correo 2  

Sin correo:

```powershell
python cargar_base_gcp.py -i lms_base_rutas.csv --schema fabrica_pruebas --sin-correo
```

## Criterios de indexación

- Si el nombre inicia con `G` seguido de dígitos, ese valor se usa como código
- En caso contrario, se utiliza el nombre del archivo sin extensión
- Cliente, origen y destino se obtienen del Excel

## Correspondencia Excel → Cloud SQL

| Valor en Excel | cliente | raíz |
|---|---|---|
| PRODUCTO | PRODUCTO | LMS_Carga |
| TANIA | TANIA | LMS_Carga |
| JARVEY | JARVEY | LMS_Carga |
| LMS_correcciones | PRODUCTO | LMS_Carga |

## Scripts

| Script | Función |
|---|---|
| `generar_base_rutas.py` | Excel → CSV |
| `cargar_base_gcp.py` | CSV → Cloud SQL + correo 2 |
| `notificar_carga_lms.py` | Notificación de carga |
| `generar_base_lms.py` | Librería compartida |
| `lms_lib/` | Constantes compartidas |
| `clonar_esquema_pruebas.py` | Administración de esquema (fuera del flujo diario) |
