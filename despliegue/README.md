# Desplegar la fábrica de contenido en Google Cloud

Guía completa, de cero, para publicar la web de la fábrica de contenido en
**Cloud Run**. Está escrita para alguien que se maneja en informática pero nunca
ha desplegado nada en Google Cloud: cada paso trae el comando exacto y qué se
espera ver.

Todos los comandos son de **PowerShell en Windows**. El acento grave (`` ` ``) al
final de una línea significa "el comando sigue en la línea siguiente".

---

> ## ⚠️ ADVERTENCIA — ESTE SERVICIO ESCRIBE EN PRODUCCIÓN
>
> La web **no es un visor**. Cada corrida **copia y modifica carpetas reales de
> Google Drive** de la fábrica de contenidos y **escribe en la base de datos de
> producción** (`planner_db`, esquema `fabrica`), además de enviar correos desde
> la cuenta `fabricadecontenidos@cun.edu.co`.
>
> **Nunca** se despliega con `--allow-unauthenticated`. **Nunca** se deja la URL
> abierta al público ni se comparte fuera del equipo. Cualquiera que abra la
> página puede lanzar una corrida que mueve archivos y toca la base.
>
> Si alguna vez ves el servicio marcado como "Permitir invocaciones no
> autenticadas" en la consola de Cloud Run, quítalo inmediatamente
> (ver [9. Restringir quién entra](#9-restringir-quién-entra)).

---

## Índice

1. [Qué vas a montar](#1-qué-vas-a-montar)
2. [Requisitos previos](#2-requisitos-previos)
3. [Habilitar las APIs](#3-habilitar-las-apis)
4. [Bucket de estado](#4-bucket-de-estado)
5. [Cuenta de servicio y permisos](#5-cuenta-de-servicio-y-permisos)
6. [Secretos](#6-secretos)
7. [Conexión a Cloud SQL](#7-conexión-a-cloud-sql)
8. [Desplegar](#8-desplegar)
9. [Restringir quién entra](#9-restringir-quién-entra)
10. [Registros y actualizaciones](#10-registros-y-actualizaciones)
11. [Problemas frecuentes](#11-problemas-frecuentes)
12. [Lista de verificación final](#12-lista-de-verificación-final)

---

## 1. Qué vas a montar

| Pieza | Para qué |
|---|---|
| **Cloud Run** (servicio `fabrica-contenido-web`) | Ejecuta la web: FastAPI sirve la página y lanza las corridas en hilos de fondo. |
| **Artifact Registry** | Guarda la imagen de Docker que construye Cloud Build a partir del `Dockerfile`. |
| **Cloud Storage** (un bucket) | Guarda el estado, el Excel de estado y los logs de cada corrida, para que sobrevivan a un reinicio. |
| **Secret Manager** | Guarda la contraseña de la base y las credenciales de Google. Nunca van dentro de la imagen. |
| **Cloud SQL** (`planner-postgres`) | La base `planner_db`, ya existente. Se conecta por el conector integrado de Cloud Run. |
| **Cuenta de servicio** | La identidad con la que corre el servicio, con los permisos mínimos. |

El detalle de cómo encajan está en [`ARQUITECTURA.md`](ARQUITECTURA.md).

Valores del proyecto que se usan en toda la guía:

| Dato | Valor |
|---|---|
| Proyecto | `it-fab-contenido-edu-1` |
| Instancia de base de datos | `planner-postgres` |
| Base de datos | `planner_db` |
| Esquemas | `fabrica_pruebas` (pruebas) y `fabrica` (producción) |
| Cuenta de Google del flujo | `fabricadecontenidos@cun.edu.co` |

Todo lo que aparezca entre `< >` lo tienes que sustituir por un valor real. **No
inventes**: si no sabes la región o el usuario de la base, pregunta antes de
seguir.

---

## 2. Requisitos previos

### 2.1 Instalar Google Cloud CLI

`gcloud` es la herramienta de línea de comandos de Google Cloud. En Windows:

1. Descarga el instalador: <https://cloud.google.com/sdk/docs/install#windows>
   (archivo `GoogleCloudSDKInstaller.exe`).
2. Ejecútalo y acepta las opciones por defecto. Deja marcado "Bundled Python" si
   no sabes qué elegir: no interfiere con tu Python 3.14.
3. **Cierra y vuelve a abrir PowerShell** (si no, no encuentra el comando).

Comprueba que quedó instalado:

```powershell
gcloud version
```

Debe imprimir varias líneas empezando por `Google Cloud SDK <versión>`.

### 2.2 Iniciar sesión

Son dos inicios de sesión distintos y hacen falta los dos:

```powershell
# 1) Tu sesión para usar los comandos de gcloud
gcloud auth login

# 2) Credenciales para que las librerías locales hablen con Google (ADC)
gcloud auth application-default login
```

Cada uno abre el navegador. Entra con tu cuenta **@cun.edu.co** que tenga
permisos en el proyecto.

### 2.3 Elegir el proyecto y la región

```powershell
gcloud config set project it-fab-contenido-edu-1
gcloud config set run/region <REGION>
```

`<REGION>` tiene que ser **la misma región de la instancia de Cloud SQL**. Para
averiguarla:

```powershell
gcloud sql instances describe planner-postgres --format="value(region,connectionName)"
```

Eso imprime, por ejemplo, `us-central1` y
`it-fab-contenido-edu-1:us-central1:planner-postgres`. **Apunta ese segundo
valor**: es el *nombre de conexión* y lo vas a necesitar varias veces.

Comprueba la configuración:

```powershell
gcloud config list
```

### 2.4 Permisos que necesitas tú

Para seguir esta guía tu usuario necesita, en el proyecto, el rol **Editor**
(`roles/editor`) o, mejor, este conjunto mínimo: `roles/run.admin`,
`roles/iam.serviceAccountAdmin`, `roles/iam.serviceAccountUser`,
`roles/secretmanager.admin`, `roles/storage.admin`, `roles/cloudsql.admin` y
`roles/serviceusage.serviceUsageAdmin`.

Si algún comando falla con `PERMISSION_DENIED`, ese es el motivo: pídeselos al
administrador del proyecto.

---

## 3. Habilitar las APIs

Una API deshabilitada da errores confusos más adelante ("API has not been used
in project..."). Se habilitan todas de una vez:

```powershell
gcloud services enable `
  run.googleapis.com `
  cloudbuild.googleapis.com `
  artifactregistry.googleapis.com `
  secretmanager.googleapis.com `
  storage.googleapis.com `
  sqladmin.googleapis.com `
  iam.googleapis.com `
  iamcredentials.googleapis.com `
  logging.googleapis.com `
  drive.googleapis.com `
  sheets.googleapis.com `
  gmail.googleapis.com
```

Tarda uno o dos minutos. Qué es cada una:

| API | Para qué |
|---|---|
| `run` | El servicio web. |
| `cloudbuild` | Construye la imagen desde el código (`--source=.`). |
| `artifactregistry` | Guarda la imagen construida. |
| `secretmanager` | Contraseña de la base y credenciales de Google. |
| `storage` | Bucket del estado de las corridas. |
| `sqladmin` | Conector de Cloud Run hacia Cloud SQL. |
| `iam` / `iamcredentials` | Cuenta de servicio y suplantación. |
| `logging` | Registros del servicio. |
| `drive`, `sheets`, `gmail` | Lo que usa el flujo: clonar carpetas, publicar el inventario y enviar el correo final. |

Verifica:

```powershell
gcloud services list --enabled --format="value(config.name)"
```

---

## 4. Bucket de estado

Aquí se guardan el estado JSON, el Excel de estado y los logs de cada corrida.
El nombre de un bucket es **único en todo Google Cloud**, así que conviene
ponerle delante el nombre del proyecto.

```powershell
gcloud storage buckets create gs://<NOMBRE_BUCKET_ESTADO> `
  --project=it-fab-contenido-edu-1 `
  --location=<REGION> `
  --uniform-bucket-level-access `
  --public-access-prevention
```

Sugerencia de nombre: `it-fab-contenido-edu-1-corridas`.

Quien escribe ahí es `flujo_lib/almacen.py`, que entiende destinos con la forma
`gs://<bucket>/<prefijo>`. De ahí sale la variable
`ALMACEN_CORRIDAS=gs://<NOMBRE_BUCKET_ESTADO>/corridas` del archivo de
variables: no hay que crear el prefijo a mano, se crea solo al escribir.

Qué hacen esas dos últimas opciones, y por qué no son opcionales:

- `--uniform-bucket-level-access`: los permisos se dan a nivel de bucket, no
  archivo por archivo. Más simple y mucho más difícil de dejar abierto por error.
- `--public-access-prevention`: **bloquea** que alguien pueda hacer público el
  bucket. Dentro hay nombres de carpetas y rutas internas de la CUN.

Opcional pero recomendado: borrar automáticamente lo que tenga más de un año,
para que el bucket no crezca sin fin.

```powershell
$ciclo = Join-Path $env:TEMP "ciclo.json"
[System.IO.File]::WriteAllText($ciclo, '{"rule":[{"action":{"type":"Delete"},"condition":{"age":365}}]}')
gcloud storage buckets update gs://<NOMBRE_BUCKET_ESTADO> --lifecycle-file="$ciclo"
Remove-Item $ciclo
```

Comprueba:

```powershell
gcloud storage buckets describe gs://<NOMBRE_BUCKET_ESTADO> `
  --format="value(name,location,iamConfiguration.publicAccessPrevention)"
```

---

## 5. Cuenta de servicio y permisos

El servicio **no** debe correr con la cuenta por defecto de Compute Engine: esa
tiene permisos de Editor sobre todo el proyecto. Creamos una propia, con lo
mínimo imprescindible.

### 5.1 Crear la cuenta

```powershell
gcloud iam service-accounts create fabrica-web `
  --display-name="Fabrica de contenido - servicio web" `
  --description="Identidad del servicio de Cloud Run que ejecuta el flujo de la fabrica de contenido"
```

Su dirección será:

```text
fabrica-web@it-fab-contenido-edu-1.iam.gserviceaccount.com
```

Para no repetirla en cada comando, guárdala en una variable de PowerShell (vale
solo mientras esa ventana esté abierta):

```powershell
$SA = "fabrica-web@it-fab-contenido-edu-1.iam.gserviceaccount.com"
```

### 5.2 Acceso al bucket

Solo a **ese** bucket, no a todo Cloud Storage:

```powershell
gcloud storage buckets add-iam-policy-binding gs://<NOMBRE_BUCKET_ESTADO> `
  --member="serviceAccount:$SA" `
  --role="roles/storage.objectAdmin"
```

`objectAdmin` permite leer, escribir y borrar objetos **dentro** del bucket,
pero no crear ni borrar buckets.

### 5.3 Cliente de Cloud SQL

```powershell
gcloud projects add-iam-policy-binding it-fab-contenido-edu-1 `
  --member="serviceAccount:$SA" `
  --role="roles/cloudsql.client"
```

Este rol permite **abrir la conexión**; el usuario y la contraseña de PostgreSQL
siguen haciendo falta. No da acceso a administrar la instancia ni a ver otras
bases.

### 5.4 Escribir registros

```powershell
gcloud projects add-iam-policy-binding it-fab-contenido-edu-1 `
  --member="serviceAccount:$SA" `
  --role="roles/logging.logWriter"
```

### 5.5 Lectura de secretos

Se da **secreto a secreto**, no sobre todo el proyecto. Los comandos están en la
sección siguiente, cuando los secretos ya existan.

### 5.6 Permiso para que tú puedas desplegar con esa cuenta

```powershell
gcloud iam service-accounts add-iam-policy-binding $SA `
  --member="user:<TU_CORREO>@cun.edu.co" `
  --role="roles/iam.serviceAccountUser"
```

Comprueba lo que tiene la cuenta a nivel de proyecto:

```powershell
gcloud projects get-iam-policy it-fab-contenido-edu-1 `
  --flatten="bindings[].members" `
  --filter="bindings.members:$SA" `
  --format="table(bindings.role)"
```

---

## 6. Secretos

Nada de esto se escribe en `variables.env`, ni en el `Dockerfile`, ni en Git.

### 6.1 Contraseña de la base de datos

Crear el secreto (de momento vacío, solo el contenedor):

```powershell
gcloud secrets create fabrica-db-password --replication-policy="automatic"
```

Añadirle el valor. **No uses `Set-Content -Encoding utf8`**: Windows PowerShell
5.1 le mete una marca BOM al principio del archivo y la contraseña llegaría
corrupta, con un error de autenticación imposible de entender. La forma segura:

```powershell
$tmp = Join-Path $env:TEMP "clave.txt"
[System.IO.File]::WriteAllText($tmp, "<CONTRASENA_DE_LA_BASE>", (New-Object System.Text.UTF8Encoding($false)))
gcloud secrets versions add fabrica-db-password --data-file="$tmp"
Remove-Item $tmp
```

Dar acceso de lectura a la cuenta del servicio:

```powershell
gcloud secrets add-iam-policy-binding fabrica-db-password `
  --member="serviceAccount:$SA" `
  --role="roles/secretmanager.secretAccessor"
```

### 6.2 Credenciales de Google: elige una de las dos opciones

El flujo necesita actuar **como `fabricadecontenidos@cun.edu.co`**: es la cuenta
dueña de las carpetas de Drive, la que publica la hoja de inventario y la que
envía los correos. Hay dos maneras de conseguirlo y conviene entender la
diferencia antes de elegir.

| | (a) Cuenta de servicio con delegación de dominio | (b) Token OAuth existente (`token.json`) |
|---|---|---|
| Qué es | Una identidad de máquina autorizada por Google Workspace a actuar en nombre del buzón. | La sesión que ya generó `renovar_token.py`. |
| Cuándo funciona | Cuando el administrador de Workspace de la CUN la autorice. | **Hoy mismo.** |
| ¿Caduca? | No. | Sí: puede caducar o ser revocada (cambio de contraseña, política de la CUN, seis meses sin uso). |
| Riesgo | Bajo: ámbitos acotados y revocable desde la consola de Workspace. | Medio: es una credencial de usuario viviendo en un servidor. |
| Recomendación | **Es lo correcto a largo plazo.** | Puente mientras llega la autorización. |

Lo práctico: monta (b) para arrancar hoy y pide (a) en paralelo. Cambiar de una
a otra es cambiar una variable de entorno y volver a desplegar.

#### Opción (a) — Cuenta de servicio con delegación de dominio

1. Crea la cuenta de servicio que representará al buzón:

   ```powershell
   gcloud iam service-accounts create fabrica-drive `
     --display-name="Fabrica de contenido - Drive/Sheets/Gmail" `
     --description="Actua en nombre de fabricadecontenidos@cun.edu.co mediante delegacion de dominio"
   ```

2. Genera una clave JSON (es un secreto: no la dejes en el repositorio):

   ```powershell
   gcloud iam service-accounts keys create "$env:TEMP\fabrica-drive.json" `
     --iam-account="fabrica-drive@it-fab-contenido-edu-1.iam.gserviceaccount.com"
   ```

3. Apunta el **ID único (numérico)** de la cuenta: es lo que pide Workspace.

   ```powershell
   gcloud iam service-accounts describe "fabrica-drive@it-fab-contenido-edu-1.iam.gserviceaccount.com" `
     --format="value(uniqueId)"
   ```

4. **Pide al administrador de Google Workspace de la CUN** que entre en
   <https://admin.google.com> → *Seguridad* → *Control de datos y acceso* →
   *Controles de API* → *Delegación de todo el dominio* → *Añadir nuevo*, y
   registre:

   - **ID de cliente**: el número del punto 3.
   - **Ámbitos de OAuth**: exactamente estos tres, separados por comas. Son los
     que declara `flujo_lib/drive.py`:

     ```text
     https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/gmail.send
     ```

   Sin este paso, la cuenta de servicio no puede suplantar al buzón y todas las
   llamadas a Drive fallan con `unauthorized_client`. **Es el único paso de toda
   la guía que no depende de ti**: pídelo con tiempo.

5. Guarda la clave como secreto y bórrala del disco:

   ```powershell
   gcloud secrets create fabrica-google-sa --replication-policy="automatic"
   gcloud secrets versions add fabrica-google-sa --data-file="$env:TEMP\fabrica-drive.json"
   Remove-Item "$env:TEMP\fabrica-drive.json"

   gcloud secrets add-iam-policy-binding fabrica-google-sa `
     --member="serviceAccount:$SA" `
     --role="roles/secretmanager.secretAccessor"
   ```

6. En `despliegue\variables.env`:

   ```env
   GOOGLE_CREDENCIALES_MODO=cuenta_servicio
   GOOGLE_SA_JSON=/secretos/cuenta-servicio.json
   GOOGLE_USUARIO_SUPLANTADO=fabricadecontenidos@cun.edu.co
   ```

   Y en el despliegue, el secreto se monta así:

   ```text
   --set-secrets="DB_PASSWORD=fabrica-db-password:latest,/secretos/cuenta-servicio.json=fabrica-google-sa:latest"
   ```

7. Comprueba que el buzón `fabricadecontenidos@cun.edu.co` sigue teniendo acceso
   a las carpetas de Drive de origen y destino: la delegación hereda sus
   permisos, no los amplía.

#### Opción (b) — Reutilizar el token OAuth existente

1. Comprueba que el `token.json` de la raíz del repositorio está vigente y tiene
   los tres ámbitos:

   ```powershell
   python renovar_token.py
   ```

   Al terminar imprime el correo autorizado: tiene que ser
   `fabricadecontenidos@cun.edu.co`.

2. Súbelo como secreto:

   ```powershell
   gcloud secrets create fabrica-google-token --replication-policy="automatic"
   gcloud secrets versions add fabrica-google-token --data-file="token.json"

   gcloud secrets add-iam-policy-binding fabrica-google-token `
     --member="serviceAccount:$SA" `
     --role="roles/secretmanager.secretAccessor"
   ```

3. En `despliegue\variables.env`:

   ```env
   GOOGLE_CREDENCIALES_MODO=token
   GOOGLE_TOKEN_JSON=/secretos/token.json
   ```

4. **Ten presente que caduca.** Cuando el servicio empiece a decir "La sesión de
   Google venció", se renueva en local y se sube una versión nueva del secreto:

   ```powershell
   python renovar_token.py
   gcloud secrets versions add fabrica-google-token --data-file="token.json"
   gcloud run services update fabrica-contenido-web --region=<REGION>   # reinicia y lo recoge
   ```

> El token se monta en `/secretos/token.json` y **no** directamente en
> `/app/token.json`: Cloud Run monta el *directorio* del secreto, así que
> montarlo dentro de `/app` taparía todo el código. El arranque del contenedor
> lo copia a `/app/token.json`, que es donde `flujo_lib/drive.py` lo busca
> (`RUTA_TOKEN = ROOT / "token.json"`).

Lista tus secretos cuando termines:

```powershell
gcloud secrets list --format="table(name,createTime)"
```

---

## 7. Conexión a Cloud SQL

Cloud Run trae un **conector integrado**: al desplegar con
`--add-cloudsql-instances`, la plataforma levanta dentro del contenedor un
socket Unix en `/cloudsql/<nombre de conexión>` y cifra el tráfico hasta la
instancia.

Ventajas, y por eso se hace así:

- **No hay que autorizar ninguna IP** en la instancia. Las instancias de Cloud
  Run no tienen IP fija, así que la lista de IP autorizadas nunca funcionaría; con
  el conector el problema desaparece.
- No hace falta IP pública en la base, ni red VPC, ni túnel, ni proxy aparte.
- La autorización la da el rol `roles/cloudsql.client` de la cuenta de servicio
  (paso 5.3), más el usuario y la contraseña de PostgreSQL de siempre.

Lo único que cambia en la configuración: el "host" de PostgreSQL no es una
dirección, es una carpeta.

```env
DB_HOST=/cloudsql/it-fab-contenido-edu-1:<REGION>:planner-postgres
DB_PORT=5432
DB_NAME=planner_db
DB_USER=<USUARIO_BD>
```

`psycopg2` entiende que un host que empieza por `/` es un socket Unix, así que
`flujo_lib/gcp.py` (`conectar_desde_env`) funciona sin tocar una línea. `DB_PORT`
se ignora en ese modo, pero se deja por claridad.

El nombre de conexión, si no lo apuntaste en el paso 2.3:

```powershell
gcloud sql instances describe planner-postgres --format="value(connectionName)"
```

Y confirma que el usuario de la base existe:

```powershell
gcloud sql users list --instance=planner-postgres
```

---

## 8. Desplegar

### 8.1 Preparar las variables

```powershell
copy despliegue\variables.env.example despliegue\variables.env
notepad despliegue\variables.env
```

Rellena todo lo que esté entre `< >`. Ese archivo **no se sube a Git**.

### 8.2 Opción fácil: el script

```powershell
powershell -ExecutionPolicy Bypass -File despliegue\desplegar.ps1
```

Comprueba que `gcloud` existe, valida que no falte ninguna variable, te enseña
lo que va a hacer y pide confirmación antes de desplegar. Para ver el plan sin
desplegar nada:

```powershell
powershell -ExecutionPolicy Bypass -File despliegue\desplegar.ps1 -SoloMostrar
```

### 8.3 Opción manual: el comando completo

Desde la **raíz del repositorio** (donde está el `Dockerfile`):

```powershell
gcloud run deploy fabrica-contenido-web `
  --source=. `
  --region=<REGION> `
  --project=it-fab-contenido-edu-1 `
  --service-account="fabrica-web@it-fab-contenido-edu-1.iam.gserviceaccount.com" `
  --add-cloudsql-instances="it-fab-contenido-edu-1:<REGION>:planner-postgres" `
  --set-env-vars="^|^DB_HOST=/cloudsql/it-fab-contenido-edu-1:<REGION>:planner-postgres|DB_PORT=5432|DB_NAME=planner_db|DB_USER=<USUARIO_BD>|SCHEMA_POR_DEFECTO=fabrica_pruebas|LMS_SCHEMA=fabrica_pruebas|BUCKET_ESTADO=<NOMBRE_BUCKET_ESTADO>|ALMACEN_CORRIDAS=gs://<NOMBRE_BUCKET_ESTADO>/corridas|DIR_CORRIDAS=/app/corridas|CORREOS_AVISO=<correo1@cun.edu.co>,<correo2@cun.edu.co>|GOOGLE_CREDENCIALES_MODO=token|GOOGLE_TOKEN_JSON=/secretos/token.json|SIMULAR_POR_DEFECTO=1|FORZAR_CARGA_POR_DEFECTO=0|TZ=America/Bogota" `
  --set-secrets="DB_PASSWORD=fabrica-db-password:latest,/secretos/token.json=fabrica-google-token:latest" `
  --min-instances=1 `
  --max-instances=1 `
  --no-cpu-throttling `
  --cpu=2 `
  --memory=2Gi `
  --concurrency=20 `
  --timeout=3600 `
  --no-allow-unauthenticated
```

> **El `^|^` del principio de `--set-env-vars` no es un error de copiado.** Le
> dice a `gcloud` que separe las variables por `|` en vez de por coma. Hace
> falta porque `CORREOS_AVISO` lleva comas dentro; sin eso, `gcloud` parte la
> lista de correos en variables sueltas y el despliegue falla.

La primera vez tarda entre 5 y 10 minutos, porque construye la imagen. Al
terminar imprime la URL del servicio, del estilo
`https://fabrica-contenido-web-xxxxxxxxxx-uc.a.run.app`.

### Por qué cada opción

| Opción | Motivo |
|---|---|
| `--source=.` | Cloud Build construye la imagen con el `Dockerfile` del repositorio. No hace falta tener Docker funcionando en tu equipo. |
| `--service-account` | Corre con la cuenta de permisos mínimos, no con la de Compute por defecto. |
| `--add-cloudsql-instances` | Monta el socket Unix de Cloud SQL (sección 7). |
| `--set-secrets` | `DB_PASSWORD` llega como variable de entorno desde Secret Manager; el token, como archivo montado. Ninguno queda dentro de la imagen. |
| `--min-instances=1` | Siempre hay una instancia viva. Sin esto, Cloud Run apaga el contenedor cuando no hay peticiones y **mataría una corrida en marcha**. |
| `--max-instances=1` | Las corridas viven en hilos y se coordinan con candados en memoria. Con dos instancias, cada una tendría su propio estado y dos personas podrían lanzar la misma corrida a la vez. |
| `--no-cpu-throttling` | CPU siempre asignada. Por defecto, Cloud Run le quita la CPU al contenedor entre peticiones, y aquí el trabajo de verdad ocurre **después** de responder, en los hilos de fondo: sin esto, una clonación se quedaría congelada. |
| `--cpu=2 --memory=2Gi` | Clonar, convertir imágenes con Pillow y armar los Excel consume memoria. Si aparecen errores de memoria, sube a `--memory=4Gi`. |
| `--concurrency=20` | Es una web de equipo, no un portal público. |
| `--timeout=3600` | Una hora de tope por petición. Las corridas largas responden al instante y siguen en el fondo, pero subir un Excel grande o lanzar una prevalidación puede tardar. |
| `--no-allow-unauthenticated` | **Obligatorio.** Ver la advertencia del principio. |

### 8.4 Comprobar que arrancó

```powershell
gcloud run services describe fabrica-contenido-web --region=<REGION> `
  --format="value(status.url,status.conditions[0].status)"
```

Y abrir la web autenticado (el navegador a secas dará 403, y eso es lo correcto):

```powershell
gcloud run services proxy fabrica-contenido-web --region=<REGION> --port=8080
```

Eso abre un túnel local con tu identidad: entra en <http://localhost:8080>.

---

## 9. Restringir quién entra

El despliegue ya va con `--no-allow-unauthenticated`, así que ahora mismo
**nadie** puede entrar. Hay que dar acceso explícito. Dos formas; la segunda es
la cómoda para el equipo.

### Opción A — Rol de invocador, persona por persona

Rápida y suficiente para tres o cuatro personas técnicas:

```powershell
gcloud run services add-iam-policy-binding fabrica-contenido-web `
  --region=<REGION> `
  --member="user:<PERSONA>@cun.edu.co" `
  --role="roles/run.invoker"
```

Mejor todavía, a un grupo de Google Workspace: se gestiona desde Workspace y no
hay que tocar Cloud cada vez que entra o sale alguien del equipo.

```powershell
gcloud run services add-iam-policy-binding fabrica-contenido-web `
  --region=<REGION> `
  --member="group:<GRUPO>@cun.edu.co" `
  --role="roles/run.invoker"
```

**Tiene una pega**: desde el navegador, a secas, no funciona. El navegador no
manda el testigo de identidad de Google, así que cada persona tendría que entrar
por el túnel (`gcloud run services proxy ...`). Para alguien no técnico eso es
un problema.

Ver quién tiene acceso hoy:

```powershell
gcloud run services get-iam-policy fabrica-contenido-web --region=<REGION>
```

Quitar a alguien:

```powershell
gcloud run services remove-iam-policy-binding fabrica-contenido-web `
  --region=<REGION> `
  --member="user:<PERSONA>@cun.edu.co" `
  --role="roles/run.invoker"
```

### Opción B — Identity-Aware Proxy (IAP), entrada por navegador

IAP se pone delante del servicio y pide iniciar sesión con la cuenta
`@cun.edu.co` en una pantalla de Google, como cualquier otra web corporativa. Es
lo recomendable si la web la va a usar gente del equipo de contenidos.

1. Configura la pantalla de consentimiento, una sola vez, de tipo **Interna**:
   <https://console.cloud.google.com/apis/credentials/consent> → *Interno*.

2. Habilita IAP en el servicio:

   ```powershell
   gcloud beta run services update fabrica-contenido-web `
     --region=<REGION> `
     --iap
   ```

3. Da acceso a las personas o al grupo:

   ```powershell
   gcloud beta iap web add-iam-policy-binding `
     --region=<REGION> `
     --resource-type=cloud-run `
     --service=fabrica-contenido-web `
     --member="group:<GRUPO>@cun.edu.co" `
     --role="roles/iap.httpsResourceAccessor"
   ```

4. Deja que **solo IAP** pueda invocar el servicio; si no, el rol de invocador
   directo seguiría siendo otra puerta de entrada:

   ```powershell
   gcloud run services add-iam-policy-binding fabrica-contenido-web `
     --region=<REGION> `
     --member="serviceAccount:service-<NUMERO_DE_PROYECTO>@gcp-sa-iap.iam.gserviceaccount.com" `
     --role="roles/run.invoker"
   ```

   El número del proyecto (no es el nombre, es un número largo):

   ```powershell
   gcloud projects describe it-fab-contenido-edu-1 --format="value(projectNumber)"
   ```

Con IAP, la URL del servicio se abre en el navegador y pide la cuenta de la CUN.
Quien no esté en la lista ve un "No tienes acceso" y nunca llega a la aplicación.

> **Ni con IAP ni sin él se usa `--allow-unauthenticated`.** Ese valor convierte
> la URL en pública para todo internet, y este servicio escribe en Drive y en la
> base de producción.

---

## 10. Registros y actualizaciones

### Ver los registros

Los últimos 50, en pantalla:

```powershell
gcloud run services logs read fabrica-contenido-web --region=<REGION> --limit=50
```

En vivo, mientras corre:

```powershell
gcloud beta run services logs tail fabrica-contenido-web --region=<REGION>
```

Solo los errores de las últimas horas:

```powershell
gcloud logging read `
  'resource.type="cloud_run_revision" AND resource.labels.service_name="fabrica-contenido-web" AND severity>=ERROR' `
  --limit=50 --freshness=6h --format="table(timestamp,severity,textPayload)"
```

También en la consola web: *Cloud Run → fabrica-contenido-web → Registros*.

Ojo con la diferencia: el log técnico de cada corrida
(`corridas/logs/<id>_<excel>.log`) lo escribe el flujo y acaba en el bucket de
estado; Cloud Logging tiene lo del servidor. Para diagnosticar una corrida
concreta, mira primero el del bucket.

### Actualizar a una versión nueva

Es exactamente el mismo comando del paso 8: Cloud Run crea una **revisión**
nueva y manda el tráfico a ella solo cuando arranca bien.

```powershell
powershell -ExecutionPolicy Bypass -File despliegue\desplegar.ps1
```

> **Antes de actualizar, comprueba que no haya una corrida en marcha.** Al
> desplegar, la instancia vieja se apaga y una corrida a mitad de camino queda
> interrumpida; habría que relanzarla. Míralo en la web o en el bucket.

Cambiar solo una variable, sin reconstruir la imagen:

```powershell
gcloud run services update fabrica-contenido-web --region=<REGION> `
  --update-env-vars="SCHEMA_POR_DEFECTO=fabrica"
```

Ver el historial de revisiones y volver atrás si algo salió mal:

```powershell
gcloud run revisions list --service=fabrica-contenido-web --region=<REGION>

gcloud run services update-traffic fabrica-contenido-web --region=<REGION> `
  --to-revisions=<REVISION_ANTERIOR>=100
```

---

## 10 bis. Comprobar los permisos de la cuenta de servicio

Un despliegue que arranca bien pero falla al conectarse a la base o a Google casi
siempre es esto: la cuenta de servicio quedó sin los roles necesarios. Se
comprueba en un comando:

```powershell
gcloud projects get-iam-policy <PROYECTO> `
  --flatten="bindings[].members" `
  --filter="bindings.members:<CUENTA_SERVICIO>" `
  --format="value(bindings.role)"
```

Si no devuelve nada, la cuenta no tiene ningún rol en el proyecto y hay que
dárselos. El mínimo es:

```powershell
gcloud projects add-iam-policy-binding <PROYECTO> `
  --member="serviceAccount:<CUENTA_SERVICIO>" --role="roles/cloudsql.client"

gcloud projects add-iam-policy-binding <PROYECTO> `
  --member="serviceAccount:<CUENTA_SERVICIO>" --role="roles/secretmanager.secretAccessor"

gcloud storage buckets add-iam-policy-binding gs://<BUCKET_ESTADO> `
  --member="serviceAccount:<CUENTA_SERVICIO>" --role="roles/storage.objectAdmin"
```

Sin `roles/cloudsql.client` el socket de Cloud SQL está montado pero la conexión
se rechaza, y el mensaje que se ve es «No se pudo conectar a la base de datos».

Los permisos sobre el bucket y sobre los secretos se pueden haber concedido en
el propio recurso en vez de en el proyecto; en ese caso no aparecen en la
consulta de arriba aunque funcionen. El de Cloud SQL sí es de proyecto.


## 11. Problemas frecuentes

| Qué ves | Qué pasa | Cómo se arregla |
|---|---|---|
| `PERMISSION_DENIED` al ejecutar un `gcloud` | Tu usuario no tiene el rol necesario. | Pide los roles del punto 2.4 al administrador del proyecto. |
| `API [run.googleapis.com] not enabled` | Falta habilitar una API. | Repite el paso 3. |
| El despliegue falla en `Building Container` | Error construyendo la imagen. | Abre el log que indica el mensaje; suele ser una dependencia de `requirements-servidor.txt`. Historial: `gcloud builds list --limit=5`. |
| `The user-provided container failed to start and listen on the port` | La aplicación no arrancó, o no escucha en `$PORT`. | Mira los logs (paso 10). Causas típicas: `servidor/app.py` no existe o no expone `app`; un `import` que revienta al arrancar; una variable obligatoria sin definir. |
| `ModuleNotFoundError: No module named 'servidor'` | El paquete no se encuentra. | El `Dockerfile` ya fija `PYTHONPATH=/app`. Revisa que `servidor/app.py` esté dentro de `servidor/` y que el `.dockerignore` no excluya esa carpeta. |
| `ModuleNotFoundError: No module named 'cargar_base_gcp'` (o `publicar_inventario_sheets`) | Falta una carpeta heredada dentro de la imagen. | `flujo_lib` importa de `LMS_Fabrica/` y `CLONACION_CARPETA/` en tiempo de ejecución. Comprueba que el `Dockerfile` las copia y que el `.dockerignore` no las excluye. Este error aparece **a mitad de una corrida**, no al arrancar. |
| `password authentication failed for user` | La contraseña del secreto está mal, o se subió con BOM. | Vuelve a subir la versión del secreto con el método de la sección 6.1 y redespliega. |
| `could not connect to server: No such file or directory /cloudsql/...` | Falta `--add-cloudsql-instances`, o `DB_HOST` no coincide con el nombre de conexión. | Compara los dos valores: tienen que ser idénticos letra por letra. |
| `permission denied for schema fabrica` | El usuario de PostgreSQL no tiene permisos en ese esquema. | Es cosa de la base, no de Cloud Run: pide los permisos al administrador de `planner_db`. |
| `unauthorized_client` al llamar a Drive | Delegación de dominio sin autorizar, o con ámbitos incompletos. | Revisa el paso 6.2 (a): los tres ámbitos exactos y el ID de cliente correcto. |
| `invalid_grant` / "La sesión de Google venció" | El token OAuth caducó o fue revocado. | `python renovar_token.py`, sube una versión nueva del secreto y reinicia el servicio (paso 6.2 (b), punto 4). |
| La corrida se queda parada y no avanza | Se apagó la CPU entre peticiones. | Comprueba que la revisión tiene `--no-cpu-throttling` y `--min-instances=1`. |
| La corrida se interrumpió sola a mitad | El servicio se reinició (despliegue, actualización o mantenimiento de la plataforma). | Es una limitación conocida de esta versión (ver `ARQUITECTURA.md`): hay que relanzar la corrida. El estado por lote evita repetir lo ya hecho. |
| Error 403 al abrir la URL en el navegador | Normal y correcto: el servicio es privado. | Usa `gcloud run services proxy ...` o configura IAP (paso 9). |
| `Memory limit of ... exceeded` | Un lote grande no cupo en memoria. | Redespliega con `--memory=4Gi`. |
| El correo final no llega | Falta `CORREOS_AVISO`, o la corrida fue en modo simulación. | Revisa las variables y con qué modo se lanzó. |
| Dos personas lanzan la misma corrida y se pisan | Hay más de una instancia. | Comprueba `--max-instances=1` en la revisión activa. |

---

## 12. Lista de verificación final

Antes de decir que está en producción:

- [ ] `gcloud run services get-iam-policy` **no** contiene `allUsers`.
- [ ] El servicio corre con `fabrica-web@...`, no con la cuenta de Compute por defecto.
- [ ] El bucket tiene `publicAccessPrevention: enforced`.
- [ ] `SCHEMA_POR_DEFECTO` y `LMS_SCHEMA` están en `fabrica_pruebas`.
- [ ] `SIMULAR_POR_DEFECTO=1`.
- [ ] La revisión activa tiene `min-instances=1`, `max-instances=1` y CPU siempre asignada.
- [ ] `despliegue\variables.env` **no** está en Git (`git status` no lo muestra).
- [ ] No hay ningún `token.json`, `credentials.json` ni `.env` dentro de la imagen.
- [ ] Una corrida de prueba, en modo simulación y contra `fabrica_pruebas`, termina bien.
- [ ] El equipo sabe que **esta web escribe en Drive y en la base de producción**.
