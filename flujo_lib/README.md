# flujo_lib — librería compartida del run único

Código común de `run_flujo.py`: un solo token de Google, una sola lectura del
Excel, carpeta destino automática, clonación reanudable, estado por lote y
prevalidación. Reemplaza, paso a paso, lo que hoy hacen por separado
`CAMBIAR_FORMATO/`, `CLONACION_CARPETA/` y `LMS_Fabrica/`; esos scripts no se
tocan mientras dura la migración.

Convenciones del paquete:

- Todo en español (código, docstrings, mensajes), con el tono del código existente.
- `from __future__ import annotations` e imports agrupados en una línea por grupo.
- Ningún módulo habla con Google ni con la base de datos al importarse; solo al llamar funciones.
- Los errores que ve la persona que opera son `ErrorFlujo`: una frase de qué pasó y otra de qué hacer.
  El detalle técnico va aparte, al log.
- `ROOT` (en `flujo_lib/__init__.py`) es la raíz del repo; `run_flujo.py` la agrega a `sys.path`,
  así que la librería se importa desde ahí (`from flujo_lib import drive`).


## Módulos

| Módulo | Qué hace | Entradas principales |
|---|---|---|
| `mensajes.py` | `ErrorFlujo(motivo, accion, *, paso, detalle, contexto)` y `traducir_excepcion(exc, *, paso, contexto)`: convierte HttpError 404/403/401/408/429/5xx, `RefreshError`, `PermissionError`, `FileNotFoundError`, errores de `psycopg2` y de red en frases claras. | `ErrorFlujo`, `traducir_excepcion` |
| `drive.py` | Token único (`credentials.json` + `token.json` en la raíz; `SCOPES` = Drive, Sheets, Gmail send). `estado_token` (diagnóstico sin login), `cargar_credenciales(interactivo=...)`, `autorizar` (imprime la URL, no abre el navegador), `construir_servicio`, `ejecutar` con reintentos y backoff ante fallos transitorios, `extraer_id_carpeta`, `listar_hijos` (paginado, Shared Drives), `obtener_carpeta`, `crear_carpeta` sin duplicar, `enviar_a_papelera`, `quien_soy`. | `cargar_credenciales`, `construir_servicio`, `listar_hijos`, `obtener_carpeta`, `crear_carpeta` |
| `certificados.py` | Suma la lista de autoridades de Python y la del equipo en `certificados_confianza.pem` y se la indica a `ssl`, `requests` y `httplib2`. Necesario donde el antivirus o la red inspeccionan el tráfico seguro (Kaspersky en los equipos de la CUN). Nunca desactiva la verificación. Se rehace en cada corrida. | `asegurar`, `generar`, `aplicar` |
| `clasificacion.py` | `detectar(svc, origen_id)`: sube por las carpetas padre del origen hasta una que se llame PRODUCTO, TANIA o LMS_CORRECCIONES y devuelve también la escuela. Así la columna `cliente` del Excel es opcional. `ascendencia` acepta `inicial` para no repetir la consulta que ya hizo la prevalidación. | `detectar`, `ascendencia`, `ClasificacionDetectada` |
| `nombres.py` | Nombre canónico de archivos (ver abajo): `extension`, `base`, `es_jpg`, `nombre_png`, `nombre_canonico`. Solo texto. | `nombre_canonico` |
| `excel.py` | `leer_lotes(ruta) -> list[Lote]` con openpyxl: cabecera flexible, alias de columnas, `cliente` obligatorio, enlaces validados, origen distinto de destino, pares únicos. Acumula todos los problemas y lanza **una** `ExcelInvalido`. `resolver_excel`, `normalizar_clasificacion`, `CLASIFICACIONES`. | `leer_lotes`, `resolver_excel`, `Lote` |
| `destino.py` | `resolver_destino(svc, origen, raiz)`: dentro de la raíz `destino` del Excel crea o reutiliza la carpeta con el nombre exacto del origen; si la raíz ya se llama como el origen la usa directamente (`directa=True`). Nunca borra nada; si hay repetidas usa la primera y avisa. `describir(res)` para el log. | `resolver_destino`, `DestinoResuelto`, `describir` |
| `clonacion.py` | `clonar_arbol(svc, origen_id, destino_id, *, log, ...) -> ResumenClon`: port fiel de `copiar_arbol` / `igualar_arbol` de `clone_carpeta_drive.py` (dos pasadas, reintentos, limpieza de duplicados) comparando archivos por nombre canónico. Nunca escribe bajo el origen. `ResumenClon.ok()` y `.texto()`. | `clonar_arbol`, `ResumenClon` |
| `formato.py` | Conversión recursiva e idempotente JPG/JPEG → PNG en el clon. | `convertir_arbol`, `ResumenFormato` |
| `verificacion.py` | Completitud, integridad, ubicación e indexabilidad del clon. | `verificar_lote`, `ResultadoVerificacion` |
| `inventario.py` | Adaptación canónica del reporte Excel y publicación en Sheets heredados. | `generar_inventario`, `publicar_inventario` |
| `gcp.py` | Escaneo del clon y carga a Cloud SQL, una transacción por lote y anti-duplicados por enlace. | `escanear_lote`, `cargar_lote` |
| `notificar.py` | Correo único con tabla por lote, consultas SQL y Excel de estado adjunto. | `construir_mensaje`, `enviar_correo` |
| `estado.py` | `EstadoCorrida.abrir(excel)` abre o crea `corridas/<excel>.estado.json`. Por lote (clave `origen_id|destino_raiz_id`) guarda el estado de cada paso (`PASOS`: destino, clonacion, formato, verificacion, carga; `ESTADOS`: pendiente, en_curso, ok, con_diferencias, fallido, omitido), la carpeta destino real y el último error. `iniciar_corrida` / `cerrar_corrida`, `registrar_lote`, `marcar`, `completado`, `resumen`, `exportar_excel` (hojas "Estado" y "Corridas", colores por paso). Escritura atómica. | `EstadoCorrida` |
| `prevalidacion.py` | `prevalidar(excel, *, schema, simular, interactivo, ...) -> ResultadoPrevalidacion`: revisa en orden Excel, token, carpetas de Drive de cada lote, `CORREOS_AVISO` y base de datos (conexión y esquema). Nada lanza: todo termina en `Hallazgo` (ok / aviso / error). Con `simular`, correo y base son aviso. `cargar_env` (`.env` de la raíz y luego los de los módulos sin pisar), `correos_aviso`. | `prevalidar`, `ResultadoPrevalidacion`, `Hallazgo` |


