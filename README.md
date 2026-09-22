# Automatizacion Inventario (Flujo LMS)

Proyecto de automatización para la fábrica de contenido (CUN).
Prepara material en Google Drive, ejecuta la clonación con control de calidad
y registra el resultado en Cloud SQL (`planner_db`).

El repositorio no incluye secretos. La configuración OAuth y el archivo `.env`
se definen en el entorno local de ejecución.

> **Nota de migración.** El proceso está pasando de cuatro scripts separados
> (con tres tokens distintos) a un **run único**: `run_flujo.py` + la librería
> `flujo_lib/`. Los scripts por módulo siguen funcionando con su semántica
> anterior (destino = carpeta final) hasta que termine la migración; para el
> run único usar `run_flujo.py`.


## Requisitos de entorno

1. Python 3.10 o superior y dependencias de cada módulo (`pip install -r requirements.txt`).
2. Token único en la **raíz del repo**: `credentials.json` (OAuth de escritorio) y
   `token.json` con permisos de Drive, Sheets y Gmail, autorizado con la cuenta
   **fábrica de contenidos** (`python renovar_token.py`).
3. Archivo `.env` en la raíz a partir de `.env.example` (`CORREOS_AVISO` y parámetros `DB_*`).
4. Archivo `RUTAS.xlsx` conforme a `DICCIONARIO_DATOS_EXCEL.md`
   (`cliente` opcional, se deduce de Drive; `destino` es la carpeta **raíz** de destino).

Documentación operativa: `DOCUMENTACION_PROCESO.md`.  
Librería del run único: `flujo_lib/README.md`.  
Documentación por módulo (scripts anteriores): `CAMBIAR_FORMATO/README.md`,
`CLONACION_CARPETA/README.md`, `LMS_Fabrica/README.md`.


## Run único (recomendado)

```powershell
python run_flujo.py --excel "<RUTA>\RUTAS.xlsx"
```

Todo sale del Excel; no hay parámetros por lote. El comando ejecuta el flujo completo:

1. **Prevalidación**: Excel, token único, acceso a la carpeta origen y a la raíz
   destino de cada lote, `CORREOS_AVISO` y conexión a la base de datos con el
   esquema indicado. Los hallazgos se muestran todos juntos (`OK`, `AVISO`,
   `ERROR`); con algún `ERROR` el flujo no arranca.
2. **Carpeta destino automática**: dentro de la carpeta raíz `destino` crea (o
   reutiliza) una carpeta con el nombre **exacto** del origen y clona ahí. Si la
   raíz ya se llama como el origen (Excel antiguo), se usa directamente.
3. **Clonación** origen → carpeta destino, reanudable y sin duplicar. Nunca se
   modifica nada dentro del origen.
4. **Conversión y verificación** del clon, con compuerta para retener diferencias.
5. **Carga transaccional por lote** a Cloud SQL, inventario en Sheets y un único correo final.

| Opción | Descripción |
|---|---|
| `--excel RUTA` | Ruta a `RUTAS.xlsx` (también variable `RUTAS_XLSX` o `RUTAS.xlsx` en la raíz). |
| `--schema NOMBRE` | Esquema de Cloud SQL. Por defecto `fabrica_pruebas` mientras se valida el flujo. Para producción: `--schema fabrica`. El run único no lee `LMS_SCHEMA`. |
| `--simular` | Ejecuta el flujo sin escribir en la base ni enviar correo. |
| `--forzar-carga` | Permite cargar lotes con diferencias de verificación. Por defecto apagado. |
| `--solo-prevalidar` | Ejecuta solo la prevalidación y termina. |
| `--rehacer PASO` | Repite ese paso aunque el estado diga OK: `destino`, `clonacion`, `formato`, `verificacion`, `carga` o `todo`. Se puede repetir. |
| `--no-interactivo` | Si hace falta autorizar Google, falla con mensaje en vez de pedir login. |

Códigos de salida: `0` flujo completo correcto; `1` prevalidación o algún paso
falló; `2` hay lotes con diferencias retenidos por la compuerta.

Salidas en `corridas/` (no versionado):

- `corridas/<excel>.estado.json`: estado por lote y por paso; es lo que permite reanudar.
- `corridas/<excel>.estado.xlsx`: el mismo estado en Excel, con colores por paso
  y columnas "Qué pasó" / "Qué hacer" (se adjuntará al correo final).
- `corridas/logs/<id_corrida>_<excel>.log`: log de cada corrida con el detalle técnico.

### Reanudación

