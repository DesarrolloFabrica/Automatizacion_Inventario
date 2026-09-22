# Traspaso — Run único de la fábrica de contenidos

Documento para el agente (o persona) que continúe este trabajo. Léelo completo antes de tocar nada.
Fecha de corte: 2026-09-22. Responsable del proyecto: Camilo Quintero (CUN).


## 1. Objetivo final

Un solo comando en PowerShell que haga todo el flujo a partir de `RUTAS.xlsx`:

```powershell
python run_flujo.py --excel "C:\ruta\RUTAS.xlsx"
```

Secuencia acordada con el usuario (no reabrir estas decisiones):

1. Prevalidar sin tocar Drive: Excel, token, acceso a carpetas, base de datos, correos.
2. Por cada lote (fila del Excel):
   1. Crear o reutilizar dentro de la carpeta `destino` (raíz) una carpeta con el nombre EXACTO del origen.
   2. Clonar el origen dentro de esa carpeta (reanudable).
   3. Convertir EN EL CLON todos los JPG/JPEG a PNG, conservando el nombre base (`pieza_01.JPG` → `pieza_01.png`). El origen nunca se modifica.
   4. Verificar el clon: completitud, integridad, ubicación por tipo de carpeta e indexabilidad.
3. Compuerta: solo los lotes verificados se cargan a Cloud SQL, cada lote en su transacción. Los fallidos se retienen y se reportan. Existe `--forzar-carga`, apagado por defecto. El esquema por defecto es `fabrica_pruebas` mientras se valida el flujo (decisión de Camilo del 2026-09-22, que reemplaza la de cargar directo a producción); para `fabrica` hay que indicarlo a mano con `--schema fabrica`.
4. Inventario a la Google Sheet fija y estado de la corrida en JSON y Excel.
5. Un único correo final (exitoso / con pendientes / fallido) con tabla por lote, enlace a la Sheet, consulta SQL del lote y el Excel de estado adjunto. Correo de fallo en lenguaje no técnico: qué pasó y qué hacer.

Otras decisiones fijas:

- Un solo token de Google (Drive + Sheets + Gmail) de la cuenta "fábrica de contenidos", en la raíz del repo (`credentials.json` + `token.json`). Se renueva con `python renovar_token.py`.
- Estado por lote en `corridas/` (ignorado por git) para reanudar sin duplicar; `--rehacer PASO` fuerza repetir.
- Comparaciones origen↔clon SIEMPRE por nombre canónico (`flujo_lib/nombres.py`): base intacta + extensión en minúscula, jpg/jpeg ≡ png. Sin esto, una reejecución de la clonación borraría los PNG convertidos y volvería a copiar los JPG.
- Reglas de ubicación por tipo de carpeta: ACTIVIDADES MOODLE → txt; SCORM → zip; PDF, FICHAS, REVISTA, GLOSARIO → pdf; PORTADA MATERIA → png; PODCAST → mp3; CONTENIDOS, GUION GRÁFICO, GUION PODCAST, QA → sin restricción por ahora.
- La columna `cliente` del Excel es OPCIONAL desde el 2026-09-22 (pedido de Camilo: "se debe tener la capacidad de saber de dónde viene automáticamente"). Se deduce subiendo por las carpetas padre del origen en Drive hasta una llamada PRODUCTO, TANIA o LMS_CORRECCIONES, y de ahí sale también la escuela. Si el Excel trae valor, manda el Excel y se avisa cuando no coincide. Ver `flujo_lib/clasificacion.py`.
- Primera entrega: un comando ejecutado por una persona. Frontend/backend vienen después; por eso toda la lógica vive en `flujo_lib/` y `run_flujo.py` solo orquesta.


## 2. Estado actual (fases 1 a 3 implementadas y revisadas; prueba de extremo a extremo hecha)

Las fases 1 y 2 están commiteadas. La fase 3 y la prueba de extremo a extremo
están en el árbol de trabajo, sin commit. Suite verificada al corte:

