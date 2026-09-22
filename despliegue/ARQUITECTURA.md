# Arquitectura del servicio web de la fábrica de contenido

Documento corto para entender **cómo encaja cada pieza** y, sobre todo, **qué
limitaciones tiene esta primera versión**. Los pasos para desplegarlo están en
[`README.md`](README.md).

---

## 1. La idea en dos frases

El flujo de la fábrica de contenido ya existía como un comando
(`python run_flujo.py --excel RUTAS.xlsx`). Lo que se ha añadido es una **capa
web delgada** por encima: una página donde el equipo sube su Excel y lanza la
corrida, y una API que arranca ese mismo flujo **en un hilo de fondo** dentro
del proceso del servidor.

La lógica no cambió: sigue siendo `flujo_lib/` y `run_flujo.py`. La web es la
puerta de entrada, no un motor nuevo.

---

## 2. Diagrama

```text
  PERSONA DEL EQUIPO (cuenta @cun.edu.co)
        |
        | HTTPS  (nadie entra sin identificarse:
        |         rol de invocador o Identity-Aware Proxy)
        v
  +=========================================================================+
  |  CLOUD RUN — servicio "fabrica-contenido-web"                           |
  |  1 instancia fija (min=1, max=1) · CPU siempre asignada · 1 worker      |
  |                                                                         |
  |   +-------------------------+        +------------------------------+   |
  |   |  FastAPI (uvicorn)      |        |  HILOS DE FONDO              |   |
  |   |  - servidor/static/     | lanza  |  un hilo por corrida         |   |
  |   |    (la pagina)          |------->|                              |   |
  |   |  - API: lanzar corrida, |        |  run_flujo / flujo_lib:      |   |
  |   |    consultar estado     |<-------|   prevalidar                 |   |
  |   |                         | estado |   destino automatico         |   |
  |   +-------------------------+        |   clonar                     |   |
  |             ^                        |   convertir JPG -> PNG       |   |
  |             |                        |   verificar                  |   |
  |    estado en memoria                 |   cargar a la base           |   |
  |    + candados por lote               |   inventario + correo        |   |
  |                                      +------------------------------+   |
  +=========================================================================+
        |                  |                      |                 |
        | estado y logs     | Drive / Sheets       | SQL             | Gmail
        v                  v                      v                 v
  +--------------+   +---------------+   +------------------+   +-----------+
  | CLOUD STORAGE|   | GOOGLE DRIVE  |   | CLOUD SQL        |   | GMAIL API |
  | bucket de    |   | carpetas de   |   | planner-postgres |   | correo    |
  | estado:      |   | origen y      |   | base planner_db  |   | final de  |
  |  .estado.json|   | destino +     |   | esquemas:        |   | la corrida|
  |  .estado.xlsx|   | hoja de       |   |  fabrica_pruebas |   |           |
  |  logs/       |   | inventario    |   |  fabrica (PROD)  |   |           |
  +--------------+   +---------------+   +------------------+   +-----------+
                             ^                     ^
                             |                     |
                    credenciales de          socket Unix del conector
                    Google (Secret           /cloudsql/<conexion>
                    Manager: token OAuth     (sin IP autorizadas)
                    o cuenta de servicio
                    delegada)
```

---

## 3. Qué hace cada pieza

| Pieza | Papel | Notas |
|---|---|---|
| **Navegador** | Sube el `RUTAS.xlsx`, lanza la corrida y consulta cómo va. | Nunca habla con Drive ni con la base: solo con el servicio. |
| **FastAPI + uvicorn** | Sirve `servidor/static/` y la API. Responde al instante y deja el trabajo pesado en un hilo. | **Un solo worker**, a propósito (ver limitaciones). |
| **Hilos de fondo** | Ejecutan la corrida completa: prevalidación, carpeta destino, clonación, conversión, verificación, carga y correo. | Es el mismo código que el comando de escritorio. |
| **Cloud Storage** | Guarda `<excel>.estado.json`, `<excel>.estado.xlsx` y `logs/`. | Lo escribe `flujo_lib/almacen.py` (`AlmacenGCS`); es lo que hace que el historial sobreviva a un reinicio del contenedor. |
| **Google Drive / Sheets** | Carpetas de origen y destino, y la hoja del inventario. | El flujo **nunca escribe dentro del origen**. |
| **Cloud SQL** | `planner_db`, una transacción por lote. | Por defecto se carga a `fabrica_pruebas`; a `fabrica` solo si se pide. |
| **Gmail API** | Un único correo final por corrida (y uno de fallo). | Se envía desde `fabricadecontenidos@cun.edu.co`. |
| **Secret Manager** | Contraseña de la base y credenciales de Google. | Nada de esto vive dentro de la imagen. |