El estado por lote (clave: origen + raíz de destino) evita repetir lo ya hecho:
un lote con clonación OK se omite en la siguiente corrida y el flujo retoma
donde quedó. Para volver a sincronizar un clon, `--rehacer clonacion` (solo
copia lo que falte). Si un lote falla, se corrige la causa y se vuelve a ejecutar
el mismo comando.


## Servicio web

Además del comando, el flujo se puede lanzar desde una página. Es la misma
ejecución: la web reutiliza `run_flujo.ejecutar_corrida`, así que no hay dos
comportamientos que mantener.

Levantarlo en el equipo:

```powershell
python -m uvicorn servidor.app:app --port 8080
```

Y abrir `http://127.0.0.1:8080`. La página pide la carpeta de origen, la de
destino y poco más; el cliente se detecta solo. Al pulsar Ejecutar se encola la
corrida y la pantalla va mostrando los cinco pasos con su barra de avance, los
mensajes del momento y, al terminar, el resultado con los enlaces.

Detalles de cómo funciona:

- Las corridas se ejecutan **de una en una**. Si alguien lanza otra mientras hay
  una en marcha, espera en la cola; si es sobre la misma carpeta de origen, se
  rechaza con un aviso.
- Se puede cerrar el navegador: la corrida sigue y se puede volver a abrir desde
  la lista de las últimas corridas.
- Cancelar detiene la corrida **entre pasos**, no a mitad de una copia. Lo ya
  hecho se conserva y se puede retomar.
- El modo prueba viene marcado por defecto: no escribe en la base ni envía correo.
- La cabecera muestra siempre contra qué cuenta y qué esquema se está trabajando,
  y avisa en rojo si el esquema es producción.

El despliegue en Google Cloud Run está documentado en `despliegue/README.md`,
con la arquitectura en `despliegue/ARQUITECTURA.md`.


## Módulos

- `flujo_lib/`: librería compartida del run único (token único, Excel, destino
  automático, clonación con nombre canónico, estado de corrida, prevalidación).
- `tests/`: pruebas unitarias de `flujo_lib` con un Drive falso (sin red, sin credenciales).
- `CAMBIAR_FORMATO/`: conversión JPG/JPEG → PNG en Drive (script por módulo).
- `CLONACION_CARPETA/`: clonación origen → destino, inventario (reporte), Sheet y correo 1 (script por módulo).
- `LMS_Fabrica/`: escaneo del destino, CSV, carga a Cloud SQL y correo 2 (script por módulo).


## Estructura actual

```text
./
├── run_flujo.py                        # run único (fase 1: prevalidación, destino, clonación)
├── renovar_token.py                    # autoriza la cuenta fábrica de contenidos (token único)
├── rutas_excel.py                      # localiza RUTAS.xlsx sin rutas fijas
├── flujo_lib/                          # librería compartida del run único
│   ├── __init__.py                     # ROOT (raíz del repo)
│   ├── mensajes.py                     # ErrorFlujo y traducción de excepciones
│   ├── drive.py                        # token único y API de Drive
│   ├── nombres.py                      # nombre canónico (jpg ≡ png)
│   ├── excel.py                        # lectura y validación de RUTAS.xlsx
│   ├── destino.py                      # carpeta destino automática
│   ├── clonacion.py                    # clonación reanudable
│   ├── estado.py                       # estado por lote en corridas/
│   ├── prevalidacion.py                # revisión previa
│   └── README.md
├── tests/                              # unittest + Drive falso (tests/fake_drive.py)
├── corridas/                           # estado y logs de corridas (no versionado)
├── CAMBIAR_FORMATO/
│   ├── convertir_jpg_a_png.py
│   ├── codigo.js
│   ├── codigos.txt
│   ├── requirements.txt
│   └── README.md
├── CLONACION_CARPETA/
│   ├── clone_carpeta_drive.py          # clon + reporte inventario + correo 1
│   ├── reporte_inventario_clon.py
│   ├── publicar_inventario_sheets.py
│   ├── notificar_clonacion.py
│   ├── renovar_token.py                # token propio del módulo (semántica anterior)
│   ├── comparar_clon_drive.py          # diagnostico (no diario)
│   ├── .env.example
│   ├── requirements.txt
│   └── README.md
├── LMS_Fabrica/
│   ├── generar_base_rutas.py           # Excel -> Drive -> CSV
│   ├── cargar_base_gcp.py              # CSV -> Cloud SQL + correo 2
│   ├── notificar_carga_lms.py
│   ├── generar_base_lms.py             # libreria compartida (API publica)
│   ├── lms_lib/                        # constantes compartidas
│   ├── clonar_esquema_pruebas.py       # admin una vez (NO diario)
│   ├── .env.example
│   ├── requirements.txt
│   └── README.md
├── .env.example
├── .gitignore
├── DOCUMENTACION_PROCESO.md
├── DICCIONARIO_DATOS_EXCEL.md
├── CHECKLIST_ENTREGA.md
├── ARCHIVOS.md
└── README.md
```