```
python -m unittest discover -s tests
Ran 414 tests  OK
```

Revisión adversarial terminada el 2026-09-22. Se corrigieron dos hallazgos:

- La reanudación ahora comprueba que la carpeta destino guardada siga activa. Si fue
  eliminada o enviada a la papelera, vuelve a resolverla y repite los pasos posteriores
  cuando cambia el ID, en vez de saltarse la clonación.
- La lectura de un Excel inválido ya no deja el `.xlsx` abierto en Windows cuando la
  validación falla durante la búsqueda de la cabecera.

Nuevo:

| Archivo | Qué es |
|---|---|
| `flujo_lib/drive.py` | Token único, `ejecutar` con reintentos, `listar_hijos`, `obtener_carpeta`, `crear_carpeta` sin duplicados, `extraer_id_carpeta`, `quien_soy` |
| `flujo_lib/mensajes.py` | `ErrorFlujo` (motivo + acción + detalle técnico) y `traducir_excepcion` |
| `flujo_lib/nombres.py` | `nombre_canonico`, `es_jpg`, `nombre_png`, `extension`, `base` |
| `flujo_lib/excel.py` | `leer_lotes` (todas las validaciones juntas, `cliente` obligatorio), `Lote`, `resolver_excel` |
| `flujo_lib/destino.py` | `resolver_destino`: crea/reutiliza la carpeta con el nombre del origen dentro de la raíz |
| `flujo_lib/clonacion.py` | Port de `CLONACION_CARPETA/clone_carpeta_drive.py` (copiar + igualar) con nombre canónico; `clonar_arbol` → `ResumenClon` |
| `flujo_lib/formato.py` | Conversión recursiva e idempotente JPG/JPEG → PNG únicamente en el clon; el JPG va a la papelera después de crear el PNG |
| `flujo_lib/verificacion.py` | Completitud canónica, integridad, ubicación por tipo e indexabilidad; resultado `ok` / `con_diferencias` |
| `flujo_lib/inventario.py` | Adaptador del inventario y publicación heredados con nombres canónicos para archivos |
| `flujo_lib/gcp.py` | Escaneo del clon y carga transaccional por lote; simulación sin conexión y anti-duplicados por enlace |
| `flujo_lib/notificar.py` | Construcción y envío del único correo final con tabla, SQL por lote y Excel adjunto |
| `flujo_lib/estado.py` | `EstadoCorrida`: JSON por Excel, pasos `destino, clonacion, formato, verificacion, carga`, `exportar_excel` |
| `flujo_lib/prevalidacion.py` | `prevalidar` → hallazgos ok/aviso/error por área (excel, token, drive, correo, db) |
| `flujo_lib/README.md` | Descripción de la librería y cómo correr pruebas |
| `run_flujo.py` | Orquestador completo: prevalidación → Drive → formato → verificación → compuerta → carga → inventario → correo |
| `renovar_token.py` | Autorización interactiva del token único |
| `tests/fake_drive.py` | Drive simulado en memoria (get/list/create/copy/update/about, fallos inyectables) |
| `tests/test_*.py` | Pruebas de cada módulo |

Modificados: `.gitignore` (agrega `corridas/`), `README.md`, `DOCUMENTACION_PROCESO.md`, `DICCIONARIO_DATOS_EXCEL.md`, `CHECKLIST_ENTREGA.md`, `ARCHIVOS.md`.

No tocados a propósito (siguen con la semántica vieja, destino = carpeta final, tokens propios): `CAMBIAR_FORMATO/`, `CLONACION_CARPETA/`, `LMS_Fabrica/`, `rutas_excel.py`.

Hay un `git stash` viejo ("borrador carpeta_destino") de un intento anterior. No sirve ya; se puede borrar con `git stash drop`.


## 2 bis. Servicio web y despliegue (hecho el 2026-09-22)

Sobre el run único se montó una página web y el despliegue en Google Cloud Run.

