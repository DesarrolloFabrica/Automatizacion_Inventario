<#
=============================================================================
 desplegar.ps1 - Despliegue del servicio web de la fabrica de contenido
                 en Google Cloud Run.

 QUE HACE, EN ORDEN
   1. Comprueba que gcloud este instalado y con sesion iniciada.
   2. Lee despliegue\variables.env y valida que no falte nada ni queden
      marcadores <ASI> sin rellenar.
   3. Arma el comando "gcloud run deploy" (variables de entorno en un archivo
      YAML, para que los correos con comas no rompan nada).
   4. Muestra EXACTAMENTE lo que va a hacer y pide confirmacion.
   5. Despliega y, al terminar, imprime la URL y los siguientes pasos.

 COMO SE USA
   powershell -ExecutionPolicy Bypass -File despliegue\desplegar.ps1
   powershell -ExecutionPolicy Bypass -File despliegue\desplegar.ps1 -SoloMostrar
   powershell -ExecutionPolicy Bypass -File despliegue\desplegar.ps1 -SinConfirmar

 IMPORTANTE
   Este script NO crea el bucket, ni la cuenta de servicio, ni los secretos:
   eso se hace una sola vez siguiendo despliegue\README.md. Aqui solo se
   despliega el codigo.

   El servicio que se despliega ESCRIBE en Google Drive y en la base de datos
   de produccion. Siempre se despliega privado (--no-allow-unauthenticated).
=============================================================================
#>

[CmdletBinding()]
param(
    # Archivo con las variables. Por defecto, el de al lado de este script.
    [string] $Archivo = "",

    # Enseña el plan y termina, sin desplegar nada.
    [switch] $SoloMostrar,

    # Despliega sin preguntar (para automatizaciones; en manual, NO usarlo).
    [switch] $SinConfirmar,

    # Nombres de los secretos en Secret Manager (ver README, seccion 6).
    [string] $SecretoClaveBd        = "fabrica-db-password",
    [string] $SecretoToken          = "fabrica-google-token",
    [string] $SecretoCuentaServicio = "fabrica-google-sa",

    # Recursos de la revision. Se pueden subir si algun lote grande no cabe.
    [string] $Cpu        = "2",
    [string] $Memoria    = "2Gi",
    [int]    $TiempoMax  = 3600
)

# Cualquier error no controlado detiene el script en vez de seguir a medias.
$ErrorActionPreference = "Stop"

# --------------------------------------------------------------------------
# Utilidades de presentacion: todo en espanol y con el mismo formato.
# --------------------------------------------------------------------------

function Escribir-Titulo([string] $texto) {
    Write-Host ""
    Write-Host "== $texto ==" -ForegroundColor Cyan
}

function Escribir-Ok([string] $texto) {
    Write-Host "  OK    $texto" -ForegroundColor Green
}

function Escribir-Aviso([string] $texto) {
    Write-Host "  AVISO $texto" -ForegroundColor Yellow
}

# Termina el script con un mensaje claro de que paso y que hacer.
function Terminar-Con-Error([string] $quePaso, [string] $queHacer) {
    Write-Host ""
    Write-Host "ERROR: $quePaso" -ForegroundColor Red
    if ($queHacer) {
        Write-Host "Que hacer: $queHacer" -ForegroundColor Yellow
    }
    Write-Host ""
    exit 1
}

# --------------------------------------------------------------------------
# 0. Situarse en la raiz del repositorio (este script vive en despliegue\)
# --------------------------------------------------------------------------

$CarpetaScript = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RaizRepo      = Split-Path -Parent $CarpetaScript

if (-not $Archivo) {
    $Archivo = Join-Path $CarpetaScript "variables.env"
}

Write-Host ""
Write-Host "Despliegue de la fabrica de contenido en Cloud Run" -ForegroundColor White
Write-Host "Raiz del repositorio: $RaizRepo"

# --------------------------------------------------------------------------
# 1. Comprobaciones previas
# --------------------------------------------------------------------------

Escribir-Titulo "1. Comprobaciones previas"

