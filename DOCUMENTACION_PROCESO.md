# Documentación del proceso — Automatización Inventario

Documentación operativa del flujo de preparación, clonación e indexación de
materiales en Google Drive y Cloud SQL.

> **Nota de migración.** Los scripts por módulo siguen funcionando con su
> semántica anterior (destino = carpeta final) hasta que termine la migración;
> para el run único usar `run_flujo.py`.


==================================================
1. OBJETIVO
==================================================

El run único (`run_flujo.py`), a partir de `RUTAS.xlsx`:

1. Revisa antes de empezar que todo esté listo (Excel, token, acceso a Drive, correos, base de datos)  
2. Crea la carpeta destino de cada lote dentro de la raíz indicada y clona origen → destino  
3. Convierte JPG/JPEG a PNG en el clon, verifica origen vs clon, carga cada lote
   verificado en una transacción de Cloud SQL (`fabrica_pruebas`) y envía un único correo final.

Los scripts por módulo cubren lo mismo por separado: conversión en una carpeta
dada, clonación + inventario + correo 1, CSV + carga + correo 2.


==================================================
2. REQUISITOS DE ENTORNO
==================================================

- Python 3.10 o superior  
- Dependencias de cada módulo (`pip install -r requirements.txt`)  
- **Token único** en la raíz del repo: `credentials.json` + `token.json` con
  permisos de Drive, Sheets y Gmail, autorizado con la cuenta **fábrica de
  contenidos** (`python renovar_token.py`)  
- Archivo `.env` en la raíz a partir de `.env.example` (`CORREOS_AVISO` y `DB_*`)  
- Archivo `RUTAS.xlsx` con las columnas definidas en `DICCIONARIO_DATOS_EXCEL.md`
  (`cliente` obligatorio; `destino` = carpeta raíz de destino)  
- Acceso de la cuenta fábrica de contenidos a las carpetas de Drive del lote y,
  para carga, IP autorizada en Cloud SQL  

Los scripts por módulo conservan sus propios `token.json` y `.env` en cada
carpeta hasta terminar la migración.

Los secretos y archivos de corrida (`corridas/` incluido) no se versionan.


==================================================
3. SECUENCIA DEL PROCESO
==================================================

### Secuencia del run único

```text
0) PREVALIDACIÓN (no escribe nada)
    Excel -> token único -> carpeta origen y raíz destino de cada lote
          -> CORREOS_AVISO -> base de datos y esquema
    Todos los hallazgos juntos (OK / AVISO / ERROR).
    Con algún ERROR: "El flujo no arrancó. Corrige lo anterior y vuelve a ejecutar." (salida 1)
              |
              v
1) POR LOTE (orden de fila del Excel; estado en corridas/<excel>.estado.json)
    destino      -> crea o reutiliza, dentro de la raíz, la carpeta con el nombre del origen
    clonacion    -> copia origen -> carpeta destino (reanudable, sin duplicar)
    formato      -> JPG/JPEG -> PNG en el clon
    verificacion -> origen vs clon por nombre canónico
              |
              v
2) COMPUERTA -> CARGA -> INVENTARIO -> CORREO
    carga a Cloud SQL (esquema fabrica_pruebas) solo de los lotes verificados
    un único correo final con el Excel de estado (o correo de fallo)
```

Si un lote falla en un paso, los pasos restantes de ese lote quedan "Omitido" y
el flujo continúa con el siguiente lote.

### Scripts por módulo (semántica anterior)

```text
1) CAMBIAR_FORMATO      JPG/JPEG -> PNG en la carpeta indicada con --carpeta
2) CLONACION_CARPETA    RUTAS.xlsx -> clonación origen -> destino -> inventario -> Sheet -> correo 1
3) LMS_Fabrica          RUTAS.xlsx (columna destino) -> CSV -> Cloud SQL (fabrica_pruebas) -> correo 2
```

El inventario es el reporte de control de la clonación.  
No corresponde al CSV de carga hacia Cloud SQL.


==================================================
4. EJECUCIÓN
==================================================

### Run único