- `servidor/app.py`: FastAPI. Sirve la página de `servidor/static/` y la API
  (`POST /api/corridas`, `GET /api/corridas`, `GET /api/corridas/{id}`,
  `POST /api/corridas/{id}/cancelar`, `GET /api/salud`).
- `servidor/corridas.py`: cola de corridas ejecutadas **de una en una** en un
  hilo de fondo. Reutiliza `run_flujo.ejecutar_corrida`, así que web y terminal
  hacen exactamente lo mismo. Candado por carpeta de origen.
- `servidor/configuracion.py`: variables de entorno y credenciales (modo `token`
  o `cuenta_servicio` con delegación de dominio).
- `servidor/static/`: formulario y pantalla de avance con barras por paso,
  consola de mensajes y sondeo cada 2 s. Modo demostración con `?demo=1`.
- `flujo_lib/progreso.py` y `flujo_lib/almacen.py`: avance paso a paso y estado
  en disco o en Cloud Storage.
- `Dockerfile`, `.dockerignore`, `requirements-servidor.txt` y `despliegue/`
  (guía, arquitectura, plantilla de variables y `desplegar.ps1`).

Cambios en la librería para que la web pudiera reutilizarla:
`prevalidar(lotes=...)` acepta lotes ya armados sin Excel; `ejecutar_corrida`
acepta `lotes`, `reporte`, `cargar_credenciales` y `cancelado`; los pasos
aceptan `avance=None`; la prevalidación se guarda en el estado.

Comprobado de verdad: la página se sirve, `/api/salud` responde con la cuenta
`fabricadecontenidos@cun.edu.co`, y una prueba de extremo a extremo recorre
petición HTTP → cola → run_flujo → Drive simulado.

Limitaciones conocidas: un solo proceso y una sola instancia (los candados y las
corridas viven en memoria); si el servicio se reinicia a mitad de una corrida,
esa corrida queda interrumpida y hay que relanzarla; cancelar actúa entre pasos,
no dentro de uno.

Pendiente por parte de Camilo: instalar Google Cloud CLI y pedir al
administrador de Google Workspace la cuenta de servicio con delegación (mientras
tanto el despliegue funciona con el `token.json` actual en Secret Manager).


## 3. Lo que falta, en orden

### 3.0 Revisar la fase 1 — COMPLETADA

Se revisó con estos tres lentes y se corrigieron los hallazgos descritos en la sección 2:

- Fidelidad del port: comparar `flujo_lib/clonacion.py` función por función con `CLONACION_CARPETA/clone_carpeta_drive.py` (`copiar_arbol`, `igualar_arbol`, `_copiar_archivo_sin_duplicar`, `_quitar_duplicados_en_carpeta`, `_esperar_indice`). Confirmar nombre canónico en TODOS los puntos de comparación de archivos y NO en carpetas, copia con nombre original, nada escribe bajo el origen, sin duplicados tras timeouts, sin borrados indebidos (origen con `x.jpg` y `x.png` a la vez).
- Estado y reanudación: segunda corrida salta lo correcto; destino creado pero clon fallido reanuda con el mismo id; carpeta destino borrada en Drive entre corridas (¿revalidar el id?); Excel que cambia (filas nuevas/eliminadas, lotes huérfanos); `exportar_excel` con el xlsx abierto no debe tumbar el run; handlers de logging duplicados si `main` se llama dos veces.
- Pruebas, mensajes y docs: asserts triviales o mocks que oculten lógica; parser de `q` del FakeDrive vs lo que genera `flujo_lib.drive`; mensajes al operador sin jerga y con acción; docs vs código (opciones, defaults, rutas, códigos de salida).

### 3.1 Fase 2: conversión en el clon, verificación, compuerta — COMPLETADA

