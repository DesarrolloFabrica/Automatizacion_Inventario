# Reemplazar un solo archivo

En la página principal, usa el formulario **Reemplazar un solo archivo**.

1. Corrige el contenido del archivo en Drive origen, conservando su ID.
2. Indica la carpeta que lo contiene directamente y su nombre exacto con extensión: por ejemplo `soyunarchivo.mp3`.
3. Indica la carpeta que contiene directamente su copia en destino.
4. Deja vacío el nombre en destino si es igual; si la migración normalizó el nombre, escribe el nombre existente con extensión. Este campo localiza el archivo y no lo renombra.
5. Puedes pulsar **Validar sin modificar** antes de **Reemplazar este archivo**.

`soyunarchivo.pdf`, `soyunarchivo.mp3` y `soyunarchivo.mp4` se buscan por separado. No se recorren subcarpetas ni se crean carpetas o archivos. Si falta un archivo, hay nombres duplicados, cambia el formato o falta el registro SQL, la validación impide empezar el reemplazo. Si la copia tiene vínculo de origen, debe coincidir con el archivo seleccionado.

Se actualiza el contenido del archivo existente mediante `files.update` de Drive; conserva ID, nombre y enlace. Se verifica su MD5 y se actualiza únicamente la huella SHA-256 de su fila en la base, conservando relaciones y recursos Moodle. No se ejecuta la carga por programa ni se modifica el archivo de origen. No admite documentos nativos de Google ni conversiones entre formatos.

El servicio impide el reemplazo mientras haya cargas de carpetas pendientes o activas. `SIMULAR_FORZADO` mantiene esta operación en validación sin escritura. La validación requiere acceso a Drive y a la base configurada.

Drive y PostgreSQL no comparten transacción: si el contenido se actualiza y después falla la sincronización o verificación, se informa el resultado parcial. Repetir sobre el mismo archivo completa la operación sin crear una copia. Una pérdida de conexión del navegador puede dejar el resultado sin confirmar; comprueba el destino antes de reintentar.

API autenticada: `POST /api/archivo-unico`, con `origen`, `nombre`, `destino`, `nombre_destino` opcional y `simular` (por defecto `true`). La ejecución HTTP es síncrona: para archivos grandes debe considerarse el tiempo máximo del servidor y del proxy. No genera una corrida en el historial actual.
