# Diccionario de datos — RUTAS.xlsx

Archivo de entrada del flujo. **Una fila = un lote** a procesar.

Ubicación del Excel: pásala con `--excel` o define la variable `RUTAS_XLSX`.
Si no, se busca `RUTAS.xlsx` en la raíz del repo, en `CLONACION_CARPETA/`,
en `LMS_Fabrica/` y en la carpeta actual.

Antes de correr cualquier script: **guarda y cierra el Excel**.


--------------------------------------------------
FORMATOS ACEPTADOS
--------------------------------------------------

Formato de 4 columnas (el que usa el run único):

| cliente | etiqueta | origen | destino |

- Los encabezados se leen en minúsculas (no importa si escribes `Cliente` o `cliente`).
- La cabecera puede estar en cualquiera de las 10 primeras filas (por ejemplo,
  con un título encima) y en cualquier hoja del libro (se revisa primero la activa).
- Las filas sin cliente, etiqueta, origen ni destino se saltan.

Formato de 3 columnas `| etiqueta | origen | destino |`: solo lo aceptan los
scripts por módulo anteriores. El run único lo rechaza porque `cliente` es
obligatorio.


--------------------------------------------------
COLUMNAS
--------------------------------------------------

## cliente

| Campo | Detalle |
|---|---|
| **Qué es** | Clasificación del lote (quién / qué tipo de carga). |
| **Obligatoria** | **No.** Si la columna falta o la celda está vacía, el run único deduce el cliente de dónde cuelga la carpeta origen en Drive. Solo es error escribir un valor que no existe. |
| **Valores válidos** | `PRODUCTO`, `TANIA`, `LMS_correcciones` (y alias como `CORRECCIONES`, `LMS_CORRECCIONES`, `LMS_correccion`). Sin distinguir mayúsculas ni acentos. |
| **Cómo se usa** | Se traduce a cliente + raíz en Cloud SQL. |
| **Detección automática** | El material cuelga en Drive de una carpeta con el nombre del cliente: `.../Q2/PRODUCTO/ESCUELA_X/PROGRAMA/...`. El flujo sube por las carpetas padre del origen hasta encontrarla y, de paso, toma la escuela. Si la celda del Excel trae valor, **manda el Excel**, y si no coincide con Drive se avisa en el log y en el correo. |
| **Mapeo actual a GCP** | Ver tabla abajo. |
| **Ejemplo** | `PRODUCTO` |

### Mapeo actual Excel → Cloud SQL

| Valor en Excel | cliente en GCP | raíz en GCP |
|---|---|---|
| `PRODUCTO` | PRODUCTO | LMS_Carga |
| `TANIA` | TANIA | LMS_Carga |
| `LMS_correcciones` (y alias) | PRODUCTO | LMS_Carga |

Nota: `LMS_correcciones` es una **clasificación del lote**, no una carpeta raíz.
La raíz canónica en Drive/GCP es siempre `LMS_Carga`.
Si en Drive la carpeta destino se llama `LMS_CORRECCIONES`, el escaneo especial
de escuelas sigue aplicándose, pero en la base se guarda raíz `LMS_Carga`.


## etiqueta

| Campo | Detalle |
|---|---|
| **Qué es** | Apodo humano del lote (para logs, estado de la corrida, inventarios y correos). |
| **Obligatoria** | Recomendada. Si viene vacía, el flujo usa `Fila N`. |
| **Se guarda en GCP** | No. Solo sirve para identificar el lote en operación. |
| **Ejemplo** | `Contaduria Q2 lote 12` |


## origen

| Campo | Detalle |
|---|---|
| **Qué es** | Carpeta de Google Drive **de donde** se copia. |
| **Obligatoria** | Sí. |
| **Formato** | URL de carpeta Drive (`.../folders/ID`, `.../open?id=ID`) o ID de carpeta. |
| **Uso en el run único** | Se clona dentro del destino. Su **nombre en Drive** es el nombre de la carpeta que el flujo crea en el destino. Nunca se modifica nada dentro del origen. |
| **Uso en carga GCP** | Solo referencia; **no** se escanea. |
| **Ejemplo** | `https://drive.google.com/drive/folders/ID_AQUI` |