# 1.1 gcloud instalado
$gcloud = Get-Command gcloud -ErrorAction SilentlyContinue
if (-not $gcloud) {
    Terminar-Con-Error `
        "No se encontro el comando 'gcloud' (Google Cloud CLI)." `
        ("Instalalo desde https://cloud.google.com/sdk/docs/install#windows, " +
         "CIERRA y vuelve a abrir PowerShell, y ejecuta este script otra vez. " +
         "Ver despliegue\README.md, seccion 2.1.")
}
Escribir-Ok "gcloud encontrado en $($gcloud.Source)"

# 1.2 Sesion iniciada
$cuenta = ""
try {
    $cuenta = (& gcloud auth list --filter=status:ACTIVE --format="value(account)" | Select-Object -First 1)
} catch {
    $cuenta = ""
}
if (-not $cuenta) {
    Terminar-Con-Error `
        "No hay ninguna sesion activa de gcloud." `
        "Ejecuta 'gcloud auth login' con tu cuenta @cun.edu.co y vuelve a intentarlo."
}
Escribir-Ok "Sesion activa: $cuenta"

# 1.3 El Dockerfile tiene que estar en la raiz: es lo que construye Cloud Build
$rutaDockerfile = Join-Path $RaizRepo "Dockerfile"
if (-not (Test-Path $rutaDockerfile)) {
    Terminar-Con-Error `
        "No se encontro el Dockerfile en $RaizRepo." `
        "Ejecuta este script desde el repositorio, sin mover la carpeta despliegue\."
}
Escribir-Ok "Dockerfile encontrado"

# 1.4 Archivo de variables
if (-not (Test-Path $Archivo)) {
    Terminar-Con-Error `
        "No existe el archivo de variables: $Archivo" `
        ("Copialo de la plantilla y rellenalo:  " +
         "copy despliegue\variables.env.example despliegue\variables.env")
}
Escribir-Ok "Archivo de variables: $Archivo"

# --------------------------------------------------------------------------
# 2. Leer las variables
#    Formato NOMBRE=valor, una por linea. Se ignoran comentarios (#) y lineas
#    en blanco. No se interpretan comillas: el valor es literal.
# --------------------------------------------------------------------------

Escribir-Titulo "2. Variables"

$vars = @{}
$numeroLinea = 0
foreach ($linea in (Get-Content -Path $Archivo -Encoding UTF8)) {
    $numeroLinea++
    $texto = $linea.Trim()
    if (-not $texto)             { continue }
    if ($texto.StartsWith("#"))  { continue }

    $corte = $texto.IndexOf("=")
    if ($corte -lt 1) {
        Escribir-Aviso "Linea $numeroLinea ignorada (no tiene la forma NOMBRE=valor): $texto"
        continue
    }

    $nombre = $texto.Substring(0, $corte).Trim()
    $valor  = $texto.Substring($corte + 1).Trim()
    # Quitar comillas de adorno si alguien las puso.
    if ($valor.Length -ge 2) {
        if (($valor.StartsWith('"') -and $valor.EndsWith('"')) -or
            ($valor.StartsWith("'") -and $valor.EndsWith("'"))) {
            $valor = $valor.Substring(1, $valor.Length - 2)
        }
    }
    $vars[$nombre] = $valor
}

# 2.1 Variables obligatorias. Sin una sola de estas, el servicio no funciona.
$obligatorias = @(
    "PROYECTO",
    "REGION",
    "SERVICIO",
    "CUENTA_SERVICIO",
    "CLOUD_SQL_CONNECTION_NAME",
    "DB_HOST",
    "DB_NAME",
    "DB_USER",
    "BUCKET_ESTADO",
    "CORREOS_AVISO",
    "GOOGLE_CREDENCIALES_MODO"
)

$faltan = @()
foreach ($nombre in $obligatorias) {
    if (-not $vars.ContainsKey($nombre) -or -not $vars[$nombre]) {
        $faltan += $nombre
    }
}
if ($faltan.Count -gt 0) {
    Terminar-Con-Error `
        ("Faltan variables obligatorias en ${Archivo}: " + ($faltan -join ", ")) `
        "Abrelas con 'notepad $Archivo' y complétalas. La plantilla explica cada una."
}