```powershell
python run_flujo.py --excel "<RUTA>\RUTAS.xlsx"
```

| Parámetro | Descripción |
|---|---|
| `--excel` | Ruta a `RUTAS.xlsx` (también variable `RUTAS_XLSX` o `RUTAS.xlsx` en la raíz) |
| `--schema` | Esquema de Cloud SQL. Por defecto `fabrica_pruebas`. Producción: `--schema fabrica`. No se lee `LMS_SCHEMA` |
| `--simular` | Hace todo menos escribir en la base y enviar correo |
| `--forzar-carga` | Permite cargar lotes con diferencias de verificación. Por defecto apagado |
| `--solo-prevalidar` | Ejecuta la prevalidación y termina |
| `--rehacer PASO` | Repite ese paso aunque el estado diga OK: `destino`, `clonacion`, `formato`, `verificacion`, `carga` o `todo`. Repetible |
| `--no-interactivo` | Si hace falta autorizar Google, falla con mensaje en vez de pedir login |

Códigos de salida:

| Código | Significado |
|---|---|
| `0` | Flujo completo correcto |
| `1` | Prevalidación fallida, algún lote fallido o corrida interrumpida (Ctrl+C) |
| `2` | Hay lotes con diferencias retenidos por la compuerta |

Salidas de cada corrida, en `corridas/` (no versionado):

| Archivo | Contenido |
|---|---|
| `corridas/<excel>.estado.json` | Estado por lote y por paso; permite reanudar |
| `corridas/<excel>.estado.xlsx` | Hoja "Estado" (Etiqueta, Fila, Cliente, Carpeta destino, Enlace, un color por paso, Qué pasó, Qué hacer) y hoja "Corridas" |
| `corridas/logs/<id_corrida>_<excel>.log` | Log de la corrida; la consola muestra lo informativo y el archivo además el detalle técnico |

Al terminar imprime una tabla por lote (etiqueta, carpeta destino, estado de
destino y de clonación) y las rutas de esos tres archivos.

**Reanudación.** El estado identifica cada lote por origen + raíz de destino.
Lo que ya está OK no se repite: un lote clonado se omite en la siguiente
corrida ("ya clonado en una corrida anterior"). Para volver a sincronizar,
`--rehacer clonacion`; para repetir todo, `--rehacer todo`. Un lote fallido se
reintenta al volver a ejecutar el mismo comando y solo se copia lo que falta.

### Ejecución por módulos

Ver la documentación de cada directorio:

- `CAMBIAR_FORMATO/README.md`
- `CLONACION_CARPETA/README.md`
- `LMS_Fabrica/README.md`


==================================================
5. REGLAS OPERATIVAS
==================================================

1. En el Excel, **`destino` es la carpeta raíz**. El run único crea (o reutiliza)
   dentro la carpeta del programa con el nombre exacto del origen. No se crea a
   mano. Si la raíz ya se llama como el origen (Excel antiguo), se usa directamente.  
2. `cliente` es obligatorio. Valores admitidos: `PRODUCTO`, `TANIA`, `LMS_correcciones`
   (`LMS_correcciones` se registra en GCP como cliente PRODUCTO y raíz LMS_Carga).  
3. Los errores del Excel se reportan todos juntos (fila y motivo); se corrige una
   vez y se vuelve a ejecutar.  
4. Un solo token, en la raíz, de la cuenta fábrica de contenidos. Si venció:
   `python renovar_token.py`.  
5. El run único carga al esquema `fabrica_pruebas` por defecto mientras se valida
   el flujo. Producción (`fabrica`) se indica a mano con `--schema fabrica` y solo
   con autorización expresa. Los scripts por módulo siguen igual.  
6. Origen y clon se comparan por **nombre canónico** (base intacta, extensión en
   minúscula, `jpg`/`jpeg` → `png`): `x.JPG` del origen equivale a `x.png` del clon.  
