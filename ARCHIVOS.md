# Inventario de archivos del repositorio

Listado de componentes del proyecto Automatización Inventario.


==================================================
RAÍZ
==================================================

  README.md                  Visión general
  DOCUMENTACION_PROCESO.md   Secuencia operativa
  DICCIONARIO_DATOS_EXCEL.md Columnas de RUTAS.xlsx
  CHECKLIST_ENTREGA.md       Verificación de cierre
  ARCHIVOS.md                Este listado
  run_flujo.py               Run único completo, desde prevalidación hasta
                             carga, inventario y correo final
  renovar_token.py           Autoriza la cuenta fábrica de contenidos y genera
                             el token único (token.json en la raíz)
  rutas_excel.py             Resolución de RUTAS.xlsx
  .gitignore                 Exclusión de secretos y artefactos
  .env.example               Plantilla de variables de entorno

  Excluidos del repositorio: credentials.json, token.json, .env, corridas/


==================================================
flujo_lib/  (librería compartida del run único)
==================================================

  __init__.py                ROOT (raíz del repo); sin imports pesados
  mensajes.py                ErrorFlujo (qué pasó + qué hacer) y traducir_excepcion
  certificados.py            Reconoce los certificados del equipo cuando el
                             antivirus inspecciona el tráfico seguro
  drive.py                   Token único (Drive + Sheets + Gmail), reintentos,
                             listado, lectura y creación de carpetas sin duplicar
  nombres.py                 Nombre canónico de archivos (jpg/jpeg ≡ png)
  clasificacion.py           Detecta el cliente y la escuela subiendo por las
                             carpetas padre del origen en Drive
  excel.py                   Lectura y validación de RUTAS.xlsx (todos los errores juntos)
  destino.py                 Carpeta destino automática dentro de la raíz
  clonacion.py               Clonación reanudable origen → destino (port de
                             clone_carpeta_drive.py con nombre canónico)
  estado.py                  Estado por lote en corridas/<excel>.estado.json y
                             exportación a Excel
  prevalidacion.py           Revisión previa: Excel, token, Drive, correos, base de datos
  formato.py                 Conversión JPG/JPEG → PNG en el clon
  verificacion.py            Completitud, integridad, ubicación e indexabilidad
  inventario.py              Inventario Excel y publicación en Sheets
  gcp.py                     Escaneo y carga transaccional por lote
  notificar.py               Correo único final con Excel adjunto
  README.md                  Documentación de la librería y de las pruebas


==================================================
tests/  (unittest; sin red, sin credenciales)
==================================================

  __init__.py                Paquete de pruebas
  fake_drive.py              Drive falso en memoria compartido por las pruebas
  test_mensajes.py           ErrorFlujo y traducción de excepciones
  test_drive.py              Token, reintentos, listado, carpetas
  test_nombres.py            Nombre canónico
  test_excel.py              Lectura y validación del Excel
  test_destino.py            Resolución de la carpeta destino
  test_clonacion.py          Clonación con el Drive falso
  test_estado.py             Estado de corrida y Excel de estado
  test_prevalidacion.py      Prevalidación con Drive y base de datos falsos
  test_formato.py            Conversión con Drive falso
  test_verificacion.py       Verificación del clon
  test_inventario.py         Adaptador canónico del inventario
  test_gcp.py                Simulación, transacción y rollback de carga
  test_notificar.py          Correo único y adjunto con Gmail falso

  Ejecución: python -m unittest discover -s tests -v   (desde la raíz)


==================================================
corridas/  (no versionado; lo crea el run único)
==================================================

  <excel>.estado.json        Estado por lote y paso; permite reanudar
  <excel>.estado.xlsx        El mismo estado en Excel (colores por paso, Qué pasó / Qué hacer)
  logs/<id>_<excel>.log      Log de cada corrida con el detalle técnico


==================================================
CAMBIAR_FORMATO/  (script por módulo)
==================================================

  convertir_jpg_a_png.py     Conversión JPG/JPEG → PNG en Drive
  codigo.js                  Alternativa Apps Script
  codigos.txt                Copia de texto del Apps Script
  requirements.txt           Dependencias
  README.md                  Documentación del módulo

  Excluidos del repositorio: credenciales.json, token.json


==================================================
CLONACION_CARPETA/  (script por módulo)
==================================================

  clone_carpeta_drive.py         Entrada del bloque (clonación + encadenamiento)
  reporte_inventario_clon.py     Reporte de inventario
  publicar_inventario_sheets.py  Publicación en Google Sheets
  notificar_clonacion.py         Correo 1
  renovar_token.py               Regeneración del token.json propio del módulo
  comparar_clon_drive.py         Diagnóstico (fuera del flujo diario)
  .env.example                   Plantilla
  requirements.txt / README.md

  Excluidos del repositorio: credentials.json, token.json, .env,
  hoja_inventario_id.txt, clonacion.log, reportes generados


==================================================
LMS_Fabrica/  (script por módulo)
==================================================

  generar_base_rutas.py      Excel → CSV
  cargar_base_gcp.py         CSV → Cloud SQL + correo 2
  notificar_carga_lms.py     Notificación de carga
  generar_base_lms.py        Librería compartida
  lms_lib/                   Constantes compartidas
  clonar_esquema_pruebas.py  Administración de esquema (fuera del flujo diario)
  .env.example               Plantilla
  requirements.txt / README.md

  Excluidos del repositorio: credenciales.json, token.json, .env, CSV de corrida


==================================================
ENTRADAS DEL FLUJO
==================================================

Run único: todo sale de RUTAS.xlsx (cliente, etiqueta, origen, destino).
`destino` es la carpeta raíz; la carpeta del programa la crea el flujo con el
nombre del origen. No hay parámetros por lote.

Scripts por módulo: RUTAS.xlsx con destino = carpeta final; la carpeta de
conversión JPG→PNG se indica con --carpeta en convertir_jpg_a_png.py.


==================================================
NOTIFICACIONES
==================================================

Run único: un único correo final por corrida con el Excel de estado
(y correo de fallo en lenguaje llano).

Scripts por módulo:
  Tras la clonación  -> notificar_clonacion.py  -> enlace Google Sheet
  Tras la carga GCP  -> notificar_carga_lms.py  -> resumen + consulta SQL


==================================================
EJECUCIÓN
==================================================

Run único (recomendado):

  python run_flujo.py --excel "<RUTA>\RUTAS.xlsx"
  python run_flujo.py --excel "<RUTA>\RUTAS.xlsx" --solo-prevalidar
  python run_flujo.py --excel "<RUTA>\RUTAS.xlsx" --rehacer clonacion
  python renovar_token.py        (solo cuando el flujo pide autorizar)

Scripts por módulo (semántica anterior, hasta terminar la migración):

  cd CAMBIAR_FORMATO
  python convertir_jpg_a_png.py --carpeta "https://drive.google.com/drive/folders/<ID_CARPETA>"

  cd CLONACION_CARPETA
  python clone_carpeta_drive.py --excel "<RUTA>\RUTAS.xlsx"

  cd LMS_Fabrica
  python generar_base_rutas.py --excel "<RUTA>\RUTAS.xlsx" -o lms_base_rutas.csv
  python cargar_base_gcp.py -i lms_base_rutas.csv --schema fabrica_pruebas

Documentación operativa: DOCUMENTACION_PROCESO.md
Validación de entrega: CHECKLIST_ENTREGA.md