# 2.2 Marcadores sin rellenar: es el fallo mas comun y el mas silencioso.
$sinRellenar = @()
foreach ($nombre in $vars.Keys) {
    if ($vars[$nombre] -match "<.+>") {
        $sinRellenar += "$nombre = $($vars[$nombre])"
    }
}
if ($sinRellenar.Count -gt 0) {
    Write-Host ""
    foreach ($item in $sinRellenar) { Write-Host "  $item" -ForegroundColor Red }
    Terminar-Con-Error `
        "Hay variables con marcadores <ASI> sin sustituir (listadas arriba)." `
        "Reemplazalas por los valores reales del proyecto en $Archivo."
}
Escribir-Ok "$($vars.Count) variables leidas, ninguna vacia ni con marcadores"

# 2.3 Coherencia entre DB_HOST y el nombre de conexion de Cloud SQL.
$conexionSql = $vars["CLOUD_SQL_CONNECTION_NAME"]
$hostEsperado = "/cloudsql/$conexionSql"
if ($vars["DB_HOST"] -ne $hostEsperado) {
    Escribir-Aviso "DB_HOST no coincide con el conector de Cloud SQL."
    Escribir-Aviso "  DB_HOST   = $($vars['DB_HOST'])"
    Escribir-Aviso "  esperado  = $hostEsperado"
    Escribir-Aviso "Si no es a proposito, corrigelo: el flujo no podra conectarse."
}

# 2.4 Modo de credenciales de Google y secreto que toca montar.
$modo = $vars["GOOGLE_CREDENCIALES_MODO"].ToLower()
switch ($modo) {
    "token" {
        if (-not $vars.ContainsKey("GOOGLE_TOKEN_JSON") -or -not $vars["GOOGLE_TOKEN_JSON"]) {
            Terminar-Con-Error `
                "GOOGLE_CREDENCIALES_MODO=token pero falta GOOGLE_TOKEN_JSON." `
                "Anade GOOGLE_TOKEN_JSON=/secretos/token.json al archivo de variables."
        }
        $rutaSecretoGoogle = $vars["GOOGLE_TOKEN_JSON"]
        $secretoGoogle     = $SecretoToken
        Escribir-Ok "Credenciales de Google: token OAuth (secreto '$secretoGoogle')"
        Escribir-Aviso "El token de usuario caduca. Lo estable es la cuenta de servicio con delegacion de dominio (README, 6.2 a)."
    }
    "cuenta_servicio" {
        if (-not $vars.ContainsKey("GOOGLE_SA_JSON") -or -not $vars["GOOGLE_SA_JSON"]) {
            Terminar-Con-Error `
                "GOOGLE_CREDENCIALES_MODO=cuenta_servicio pero falta GOOGLE_SA_JSON." `
                "Anade GOOGLE_SA_JSON=/secretos/cuenta-servicio.json al archivo de variables."
        }
        if (-not $vars.ContainsKey("GOOGLE_USUARIO_SUPLANTADO") -or -not $vars["GOOGLE_USUARIO_SUPLANTADO"]) {
            Terminar-Con-Error `
                "GOOGLE_CREDENCIALES_MODO=cuenta_servicio pero falta GOOGLE_USUARIO_SUPLANTADO." `
                "Anade GOOGLE_USUARIO_SUPLANTADO=fabricadecontenidos@cun.edu.co."
        }
        $rutaSecretoGoogle = $vars["GOOGLE_SA_JSON"]
        $secretoGoogle     = $SecretoCuentaServicio
        Escribir-Ok "Credenciales de Google: cuenta de servicio delegada (secreto '$secretoGoogle')"
    }
    default {
        Terminar-Con-Error `
            "GOOGLE_CREDENCIALES_MODO tiene un valor no valido: '$($vars['GOOGLE_CREDENCIALES_MODO'])'." `
            "Solo se admite 'token' o 'cuenta_servicio'. Ver despliegue\README.md, seccion 6.2."
    }
}

# 2.5 Aviso bien visible si se va a cargar directamente en PRODUCCION.
$esquema = $vars["SCHEMA_POR_DEFECTO"]
if ($esquema -eq "fabrica") {
    Write-Host ""
    Write-Host "  ATENCION: SCHEMA_POR_DEFECTO=fabrica (PRODUCCION)." -ForegroundColor Red
    Write-Host "  Las corridas escribiran en la base de produccion salvo que se elija otro esquema." -ForegroundColor Red
}
if ($vars["SIMULAR_POR_DEFECTO"] -eq "0") {
    Escribir-Aviso "SIMULAR_POR_DEFECTO=0: las corridas escribiran de verdad en Drive, en la base y enviaran correo."
}