## Cómo encajan en una corrida

```text
run_flujo.py
  excel.resolver_excel  ->  estado.EstadoCorrida.abrir + iniciar_corrida
  prevalidacion.prevalidar  (leer_lotes, cargar_credenciales, obtener_carpeta, correos, db)
     ERROR -> cerrar_corrida("fallido_prevalidacion"), exportar_excel, salida 1
  por lote:
     estado.registrar_lote
     destino.resolver_destino  ->  estado.set_destino + marcar("destino", "ok")
     clonacion.clonar_arbol    ->  marcar("clonacion", "ok" | "fallido")
     formato.convertir_arbol   ->  marcar("formato", "ok" | "fallido")
     verificacion.verificar_lote -> compuerta
     gcp.cargar_lote           ->  transacción independiente por lote
  inventario -> cerrar_corrida -> exportar_excel -> correo único -> resumen
```

Cualquier excepción de un paso pasa por `mensajes.traducir_excepcion` y se guarda
en el estado con motivo y acción; el detalle técnico va al log de `corridas/logs/`.


## Nombre canónico (jpg ≡ png)

La conversión JPG → PNG se hace **en el clon**, conservando el
nombre base. Después de eso el origen tiene `pieza_01.JPG` y el clon
`pieza_01.png`. Si la clonación comparara por nombre literal, una reejecución
vería el PNG como "sobrante" (lo mandaría a la papelera) y el JPG como
"faltante" (lo copiaría otra vez), deshaciendo la conversión.