## Que NO va en el repositorio

Estas cosas se generan en local o son secretos; estan en `.gitignore`:

- `credentials.json` / `credenciales.json` (raíz y módulos)
- `token.json` (raíz y módulos)
- `.env`
- `corridas/` (estado JSON y Excel de cada RUTAS, logs de corrida)
- `clonacion.log`, `hoja_inventario_id.txt`
- CSV/Excel de corridas (`lms_base_rutas.csv`, `RUTAS.xlsx`, reportes)
- `__pycache__/`, `.venv/`

En runtime:

- El run único escribe y actualiza `corridas/<excel>.estado.json`, su Excel y el log.
- Con los scripts por módulo, el inventario local y la Sheet se actualizan al
  clonar y el CSV se crea al generar la base; ninguno se versiona.


## Excel RUTAS.xlsx

Una fila = un lote.

- `cliente`: `PRODUCTO`, `TANIA` o `LMS_correcciones`. **Opcional:** si se deja vacío
  se deduce de la carpeta de Drive de la que cuelga el origen. Si se escribe, manda
  el Excel y se avisa cuando no coincide con Drive.
- `etiqueta`: apodo humano (no se guarda en GCP). Vacía → `Fila N`.
- `origen`: carpeta a clonar. Su **nombre** en Drive es el de la carpeta que el
  flujo crea en el destino.
- `destino`: carpeta **raíz** de destino. El run único crea (o reutiliza) dentro
  la carpeta del programa, con el nombre exacto del origen, y clona ahí. No hay
  que crearla a mano. Si la raíz ya se llama como el origen, se usa directamente.

Los errores del Excel se reportan **todos juntos** en una sola pasada
(fila y motivo), para corregirlos de una vez.

Detalle completo de columnas, valores y mapeo a GCP:
ver `DICCIONARIO_DATOS_EXCEL.md`.

El nombre del **programa** en GCP sale del nombre de la carpeta en Drive.
Cliente, origen y destino se toman del Excel.

Scripts por módulo (semántica anterior): `destino` es la carpeta final de la
copia y la **unica** que escanea la carga a GCP.


## Dos reglas distintas

Inventario del clon:

- Cuenta por tipo de carpeta (Moodle, contenidos, SCORM, PDF, etc.).
- Archivos tipo `01_Quiz.txt` en ACTIVIDADES MOODLE **si** cuentan.

Carga a GCP:

- Si el nombre empieza con `G` + digitos, ese es el codigo.
- Si no (Moodle u otros), se usa el stem del archivo.

Comparación origen ↔ clon (run único): por **nombre canónico** (base intacta,
extensión en minúscula, `jpg`/`jpeg` → `png`), porque tras convertir en el clon
el origen tiene `x.JPG` y el clon `x.png`. Ver `flujo_lib/README.md`.


## Correos

Run único: un **único correo final** por corrida (y correo de fallo en lenguaje
no técnico), con el Excel de estado adjunto. Se incorpora en la fase 3; hoy no
se envía.

Scripts por módulo (hay dos):

- Despues del clon (`notificar_clonacion.py`): link de la Google Sheet del inventario.
- Despues de cargar GCP (`notificar_carga_lms.py`): resumen del lote + query SQL para Cloud SQL Studio.

Ninguno se dispara solo por subir un PDF a Drive.
Destinatarios: `CORREOS_AVISO` en `.env`.


## Variables de entorno

Copia la plantilla en la raíz:

```text
copy .env.example .env
```

El run único lee `.env` de la raíz y, sin pisar lo ya definido, los antiguos
`CLONACION_CARPETA/.env` y `LMS_Fabrica/.env`.

Configura (ejemplo PowerShell):

```powershell
$env:CORREOS_AVISO="correo1@cun.edu.co,correo2@cun.edu.co"
$env:DB_HOST="TU_HOST"
$env:DB_PORT="5432"
$env:DB_NAME="planner_db"
$env:DB_USER="TU_USUARIO"
$env:DB_PASSWORD="TU_PASSWORD"
$env:LMS_SCHEMA="fabrica_pruebas"
```

Tambien puede ir en archivo `.env`:

```env
CORREOS_AVISO=correo1@cun.edu.co,correo2@cun.edu.co
DB_HOST=
DB_PORT=5432
DB_NAME=planner_db
DB_USER=
DB_PASSWORD=
LMS_SCHEMA=fabrica_pruebas
```