# --------------------------------------------------------------------------
# 3. Preparar el archivo YAML de variables de entorno
#    Se usa --env-vars-file en lugar de --set-env-vars porque CORREOS_AVISO
#    lleva comas, y gcloud las tomaria como separador de variables.
#    Se excluyen las variables que solo sirven para desplegar (PROYECTO,
#    REGION, ...) y PORT, que la inyecta Cloud Run y no se puede fijar a mano.
# --------------------------------------------------------------------------

Escribir-Titulo "3. Variables de entorno del servicio"

$noSeEnvian = @(
    "PROYECTO", "REGION", "SERVICIO", "CUENTA_SERVICIO",   # solo para el despliegue
    "PORT",                                                 # la pone Cloud Run
    "DB_PASSWORD"                                           # viene de Secret Manager
)

$lineasYaml = New-Object System.Collections.Generic.List[string]
$enviadas   = @()
foreach ($nombre in ($vars.Keys | Sort-Object)) {
    if ($noSeEnvian -contains $nombre) { continue }
    # En YAML, comillas simples; una comilla simple dentro se escribe doblada.
    $valorYaml = $vars[$nombre].Replace("'", "''")
    $lineasYaml.Add("$nombre" + ": '" + $valorYaml + "'")
    $enviadas += $nombre
}

if ($enviadas.Count -eq 0) {
    Terminar-Con-Error `
        "No hay ninguna variable que enviar al servicio." `
        "Revisa el contenido de $Archivo."
}

$rutaYaml = Join-Path $env:TEMP "fabrica-env-$(Get-Date -Format 'yyyyMMddHHmmss').yaml"
# Sin BOM: gcloud no lee bien un YAML con marca de orden de bytes.
[System.IO.File]::WriteAllText($rutaYaml, ($lineasYaml -join "`n") + "`n", (New-Object System.Text.UTF8Encoding($false)))
Escribir-Ok "$($enviadas.Count) variables preparadas: $($enviadas -join ', ')"

# --------------------------------------------------------------------------
# 4. Armar el comando de despliegue
# --------------------------------------------------------------------------

# Secretos: la clave de la base como variable de entorno; las credenciales de
# Google como archivo montado dentro del contenedor.
$secretos = "DB_PASSWORD=${SecretoClaveBd}:latest,${rutaSecretoGoogle}=${secretoGoogle}:latest"

$argumentos = @(
    "run", "deploy", $vars["SERVICIO"],
    "--source=$RaizRepo",
    "--project=$($vars['PROYECTO'])",
    "--region=$($vars['REGION'])",
    "--service-account=$($vars['CUENTA_SERVICIO'])",
    "--add-cloudsql-instances=$conexionSql",
    "--env-vars-file=$rutaYaml",
    "--set-secrets=$secretos",
    # Una instancia siempre viva y solo una: las corridas viven en hilos dentro
    # del proceso, con candados en memoria. Dos instancias = dos estados.
    "--min-instances=1",
    "--max-instances=1",
    # CPU siempre asignada: el trabajo de verdad ocurre DESPUES de responder a
    # la peticion. Con la CPU limitada en reposo, las corridas se congelarian.
    "--no-cpu-throttling",
    "--cpu=$Cpu",
    "--memory=$Memoria",
    "--concurrency=20",
    "--timeout=$TiempoMax",
    # PRIVADO SIEMPRE. Este servicio escribe en Drive y en la base de produccion.
    "--no-allow-unauthenticated",
    "--quiet"
)

# --------------------------------------------------------------------------
# 5. Enseñar el plan
# --------------------------------------------------------------------------

Escribir-Titulo "4. Esto es lo que se va a hacer"