- `flujo_lib/formato.py`: implementado y cubierto por pruebas, incluida recursión, reanudación y garantía de no tocar el origen.
- `flujo_lib/verificacion.py`: implementado por lote, contra el origen:
  - Completitud: cada archivo del origen tiene su par en el clon por nombre canónico y no sobra nada.
  - Integridad: mismo `size` y `md5Checksum` para archivos no convertidos (Drive no da md5 para documentos nativos de Google: comparar solo nombre); PNG convertidos con `size > 0`; ningún `.jpg/.jpeg` restante en el clon.
  - Ubicación: extensión permitida según el tipo de carpeta padre (tabla de la sección 1; reutilizar `_es_tipo_material` de `CLONACION_CARPETA/reporte_inventario_clon.py`).
  - Indexabilidad: que `LMS_Fabrica/generar_base_rutas.parsear_ruta_programa` (o `parsear_ruta`) clasifique cada archivo; los que no, se listan como "no indexables".
  - Resultado: `ok` / `con_diferencias` con lista de hallazgos legibles; se guarda en estado.
- Inventario: implementado en `flujo_lib/inventario.py`; reutiliza los módulos heredados mediante una vista canónica sin modificarlos.
- Compuerta en `run_flujo.py`: implementada. Los lotes `con_diferencias` quedan con carga `omitido`, salvo `--forzar-carga`, que permite cargarlos. Código de salida 2 = con pendientes.

### 3.2 Fase 3: carga a GCP y correo único — COMPLETADA

- `flujo_lib/gcp.py`: implementado con escaneo del destino, una transacción por lote, `--simular` sin conexión y anti-duplicados por enlace.
- `flujo_lib/notificar.py`: implementado con Gmail API, tabla por lote, enlace a Sheet, consulta SQL por lote y Excel de estado adjunto; los fallos muestran qué pasó y qué hacer sin trazas.
- `run_flujo.py`: integración completa y códigos de salida 0/1/2. `--rehacer carga` repite únicamente la carga; `--simular` no escribe ni envía correo.
- Cierre pendiente por decisión previa: los scripts antiguos siguen intactos. La documentación principal y este traspaso ya reflejan el run completo.

### 3.3 Fase 4: pruebas de extremo a extremo y primera corrida

- Prueba de extremo a extremo — COMPLETADA. Está en `tests/test_end_to_end.py`:
  Excel de dos lotes que comparten la raíz de destino, uno correcto con un JPG
  real y otro con un PDF dentro de PORTADA MATERIA. Recorre prevalidación,
  destino, clonación, conversión, verificación, compuerta, carga, inventario y
  correo. Solo se sustituyen la sesión de Google, la conexión a la base, la
  publicación en Sheets y el envío por Gmail; el resto se ejecuta de verdad,
  incluidos los módulos heredados. Comprueba además que el origen queda intacto
  y que una segunda corrida no duplica nada.
- Primera corrida real supervisada — PENDIENTE. La hace Camilo, en este orden:
  `--simular`, luego `--schema fabrica_pruebas`, y solo después `fabrica`.
  Antes hace falta `credentials.json` en la raíz y `python renovar_token.py`
  con la cuenta fábrica de contenidos.


## 4. Reglas de trabajo para quien continúe

- Todo en español: código, comentarios, mensajes, docs. Imports agrupados en una línea por grupo; `from __future__ import annotations`.
- No ejecutar nada contra Drive, Sheets, Gmail ni Cloud SQL durante el desarrollo. Solo `py_compile`, `--help` y `unittest` con `tests/fake_drive.py`. No crear credenciales.
- No hacer commits salvo que Camilo lo pida. Él revisa antes de implementar: si algo no está en este documento, preguntar antes de construirlo.
- Mensajes al operador: siempre "qué pasó" + "qué hacer", sin jerga técnica. El detalle técnico va al log.
- Correr la suite antes de entregar:

```powershell
cd C:\Dev\Pruebas\Sarita\AutomatizacionProcesos\Automatizacion_Inventario
python -m unittest discover -s tests -v
python run_flujo.py --help
```