## destino

| Campo | Detalle |
|---|---|
| **Qué es** | Carpeta **raíz** de Google Drive **bajo la cual** se copia. |
| **Obligatoria** | Sí. |
| **Formato** | URL de carpeta Drive o ID de carpeta. Distinta del origen. |
| **Uso en el run único** | El flujo crea dentro de esta raíz una carpeta con el nombre **exacto** del origen (o la reutiliza si ya existe) y clona ahí. **No hay que crear esa subcarpeta a mano.** |
| **Salvaguarda** | Si la raíz ya se llama igual que el origen (Excel antiguo, donde `destino` era la carpeta final), se usa directamente sin crear subcarpeta. |
| **Uso en scripts por módulo** | Semántica anterior: carpeta final de la copia y la **única** que se escanea para armar el CSV. |
| **Ejemplo** | `https://drive.google.com/drive/folders/ID_RAIZ_DESTINO` |

Ejemplo: origen `Contaduría 2026-1` y destino la raíz `LMS_Carga` →
el flujo clona en `LMS_Carga/Contaduría 2026-1`.


--------------------------------------------------
VALIDACIONES DEL RUN ÚNICO
--------------------------------------------------

La prevalidación revisa el Excel completo y reporta **todos** los problemas en
un solo mensaje, para corregirlos de una vez:

```text
ERROR El Excel RUTAS.xlsx tiene 3 fila(s) con errores. Fila 5: cliente vacío.
Fila 7: enlace de origen inválido («...»). Fila 9: origen y destino son la misma
carpeta. Corrige el Excel y vuelve a ejecutar.
```

Se reporta como error:

- Falta la columna `cliente`, `origen` o `destino` (se enumeran las que faltan).
- Cliente vacío o no reconocido.
- Enlace de origen o destino vacío o que no es una URL/ID de carpeta usable.
- Origen y destino son la misma carpeta.
- Dos filas con el mismo par origen + destino (se indica la fila repetida).
- El Excel no tiene lotes debajo de la cabecera.
- El Excel está abierto o bloqueado.

Además, en Drive se comprueba que cada origen y cada raíz destino existan, sean
carpetas y la cuenta fábrica de contenidos tenga acceso.


--------------------------------------------------
REGLAS DE LLENADO
--------------------------------------------------

1. Una fila = un lote. No mezclar varios destinos en la misma celda.
2. `origen` y `destino` deben ser carpetas válidas (URL o ID) y distintas entre sí.
3. `destino` es la **raíz**: no crear a mano la subcarpeta del programa; la crea el flujo.
4. Cierra el Excel antes de ejecutar (si está abierto, falla la lectura).
5. En carga GCP: si el nombre tiene `G`+dígitos se usa ese código; si no, el stem (p. ej. Moodle).
6. El **nombre del programa** en GCP sale del **nombre de la carpeta en Drive**
   (la que el flujo crea, igual al origen), no de la columna `etiqueta`.
7. Un mismo par origen + destino identifica al lote en el estado de la corrida:
   si se vuelve a ejecutar, no se repite lo ya hecho.


--------------------------------------------------
QUE NO ES ESTE EXCEL
--------------------------------------------------

- No es el inventario (eso lo genera la clonación por módulo).
- No es el CSV que va a Cloud SQL (eso lo genera `generar_base_rutas.py`).
- No es el estado de la corrida (`corridas/<excel>.estado.xlsx`, lo genera el run único).
- Origen, destino y cliente del Excel son la fuente de rutas a procesar.


--------------------------------------------------
COLUMNAS ALIAS
--------------------------------------------------

Además de los nombres oficiales, tanto el run único como `LMS_Fabrica` reconocen:

- En lugar de `cliente`: `tipo`, `clasificacion`, `clasificación`
- En lugar de `etiqueta`: `programa`

El emparejamiento es por nombre exacto en minúsculas y sin espacios alrededor:
`Cliente:` u `Origen (URL)` no se reconocen.

Para el día a día, usar siempre: `cliente | etiqueta | origen | destino`.