Write-Host "  Proyecto            : $($vars['PROYECTO'])"
Write-Host "  Region              : $($vars['REGION'])"
Write-Host "  Servicio            : $($vars['SERVICIO'])"
Write-Host "  Cuenta de servicio  : $($vars['CUENTA_SERVICIO'])"
Write-Host "  Codigo fuente       : $RaizRepo (se construye con el Dockerfile)"
Write-Host "  Cloud SQL           : $conexionSql"
Write-Host "  Base de datos       : $($vars['DB_NAME']) como $($vars['DB_USER'])"
Write-Host "  Esquema por defecto : $esquema"
Write-Host "  Bucket de estado    : $($vars['BUCKET_ESTADO'])"
Write-Host "  Correos de aviso    : $($vars['CORREOS_AVISO'])"
Write-Host "  Credenciales Google : $modo -> $rutaSecretoGoogle (secreto '$secretoGoogle')"
Write-Host "  Recursos            : $Cpu vCPU, $Memoria, CPU siempre asignada, 1 instancia fija"
Write-Host "  Acceso              : PRIVADO (--no-allow-unauthenticated)"
Write-Host ""
Write-Host "  Comando:" -ForegroundColor DarkGray
Write-Host "  gcloud $($argumentos -join ' ')" -ForegroundColor DarkGray

if ($SoloMostrar) {
    Write-Host ""
    Write-Host "Modo -SoloMostrar: no se ha desplegado nada." -ForegroundColor Yellow
    Remove-Item $rutaYaml -ErrorAction SilentlyContinue
    exit 0
}

# --------------------------------------------------------------------------
# 6. Confirmacion
# --------------------------------------------------------------------------

if (-not $SinConfirmar) {
    Write-Host ""
    Write-Host "  RECUERDA: el servicio escribe en Google Drive y en la base de datos." -ForegroundColor Yellow
    Write-Host "  Si hay una corrida en marcha, este despliegue la va a interrumpir." -ForegroundColor Yellow
    Write-Host ""
    $respuesta = Read-Host "Escribe SI (en mayusculas) para desplegar"
    if ($respuesta -cne "SI") {
        Write-Host ""
        Write-Host "Cancelado: no se ha desplegado nada." -ForegroundColor Yellow
        Remove-Item $rutaYaml -ErrorAction SilentlyContinue
        exit 0
    }
}

# --------------------------------------------------------------------------
# 7. Desplegar
# --------------------------------------------------------------------------

Escribir-Titulo "5. Desplegando (la primera vez tarda entre 5 y 10 minutos)"

& gcloud @argumentos
$codigo = $LASTEXITCODE

# El YAML lleva la configuracion del proyecto: se borra pase lo que pase.
Remove-Item $rutaYaml -ErrorAction SilentlyContinue

if ($codigo -ne 0) {
    Terminar-Con-Error `
        "El despliegue fallo (gcloud devolvio el codigo $codigo)." `
        ("Mira el mensaje de arriba y la seccion 'Problemas frecuentes' de " +
         "despliegue\README.md. Para el log de construccion: gcloud builds list --limit=5")
}

# --------------------------------------------------------------------------
# 8. Resultado
# --------------------------------------------------------------------------

Escribir-Titulo "6. Listo"

$url = ""
try {
    $url = (& gcloud run services describe $vars["SERVICIO"] `
                --project=$($vars["PROYECTO"]) `
                --region=$($vars["REGION"]) `
                --format="value(status.url)")
} catch {
    $url = ""
}

if ($url) {
    Escribir-Ok "Servicio desplegado en: $url"
} else {
    Escribir-Aviso "El despliegue termino bien, pero no se pudo leer la URL del servicio."
}

Write-Host ""
Write-Host "Siguientes pasos:" -ForegroundColor White
Write-Host "  1. Dar acceso al equipo (rol de invocador o IAP): README, seccion 9."
Write-Host "  2. Abrirlo para probar, con tu propia identidad:"
Write-Host "     gcloud run services proxy $($vars['SERVICIO']) --region=$($vars['REGION']) --port=8080"
Write-Host "  3. Ver los registros:"
Write-Host "     gcloud run services logs read $($vars['SERVICIO']) --region=$($vars['REGION']) --limit=50"
Write-Host ""
Write-Host "El servicio es PRIVADO. No lo abras al publico: escribe en Drive y en produccion." -ForegroundColor Yellow
Write-Host ""
exit 0