`LMS_SCHEMA` solo lo usan los scripts de `LMS_Fabrica`. El run único carga al
esquema de `--schema`. Por defecto es `fabrica_pruebas`: mientras se valida el
flujo nada llega a producción salvo que se pida a mano con `--schema fabrica`.

### Token único (run único)

OAuth de escritorio de la cuenta **fábrica de contenidos**, en la **raíz del repo**:

- `credentials.json` (lo entrega soporte)
- `token.json` (lo genera la autorización; permisos de Drive, Sheets y Gmail send)

Para autorizar por primera vez, o cuando el flujo avise "La sesión de Google
venció" o "Hay que autorizar la cuenta fábrica de contenidos en Google":

```powershell
python renovar_token.py
```

No abre el navegador solo: copia la URL que imprime en el navegador donde ya
está abierta la cuenta fábrica de contenidos y acepta los permisos. Al terminar
muestra el correo autorizado. Con `--no-interactivo` el run nunca pide login:
si hace falta, falla con ese mismo mensaje.

### Tokens de los scripts por módulo

Hasta terminar la migración conservan sus propios tokens:

- Clonacion: `CLONACION_CARPETA/credentials.json` + `token.json` (`cd CLONACION_CARPETA; python renovar_token.py`)
- Formato / LMS: `credenciales.json` + `token.json` en su carpeta

En nube o equipo compartido: secretos fuera del repo (nunca versionar
`token.json`, `credentials.json` ni `.env`).


## Requisitos

- Python 3.10+
- Acceso de la cuenta fábrica de contenidos a las carpetas Drive del lote
- Para carga GCP: IP autorizada en Cloud SQL + `.env` completo


## Ejecutar el run único

```powershell
python run_flujo.py --excel "<RUTA>\RUTAS.xlsx"
```

Solo revisar, sin clonar nada:

```powershell
python run_flujo.py --excel "<RUTA>\RUTAS.xlsx" --solo-prevalidar
```

Volver a sincronizar la clonación de todos los lotes:

```powershell
python run_flujo.py --excel "<RUTA>\RUTAS.xlsx" --rehacer clonacion
```

Al final imprime una tabla por lote (etiqueta, carpeta destino, estado de
destino y de clonación), las rutas del JSON, del Excel de estado y del log, y
la nota "Conversión, verificación, carga a GCP y correo se incorporan en las
fases siguientes."


## Scripts por módulo (semántica anterior)

Siguen disponibles mientras dura la migración. En ellos `destino` es la carpeta
final de la copia y cada carpeta usa su propio token.

### Formato

```powershell
cd CAMBIAR_FORMATO
pip install -r requirements.txt
python convertir_jpg_a_png.py --carpeta "https://drive.google.com/drive/folders/<ID_CARPETA>"
```

Ver `CAMBIAR_FORMATO/README.md`.

### Clonacion + inventario + correo 1

```powershell
cd CLONACION_CARPETA
pip install -r requirements.txt
python clone_carpeta_drive.py --excel "<RUTA>\RUTAS.xlsx"
```

Ese comando encadena inventario, Google Sheets y el correo 1.
Ver `CLONACION_CARPETA/README.md`.

### Carga a GCP

```powershell
cd LMS_Fabrica
pip install -r requirements.txt
python generar_base_rutas.py --excel RUTAS.xlsx -o lms_base_rutas.csv
python cargar_base_gcp.py -i lms_base_rutas.csv --schema fabrica_pruebas
```

Reclasificar cliente/raiz en prueba (no permitido en produccion `fabrica`):

```powershell
python cargar_base_gcp.py -i lms_base_rutas.csv --schema fabrica_pruebas --actualizar
```

Omitir correo:

```powershell
python cargar_base_gcp.py -i lms_base_rutas.csv --schema fabrica_pruebas --sin-correo
```


## Pruebas

Desde la raíz del repo (no hace falta pytest; no tocan Google ni la base):

```powershell
python -m unittest discover -s tests -v
```

Detalle en `flujo_lib/README.md`.


## Fuera del flujo diario

- Creación del esquema `fabrica_pruebas` (actividad administrativa puntual)
- `clonar_esquema_pruebas.py`
- `comparar_clon_drive.py` (diagnóstico)
- `codigo.js` / `codigos.txt` (alternativa Apps Script; la ejecución oficial es Python)

Documentación operativa: `DOCUMENTACION_PROCESO.md`.  
Validación de entrega: `CHECKLIST_ENTREGA.md`.