Por eso toda comparación origen ↔ clon se hace por **nombre canónico**:

```text
nombre_canonico("pieza_01.JPG")  == "pieza_01.png"
nombre_canonico("pieza_01.jpeg") == "pieza_01.png"
nombre_canonico("Guia.PDF")      == "Guia.pdf"
nombre_canonico("archivo")       == "archivo"      (sin extensión: tal cual)
```

Regla: base intacta + `.` + extensión en minúscula, con `jpg`/`jpeg` → `png`.
Las carpetas se comparan por nombre exacto. La copia en Drive sigue usando el
nombre original del archivo; el canónico solo sirve para decidir qué falta,
qué sobra y qué ya está.


## Estado de la corrida

- `corridas/<excel>.estado.json`: `{"version", "excel", "creado", "actualizado", "corridas": [...], "lotes": {clave: {...}}}`.
  Cada lote guarda etiqueta, fila, cliente, `destino_id` / `destino_nombre`, un
  registro por paso (`estado`, `fecha`, `detalle`, `motivo`, `accion`) y `ultimo_error`.
- `corridas/<excel>.estado.xlsx`: hoja "Estado" (Etiqueta | Fila | Cliente | Carpeta destino |
  Enlace destino | Destino | Clonación | Formato | Verificación | Carga | Qué pasó | Qué hacer |
  Actualizado; verde ok, rojo fallido, amarillo con diferencias / en curso, gris pendiente / omitido)
  y hoja "Corridas" (Id | Inicio | Fin | Resultado).
- Reanudar: `completado(clave, paso)` dice si un paso ya quedó en `ok`; `run_flujo.py` lo
  salta salvo `--rehacer`. Un `fallido` deja `ultimo_error` con qué pasó y qué hacer.


## Pruebas

Desde la raíz del repo (unittest; pytest no hace falta):

```powershell
python -m unittest discover -s tests -v
```

O con el intérprete explícito:

```powershell
C:\Python314\python.exe -m unittest discover -s tests -v
```

Reglas de las pruebas:

- Ninguna toca Google, Cloud SQL ni la red, y ninguna duerme: `dormir`,
  `ejecutar`, `listar`, `cargar_credenciales`, `construir_servicio` y
  `conectar_db` se inyectan.
- `tests/fake_drive.py` es el Drive falso compartido (`FakeDrive`): carpetas y
  archivos en memoria, `files().get/list/create/copy/update/get_media`,
  `about().get`, paginación real, consultas `q` con `in parents`, `trashed`,
  `mimeType`, `name`, y `fallar(...)` / `fallar_red(...)` para simular errores
  HTTP (`HttpError` real) o de red, con o sin efecto aplicado. No se cambian sus
  métodos existentes; si se agrega uno, se documenta.
- Los Excels de prueba se generan con openpyxl en carpetas temporales; el estado
  usa `tempfile` y un reloj inyectado.
- No se crean ni usan `credentials.json` ni `token.json`.

Archivos: `test_mensajes.py`, `test_drive.py`, `test_nombres.py`,
`test_excel.py`, `test_destino.py`, `test_clonacion.py`, `test_estado.py`,
`test_prevalidacion.py`.


## Pendiente (fases 2 y 3)

- Conversión JPG → PNG en el clon (todos los archivos, conservando el nombre base).
- Verificación origen vs clon por nombre canónico y resultado `con_diferencias`.
- Compuerta: solo los lotes verificados pasan a la carga (o `--forzar-carga`).
- Carga al esquema `fabrica_pruebas` (producción solo con `--schema fabrica`) reutilizando el token único.
- Un único correo final con el Excel de estado adjunto y correo de fallo en lenguaje llano.
