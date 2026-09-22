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
3. Compuerta: solo los lotes verificados se cargan a Cloud SQL, esquema `fabrica` (producción), cada lote en su transacción. Los fallidos se retienen y se reportan. Existe `--forzar-carga`, apagado por defecto.
4. Inventario a la Google Sheet fija y estado de la corrida en JSON y Excel.
5. Un único correo final (exitoso / con pendientes / fallido) con tabla por lote, enlace a la Sheet, consulta SQL del lote y el Excel de estado adjunto. Correo de fallo en lenguaje no técnico: qué pasó y qué hacer.

Otras decisiones fijas:

- Un solo token de Google (Drive + Sheets + Gmail) de la cuenta "fábrica de contenidos", en la raíz del repo (`credentials.json` + `token.json`). Se renueva con `python renovar_token.py`.
- Estado por lote en `corridas/` (ignorado por git) para reanudar sin duplicar; `--rehacer PASO` fuerza repetir.
- Comparaciones origen↔clon SIEMPRE por nombre canónico (`flujo_lib/nombres.py`): base intacta + extensión en minúscula, jpg/jpeg ≡ png. Sin esto, una reejecución de la clonación borraría los PNG convertidos y volvería a copiar los JPG.
- Reglas de ubicación por tipo de carpeta: ACTIVIDADES MOODLE → txt; SCORM → zip; PDF, FICHAS, REVISTA, GLOSARIO → pdf; PORTADA MATERIA → png; PODCAST → mp3; CONTENIDOS, GUION GRÁFICO, GUION PODCAST, QA → sin restricción por ahora.
- Primera entrega: un comando ejecutado por una persona. Frontend/backend vienen después; por eso toda la lógica vive en `flujo_lib/` y `run_flujo.py` solo orquesta.


## 2. Estado actual (fases 1 y 2 implementadas y revisadas, SIN commit)

Todo está en el árbol de trabajo, sin commit. Suite verificada al corte:

```
python -m unittest discover -s tests
Ran 255 tests  OK
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
| `flujo_lib/estado.py` | `EstadoCorrida`: JSON por Excel, pasos `destino, clonacion, formato, verificacion, carga`, `exportar_excel` |
| `flujo_lib/prevalidacion.py` | `prevalidar` → hallazgos ok/aviso/error por área (excel, token, drive, correo, db) |
| `flujo_lib/README.md` | Descripción de la librería y cómo correr pruebas |
| `run_flujo.py` | Orquestador en proceso único hasta formato, verificación, inventario y compuerta; deja la carga en `pendiente` o `omitido` |
| `renovar_token.py` | Autorización interactiva del token único |
| `tests/fake_drive.py` | Drive simulado en memoria (get/list/create/copy/update/about, fallos inyectables) |
| `tests/test_*.py` | Pruebas de cada módulo |

Modificados: `.gitignore` (agrega `corridas/`), `README.md`, `DOCUMENTACION_PROCESO.md`, `DICCIONARIO_DATOS_EXCEL.md`, `CHECKLIST_ENTREGA.md`, `ARCHIVOS.md`.

No tocados a propósito (siguen con la semántica vieja, destino = carpeta final, tokens propios): `CAMBIAR_FORMATO/`, `CLONACION_CARPETA/`, `LMS_Fabrica/`, `rutas_excel.py`.

Hay un `git stash` viejo ("borrador carpeta_destino") de un intento anterior. No sirve ya; se puede borrar con `git stash drop`.


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
- Compuerta en `run_flujo.py`: implementada. Los lotes `con_diferencias` quedan con carga `omitido`, salvo `--forzar-carga`, que los deja `pendiente` para fase 3. Código de salida 2 = con pendientes.

### 3.2 Fase 3: carga a GCP y correo único

- `flujo_lib/gcp.py`: port en proceso de `LMS_Fabrica/generar_base_rutas.py` (escaneo del destino resuelto: `escanear_ruta_drive`, metadata de programa, `IdResolver`) y de `LMS_Fabrica/cargar_base_gcp.py` (`cargar_csv`). Programa = nombre de la carpeta clonada (= nombre del origen). Un lote = una transacción; `--schema` default `fabrica`; `--simular` no escribe; `--actualizar` no existe en el run único. Anti-duplicados por enlace se mantiene.
- `flujo_lib/notificar.py`: un correo por corrida vía Gmail API con el token único. Cuerpo: título por resultado, tabla por lote (destino, clonación, formato, verificación, carga), enlace a la Sheet, consulta SQL por lote (reutilizar `LMS_Fabrica/notificar_carga_lms.consulta_sql`), adjunto `corridas/<excel>.estado.xlsx`. Correo de fallo: motivo y acción de `ErrorFlujo`, sin trazas. Destinatarios: `CORREOS_AVISO` del `.env` de la raíz.
- `run_flujo.py`: integrar formato → verificación → compuerta → carga → inventario → correo; resumen final y códigos de salida 0/1/2.
- Cierre: convertir los scripts antiguos en envoltorios de `flujo_lib` o retirarlos; un solo `.env` en la raíz; actualizar toda la documentación y `CHECKLIST_ENTREGA.md`; quitar la nota de migración.

### 3.3 Fase 4: pruebas de extremo a extremo y primera corrida

- Prueba end-to-end con FakeDrive: Excel de 2 lotes, un lote con JPG y un archivo mal ubicado; comprobar estado, compuerta y correo (con Gmail simulado).
- Primera corrida real supervisada con `--simular`, luego `--schema fabrica_pruebas`, y solo después `fabrica`.


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