7. Nunca se modifica nada dentro de la carpeta origen.  
8. El estado por lote vive en `corridas/`; reejecutar no repite lo ya hecho.  
9. `clonar_esquema_pruebas.py` no forma parte del flujo diario.  
10. No versionar secretos ni artefactos de corrida.  
11. Código de archivo: prefijo `G` + dígitos si existe; en su defecto, nombre sin extensión.  


==================================================
6. INCIDENCIAS FRECUENTES
==================================================

Los mensajes del run único dicen qué pasó y qué hacer; el detalle técnico queda
en el log de la corrida.

| Mensaje o situación | Verificación |
|---|---|
| `El Excel RUTAS.xlsx está abierto o bloqueado.` | Cerrar el Excel y volver a ejecutar |
| `El Excel RUTAS.xlsx tiene N fila(s) con errores. Fila 5: ...` | Corregir todas las filas indicadas (cliente no reconocido, enlace inválido, origen = destino, par repetido). El cliente vacío no es error: se deduce de Drive |
| `No se pudo saber a qué cliente pertenece el lote «X».` | La carpeta origen no cuelga de una carpeta PRODUCTO, TANIA o LMS_CORRECCIONES: escribir el cliente en el Excel o mover la carpeta en Drive |
| `el Excel dice X pero en Drive la carpeta cuelga de «Y»` | Aviso, no detiene el flujo: manda el Excel. Corregir la columna cliente si el valor del Excel no es el correcto |
| `No se encontró el archivo RUTAS.xlsx.` | Parámetro `--excel` o variable `RUTAS_XLSX` |
| `Hay que autorizar la cuenta fábrica de contenidos en Google.` / `La sesión de Google venció.` | `python renovar_token.py` eligiendo la cuenta fábrica de contenidos |
| `Falta el archivo credentials.json en ...` | Pedir a soporte el `credentials.json` de la cuenta fábrica y copiarlo en la raíz |
| `certificate verify failed` / `self-signed certificate in certificate chain` | El antivirus del equipo (Kaspersky) revisa el tráfico seguro. El flujo lo resuelve solo reconociendo los certificados del equipo; si aparece, comprobar que `certificados_confianza.pem` se creó en la raíz |
| `La cuenta fábrica de contenidos no tiene permiso sobre la carpeta origen del lote «X».` | Pedir acceso a esa carpeta para la cuenta fábrica |
| `No se encontró la carpeta destino del lote «X».` | Revisar el enlace en el Excel; la carpeta pudo moverse o eliminarse |
| `La carpeta ... del lote «X» no es una carpeta de Drive.` | El enlace apunta a un archivo, no a una carpeta |
| `No hay destinatarios de correo configurados.` | `CORREOS_AVISO` en `.env` de la raíz |
| `Faltan datos de la base de datos en el archivo .env: ...` / `No se pudo conectar a la base de datos.` | Variables `DB_*`, IP autorizada en Cloud SQL, conexión a internet |
| `El esquema X no existe en la base de datos.` | Parámetro `--schema` |
| `Google Drive no respondió a tiempo al trabajar con ...` | Volver a ejecutar; el flujo retoma donde quedó |
| `No se pudieron copiar N archivo(s) del lote «X».` | Volver a ejecutar (solo copia lo que falta); si persiste, revisar permisos de esos archivos (rutas en el log) |
| Lote "ya clonado en una corrida anterior" pero el origen cambió | `--rehacer clonacion` |
| `No se pudo actualizar el Excel de estado. El archivo RUTAS.estado.xlsx está abierto o protegido.` | Cerrar ese Excel; la corrida no se detiene (el JSON sí queda al día) y el Excel se regenera en la siguiente corrida |
| `Corrida interrumpida por el usuario.` (Ctrl+C) | Volver a ejecutar el mismo comando; retoma donde quedó |
| El flujo pidió login en un equipo sin navegador | Usar `--no-interactivo` y autorizar aparte con `python renovar_token.py` |
| Conversión con 0 archivos (script por módulo) | Carpeta sin JPG; si `Errores: 0`, el acceso fue correcto |


==================================================
7. CIERRE
==================================================

Validación de entrega: `CHECKLIST_ENTREGA.md`.
