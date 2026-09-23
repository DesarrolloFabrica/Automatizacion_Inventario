# =============================================================================
# Dockerfile — imagen de produccion del servicio web de la fabrica de contenido
#
# Que corre aqui: servidor/app.py (FastAPI) sirviendo la pagina estatica y la
# API, y lanzando las corridas del flujo (run_flujo.py / flujo_lib) en hilos de
# fondo dentro del mismo proceso.
#
# Construccion y despliegue: no hace falta hacer "docker build" a mano;
# "gcloud run deploy --source=." usa este Dockerfile a traves de Cloud Build.
# Ver despliegue/README.md.
# =============================================================================

# Python 3.12 y no 3.14: para 3.14 todavia no hay ruedas (wheels) publicadas de
# todo lo que usamos (psycopg2-binary, pandas, Pillow), y sin ruedas la imagen
# tendria que compilar desde codigo fuente, lo que alarga el build y obliga a
# meter compiladores en la imagen. La variante "slim" pesa poco y ya trae las
# autoridades certificadoras (ca-certificates), que es lo unico del sistema que
# necesitamos para hablar por HTTPS con Google y con Cloud SQL.
FROM python:3.12-slim

# --- Ajustes del interprete -------------------------------------------------
# PYTHONUNBUFFERED: los prints y los logs salen al instante, sin quedarse en el
#   buffer; es lo que hace que se vean en tiempo real en Cloud Logging.
# PYTHONDONTWRITEBYTECODE: no escribe .pyc dentro del contenedor (es de un solo
#   uso y el disco es memoria RAM).
# PIP_NO_CACHE_DIR / PIP_DISABLE_PIP_VERSION_CHECK: imagen mas pequena y build
#   mas rapido.
# PYTHONPATH=/app: permite importar "servidor.app" y "flujo_lib" desde cualquier
#   directorio de trabajo.
# PORT: valor por defecto para pruebas locales. En Cloud Run esta variable la
#   inyecta la plataforma y manda la suya.
# TZ: las fechas de las corridas y de los correos salen en hora de Colombia.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    PORT=8080 \
    TZ=America/Bogota

# --- Dependencias del sistema ----------------------------------------------
# A proposito NO se instala nada con apt-get:
#   * psycopg2-binary trae su propia libpq, asi que no hace falta libpq-dev.
#   * Pillow y pandas se instalan desde ruedas ya compiladas.
#   * python:3.12-slim ya incluye ca-certificates.
# Si algun dia hiciera falta (por ejemplo, para generar PDF), se anade aqui con
# el patron de siempre: apt-get update && apt-get install --no-install-recommends
# ... && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Dependencias de Python -------------------------------------------------
# Se copia primero SOLO el archivo de dependencias: mientras no cambie, Docker
# reutiliza esta capa y el despliegue de un cambio de codigo tarda segundos.
COPY requirements-servidor.txt ./
RUN python -m pip install --upgrade pip \
 && python -m pip install -r requirements-servidor.txt

# --- Codigo de la aplicacion ------------------------------------------------
# Solo lo que el servicio necesita en tiempo de ejecucion:
#   servidor/       aplicacion FastAPI + pagina estatica
#   flujo_lib/      libreria del run unico (toda la logica)
#   run_flujo.py    flujo completo por linea de comandos (lo reutiliza el servidor)
#   rutas_excel.py  localizador de RUTAS.xlsx que importan los scripts
# Y las dos carpetas heredadas, que NO son historia: flujo_lib importa codigo de
# ellas en caliente, con sys.path.insert, cada vez que se ejecuta un paso:
#   LMS_Fabrica/        -> flujo_lib/gcp.py         (cargar_base_gcp, generar_base_rutas)
#                          flujo_lib/verificacion.py (generar_base_rutas: parsear_ruta)
#                          flujo_lib/notificar.py    (notificar_carga_lms: consulta_sql)
#   CLONACION_CARPETA/  -> flujo_lib/inventario.py  (publicar_inventario_sheets,
#                                                    reporte_inventario_clon)
# Si faltaran, la corrida reventaria en mitad del proceso (ImportError), no al
# arrancar. CAMBIAR_FORMATO/ no se copia: flujo_lib/formato.py no lo importa,
# hace la conversion con Pillow por su cuenta.
COPY flujo_lib/ ./flujo_lib/
COPY servidor/ ./servidor/
COPY LMS_Fabrica/ ./LMS_Fabrica/
COPY CLONACION_CARPETA/ ./CLONACION_CARPETA/
COPY run_flujo.py rutas_excel.py ./

# --- Usuario sin privilegios ------------------------------------------------
# El servicio no necesita ser root: si alguien lograra ejecutar algo dentro del
# contenedor, no seria administrador de la maquina. El usuario tiene que poder
# escribir en /app porque el flujo deja ahi corridas/ (estado JSON, Excel de
# estado y logs) y, si se usa el token OAuth, la copia de token.json.
RUN useradd --create-home --uid 1000 fabrica \
 && mkdir -p /app/corridas/logs \
 && chown -R fabrica:fabrica /app
USER fabrica

EXPOSE 8080

# --- Arranque ---------------------------------------------------------------
# Se usa "sh -c" para expandir $PORT, que Cloud Run entrega como variable de
# entorno. El token no se copia al disco: servidor/configuracion.py lee
# GOOGLE_TOKEN_JSON directamente desde el volumen de solo lectura de Secret
# Manager y pasa esa ruta a flujo_lib/drive.py.
#
# Uvicorn corre con UN SOLO worker, a proposito. Las corridas viven en hilos dentro
#    del proceso y se coordinan con candados en memoria; con varios workers cada
#    proceso tendria su propio estado y sus propios candados, y dos peticiones
#    podrian lanzar la misma corrida a la vez o ver estados distintos. Por la
#    misma razon el servicio se despliega con --max-instances=1.
#    "exec" hace que uvicorn sea el proceso 1 y reciba las senales de parada de
#    Cloud Run (apagado limpio en vez de muerte a los 10 segundos).
CMD ["/bin/sh", "-c", "exec uvicorn servidor.app:app --host 0.0.0.0 --port \"${PORT:-8080}\" --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