---

## 4. Ciclo de vida de una corrida

```text
1. La persona sube RUTAS.xlsx y pulsa "lanzar".
2. La API valida, crea la corrida, arranca un hilo y responde ya
   ("en curso", con un identificador). El navegador no se queda esperando.
3. El hilo ejecuta paso a paso, escribiendo el estado por lote tras cada paso.
4. El navegador pregunta cada pocos segundos cómo va y pinta la tabla.
5. Al terminar: estado y Excel al bucket, correo final al equipo.
```

El **estado por lote** (clave `origen_id|destino_raiz_id`) es la pieza que hace
todo esto reanudable: un lote con la clonación en `ok` se salta en la siguiente
corrida. Por eso, relanzar una corrida interrumpida no repite lo ya hecho.

---

## 5. Decisiones y por qué

- **Una sola instancia, un solo worker.** Las corridas viven en hilos dentro del
  proceso y se coordinan con candados en memoria (para que dos personas no
  lancen a la vez la misma carpeta). Con varios workers o varias instancias,
  cada proceso tendría su propio estado y sus propios candados: los candados
  dejarían de servir y dos corridas podrían pisarse en Drive.
- **CPU siempre asignada.** El trabajo ocurre *después* de responder a la
  petición. Cloud Run, por defecto, le quita la CPU al contenedor cuando no hay
  peticiones en curso; eso congelaría la clonación a media faena.
- **Estado en el bucket, no en el disco.** El disco del contenedor es memoria
  RAM y se borra en cada reinicio.
- **Cloud SQL por el conector integrado.** Cloud Run no tiene IP fija, así que
  la lista de IP autorizadas nunca sería una opción viable.
- **El servicio es privado.** Escribe en Drive y en la base de producción.
- **Modo simulación encendido por defecto.** Escribir de verdad tiene que ser
  una decisión consciente de quien lanza la corrida.

---

## 6. Limitaciones conocidas de esta primera versión

Están aquí para que nadie se lleve una sorpresa, y para saber por dónde
evolucionar.

1. **Un solo proceso y una sola instancia.** No escala horizontalmente: si hace
   falta atender más carga, no basta con subir el número de instancias, habría
   que sacar el trabajo fuera del proceso web (ver punto 5 de abajo).

2. **Las corridas viven en memoria mientras se ejecutan.** El progreso "en
   vivo", la cola y los candados están en la memoria del proceso. Solo lo que se
   va guardando (estado JSON, Excel de estado y logs) sobrevive.

3. **Si el servicio se reinicia a mitad de una corrida, esa corrida queda
   interrumpida.** Pasa al desplegar una versión nueva, al cambiar una variable
   de entorno, o si la plataforma mueve la instancia por mantenimiento. No se
   reanuda sola: **hay que relanzarla a mano**. La buena noticia es que el
   estado por lote evita rehacer lo ya completado. Por eso conviene comprobar
   que no haya nada en marcha antes de desplegar.

4. **Tiempo de vida limitado por la petición y por la instancia.** La corrida
   sigue en el fondo aunque el navegador se cierre, pero no hay garantía de que
   una corrida de varias horas termine si la plataforma decide reciclar la
   instancia.

5. **Las credenciales de Google son el punto frágil.** Con la opción de token
   OAuth (`token.json`), la credencial es de usuario: caduca y puede ser
   revocada, y entonces el servicio deja de poder tocar Drive hasta que alguien
   suba una versión nueva del secreto. La cuenta de servicio con delegación de
   dominio resuelve esto, pero depende del administrador de Google Workspace de
   la CUN.

6. **No hay cola de trabajos ni reintentos automáticos.** Si un paso falla, el
   estado lo registra con "qué pasó" y "qué hacer", pero volver a lanzar es
   decisión de una persona.

### La evolución natural

Mover el trabajo pesado a **Cloud Run Jobs**: la web se quedaría solo con
recibir la petición, encolarla y mostrar el estado, y cada corrida se ejecutaría
como un *job* independiente con su propio tiempo de vida, sus reintentos y su
registro. Eso resolvería de golpe los puntos 1, 2, 3, 4 y 6: un despliegue
dejaría de interrumpir corridas, se podrían ejecutar varias a la vez y el
progreso viviría fuera del proceso web (en el bucket o en la propia base).

El camino no obliga a reescribir la lógica: `run_flujo.py` ya es un comando que
recibe un Excel y unas opciones, que es exactamente lo que un Cloud Run Job
necesita ejecutar.
