# Checklist de entrega — Automatización Inventario

Lista de verificación al cierre de cada corrida o lote.

Hay dos formas de ejecutar: el **run único** (`run_flujo.py`, recomendado) y
los **scripts por módulo** (semántica anterior, vigentes hasta terminar la
migración). El run único ya integra conversión, verificación, inventario,
carga transaccional y un único correo final.


--------------------------------------------------
A. PREPARACIÓN
--------------------------------------------------

- [ ] `RUTAS.xlsx` con columnas `cliente`, `etiqueta`, `origen`, `destino` (`cliente` obligatorio)
- [ ] `destino` es la carpeta **raíz**; no se creó a mano la subcarpeta del programa
      (la crea el flujo con el nombre exacto del origen)
- [ ] Excel guardado y cerrado
- [ ] Enlaces o IDs de origen y destino válidos y distintos; sin pares origen/destino repetidos
- [ ] Valores de `cliente` válidos: `PRODUCTO`, `TANIA` o `LMS_correcciones`
- [ ] Token único en la **raíz del repo**: `credentials.json` + `token.json` (Drive, Sheets, Gmail)
      de la cuenta fábrica de contenidos (`python renovar_token.py` si falta o venció)
- [ ] `.env` en la raíz con `CORREOS_AVISO` y `DB_*`
- [ ] Dependencias instaladas (`pip install -r requirements.txt`)
- [ ] (Scripts por módulo) credenciales propias de cada carpeta vigentes


--------------------------------------------------
B. EJECUCIÓN (RUN ÚNICO)
--------------------------------------------------

- [ ] Ejecución de `python run_flujo.py --excel "<RUTA>\RUTAS.xlsx"`
- [ ] Prevalidación sin líneas `ERROR` (los `AVISO` revisados)
- [ ] Código de salida `0` (todos los pasos de todos los lotes en OK)
- [ ] `corridas/<excel>.estado.xlsx` revisado: todos los pasos en verde;
      "Qué pasó" / "Qué hacer" vacías
- [ ] Carpeta del programa creada (o reutilizada) dentro de la raíz con el nombre exacto del origen
- [ ] Si hubo lotes fallidos: causa corregida y reejecutado el mismo comando (retoma donde quedó);
      `--rehacer clonacion` solo si hacía falta resincronizar
- [ ] Log de la corrida disponible en `corridas/logs/`

Ejecución por módulos: continuar con las secciones C a E.


--------------------------------------------------
C. CONVERSIÓN JPG → PNG
--------------------------------------------------

Run único: integrado; se hace en el clon conservando el nombre base.

Scripts por módulo:

- [ ] Carpeta de Drive indicada con `--carpeta`
- [ ] Ejecución sin error
- [ ] Material del lote disponible en PNG


--------------------------------------------------
D. CLONACIÓN + INVENTARIO + CORREO 1
--------------------------------------------------

Run único: la clonación se valida en el bloque B. Inventario, Sheet y correo 1
se reemplazan por la verificación origen vs clon y el correo único final.

Scripts por módulo:

- [ ] Ejecución de `python clone_carpeta_drive.py --excel "<RUTA>\RUTAS.xlsx"`
- [ ] Clonación origen → destino completada
- [ ] Inventario local generado (Excel de reporte)
- [ ] Google Sheet de inventario actualizada
- [ ] Correo 1 enviado con el enlace de la Sheet
- [ ] Conteos origen vs destino coherentes


--------------------------------------------------
E. CARGA LMS / GCP + CORREO 2
--------------------------------------------------

Run único: integrado; carga al esquema `fabrica_pruebas` solo los lotes
verificados y envía un correo único con el Excel de estado. Producción
(`fabrica`) se pide a mano con `--schema fabrica`.

Scripts por módulo:

- [ ] CSV generado (`generar_base_rutas.py`)
- [ ] CSV con filas
- [ ] Carga a `fabrica_pruebas` completada
- [ ] Correo 2 enviado (resumen + consulta SQL)
- [ ] Resultados verificados en Cloud SQL Studio
- [ ] Cliente y raíz del lote correctos (`LMS_correcciones` → raíz LMS_Carga)


--------------------------------------------------
F. EXCLUSIONES DEL FLUJO DIARIO
--------------------------------------------------

- [ ] No se ejecutó `clonar_esquema_pruebas.py`
- [ ] No se utilizó `--schema fabrica` (producción) salvo autorización expresa:
      mientras se valida el flujo todo va a `fabrica_pruebas`
- [ ] No se versionaron secretos ni artefactos de corrida (`corridas/` incluido)
- [ ] No se modificó nada dentro de las carpetas origen


--------------------------------------------------
G. CIERRE
--------------------------------------------------

- [ ] Documentación revisada si hubo cambios de proceso
- [ ] Lote registrado como entregado

---

## Registro

| Bloque | OK | Observación |
|---|---|---|
| Preparación | | |
| Ejecución (run único) | | |
| Formato | | integrado en el run único |
| Clonación + correo 1 | | |
| Verificación | | integrada en el run único |
| GCP + correo único | | integrado en el run único |
| Cierre | | |

**Fecha:** _______________  
**Lote / etiqueta:** _______________  
**Responsable:** _______________  
