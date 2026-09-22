/* ==========================================================================
   Clonación e inventario — lógica de la página
   Sin dependencias: la red de la CUN bloquea los CDN, así que todo va aquí.
   ========================================================================== */

(function () {
  'use strict';

  /* ----------------------------------------------------------------------
     Tablas de traducción
     Se declaran una sola vez para que el nombre visible de un paso o de un
     estado no quede repartido por el código: si mañana cambia una palabra,
     se cambia en un único sitio.
     ---------------------------------------------------------------------- */

  // El orden de este arreglo es el orden en que se pintan los pasos.
  var PASOS = [
    { clave: 'destino', nombre: 'Carpeta destino' },
    { clave: 'clonacion', nombre: 'Clonación' },
    { clave: 'formato', nombre: 'Conversión a PNG' },
    { clave: 'verificacion', nombre: 'Verificación' },
    { clave: 'carga', nombre: 'Carga a la base' }
  ];

  // Iconos de texto, no imágenes: se ven igual sin conexión y se leen bien.
  var ESTADOS_PASO = {
    pendiente: { texto: 'Pendiente', icono: '·' },
    en_curso: { texto: 'En curso', icono: '\u00BB' },
    ok: { texto: 'OK', icono: '\u2713' },
    con_diferencias: { texto: 'Con diferencias', icono: '!' },
    fallido: { texto: 'Fallido', icono: '\u2715' },
    omitido: { texto: 'Omitido', icono: '\u2013' }
  };

  var ESTADOS_CORRIDA = {
    en_curso: { texto: 'En curso', clase: 'curso' },
    ok: { texto: 'Correcta', clase: 'ok' },
    con_pendientes: { texto: 'Con pendientes', clase: 'aviso' },
    fallido: { texto: 'Fallida', clase: 'error' },
    fallido_prevalidacion: { texto: 'No pudo empezar', clase: 'error' },
    cancelada: { texto: 'Cancelada', clase: 'neutro' }
  };

  var MS_SONDEO = 2000;
  // Espera creciente ante fallos de red: evita machacar un servidor caído.
  var ESPERAS_REINTENTO = [2000, 4000, 8000, 15000, 30000];
  var CLAVE_ALMACEN = 'inventario.ultimas_rutas';

  /* ----------------------------------------------------------------------
     Estado en memoria
     ---------------------------------------------------------------------- */

  var idCorrida = null;
  var temporizador = null;
  var fallosSeguidos = 0;
  var mensajesPintados = 0;
  var firmaPrevalidacion = null;
  var seguirAlFinal = true; // se desactiva si el usuario sube a leer la consola
  var confirmandoCancelacion = false;
  var relojConfirmacion = null;
  // Nodos ya creados por lote: se actualizan en sitio en vez de repintarse.
  // Repintar entero cada 2 s haría que el lector de pantalla releyese todo.
  var nodosPorLote = {};

  function $(id) { return document.getElementById(id); }

  /* ----------------------------------------------------------------------
     Capa de API
     Dos implementaciones con la misma forma: la real y la de demostración.
     El resto del código no sabe cuál está usando.
     ---------------------------------------------------------------------- */

  var parametros = new URLSearchParams(window.location.search);
  var MODO_DEMO = parametros.get('demo') === '1';

  function pedirJson(ruta, opciones) {
    return fetch(ruta, opciones).then(function (respuesta) {
      // El cuerpo se lee siempre: en los 400/409 es donde viene el motivo
      // escrito en lenguaje sencillo que hay que mostrar tal cual.
      return respuesta.text().then(function (crudo) {
        var cuerpo = null;
        try { cuerpo = crudo ? JSON.parse(crudo) : null; } catch (e) { cuerpo = null; }
        if (!respuesta.ok) {
          var error = new Error('respuesta ' + respuesta.status);
          error.codigo = respuesta.status;
          error.cuerpo = cuerpo;
          throw error;
        }
        return cuerpo;
      });
    });
  }

  var apiReal = {
    salud: function () {
      return pedirJson('/api/salud');
    },
    listarCorridas: function () {
      return pedirJson('/api/corridas');
    },
    crearCorrida: function (cuerpo) {
      return pedirJson('/api/corridas', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cuerpo)
      });
    },
    obtenerCorrida: function (id) {
      return pedirJson('/api/corridas/' + encodeURIComponent(id));
    },
    cancelar: function (id) {
      return pedirJson('/api/corridas/' + encodeURIComponent(id) + '/cancelar', { method: 'POST' });
    }
  };

  var api = MODO_DEMO ? construirApiDemo() : apiReal;

  /* ----------------------------------------------------------------------
     Validación de enlaces de Drive
     ---------------------------------------------------------------------- */

  // Devuelve el identificador si el texto parece una carpeta de Drive, o null.
  // Se aceptan las formas que la gente copia de verdad desde el navegador.
  function extraerIdCarpeta(valor) {
    var texto = (valor || '').trim();
    if (!texto) { return null; }

    // https://drive.google.com/drive/folders/ID, con /u/N/ opcional y
    // cualquier cola (?usp=sharing, /edit, #...).
    var enlace = texto.match(
      /^https?:\/\/drive\.google\.com\/(?:drive\/)?(?:u\/\d+\/)?folders\/([-\w]+)(?:[/?#][\s\S]*)?$/
    );
    if (enlace) { return enlace[1]; }

    // Forma antigua que todavía aparece en correos: /open?id=ID
    var abierto = texto.match(/^https?:\/\/drive\.google\.com\/open\?id=([-\w]+)(?:&[\s\S]*)?$/);
    if (abierto) { return abierto[1]; }

    // Identificador suelto. Se exige una longitud mínima para no aceptar
    // por error una palabra escrita a mano.
    if (/^[-\w]{15,}$/.test(texto)) { return texto; }

    return null;
  }

  function validarCampoCarpeta(entrada, cajaError) {
    var texto = entrada.value.trim();
    if (!texto) {
      marcarError(entrada, cajaError, 'Hace falta el enlace de la carpeta.');
      return false;
    }
    if (!extraerIdCarpeta(texto)) {
      marcarError(entrada, cajaError,
        'Esto no parece una carpeta de Drive. Copia el enlace desde la barra del navegador, ' +
        'con la forma https://drive.google.com/drive/folders/…');
      return false;
    }
    limpiarError(entrada, cajaError);
    return true;
  }

  function marcarError(entrada, cajaError, texto) {
    entrada.classList.add('campo__entrada--invalido');
    entrada.setAttribute('aria-invalid', 'true');
    cajaError.textContent = texto;
    cajaError.hidden = false;
  }

  function limpiarError(entrada, cajaError) {
    entrada.classList.remove('campo__entrada--invalido');
    entrada.removeAttribute('aria-invalid');
    cajaError.textContent = '';
    cajaError.hidden = true;
  }

  /* ----------------------------------------------------------------------
     Memoria local de las últimas rutas
     Va envuelto en try/catch porque en algunos equipos de la CUN el
     almacenamiento del navegador está restringido y lanzaría excepción.
     ---------------------------------------------------------------------- */

  function guardarRutas(origen, destino) {
    try {
      window.localStorage.setItem(CLAVE_ALMACEN, JSON.stringify({ origen: origen, destino: destino }));
    } catch (e) { /* sin memoria local se sigue trabajando igual */ }
  }

  function recuperarRutas() {
    try {
      var crudo = window.localStorage.getItem(CLAVE_ALMACEN);
      return crudo ? JSON.parse(crudo) : null;
    } catch (e) {
      return null;
    }
  }

  /* ----------------------------------------------------------------------
     Formato de fechas
     El servidor manda marcas de tiempo sin zona horaria; se parten a mano
     para que el navegador no las desplace según la zona del equipo.
     ---------------------------------------------------------------------- */

  function formatearMarca(iso) {
    if (!iso) { return ''; }
    var partes = String(iso).split('T');
    if (partes.length < 2) { return String(iso); }
    var fecha = partes[0].split('-');
    var hora = partes[1].slice(0, 5);
    if (fecha.length < 3) { return String(iso); }
    return fecha[2] + '/' + fecha[1] + '/' + fecha[0] + ' ' + hora;
  }

  function soloHora(iso) {
    if (!iso) { return ''; }
    var partes = String(iso).split('T');
    return partes.length > 1 ? partes[1].slice(0, 8) : '';
  }

  /* ----------------------------------------------------------------------
     Navegación entre las dos pantallas
     ---------------------------------------------------------------------- */

  function mostrarVista(cual) {
    $('vista-formulario').hidden = (cual !== 'formulario');
    $('vista-avance').hidden = (cual !== 'avance');
    window.scrollTo(0, 0);
  }

  /* ----------------------------------------------------------------------
     Estado de salud (cuenta y esquema)
     ---------------------------------------------------------------------- */

  function pintarSalud(datos) {
    var cuenta = $('salud-cuenta');
    var esquema = $('salud-esquema');

    cuenta.textContent = 'Cuenta: ' + (datos && datos.cuenta ? datos.cuenta : 'sin autorizar');
    if (!datos || datos.ok !== true) {
      cuenta.classList.add('salud__dato--peligro');
    } else {
      cuenta.classList.remove('salud__dato--peligro');
    }

    var nombreEsquema = (datos && datos.esquema) ? datos.esquema : 'desconocido';
    // Cualquier esquema que no sea el de pruebas significa que se está
    // escribiendo en producción: eso tiene que saltar a la vista.
    if (nombreEsquema === 'fabrica_pruebas') {
      esquema.textContent = 'Esquema: ' + nombreEsquema;
      esquema.classList.remove('salud__dato--peligro');
    } else {
      esquema.textContent = 'Esquema: ' + nombreEsquema + ' — atención: esto es producción';
      esquema.classList.add('salud__dato--peligro');
    }
  }

  function cargarSalud() {
    api.salud().then(pintarSalud).catch(function () {
      $('salud-cuenta').textContent = 'Cuenta: sin respuesta del servidor';
      $('salud-esquema').textContent = 'Esquema: sin respuesta del servidor';
      $('salud-esquema').classList.add('salud__dato--peligro');
    });
  }

  /* ----------------------------------------------------------------------
     Historial de corridas anteriores
     ---------------------------------------------------------------------- */

  function cargarHistorial() {
    api.listarCorridas().then(function (datos) {
      var corridas = (datos && datos.corridas) ? datos.corridas.slice(0, 5) : [];
      if (!corridas.length) { return; }

      var lista = $('historial');
      lista.textContent = '';

      corridas.forEach(function (corrida) {
        var fila = document.createElement('li');
        var boton = document.createElement('button');
        boton.type = 'button';
        boton.className = 'historial__fila';

        var etiqueta = document.createElement('span');
        etiqueta.className = 'historial__etiqueta';
        etiqueta.textContent = corrida.etiqueta || corrida.id;

        var fecha = document.createElement('span');
        fecha.className = 'historial__fecha';
        fecha.textContent = formatearMarca(corrida.inicio);

        var traduccion = ESTADOS_CORRIDA[corrida.estado] || { texto: corrida.estado, clase: 'neutro' };
        var distintivo = document.createElement('span');
        distintivo.className = 'distintivo distintivo--' + traduccion.clase;
        distintivo.textContent = traduccion.texto;

        boton.appendChild(etiqueta);
        boton.appendChild(fecha);
        boton.appendChild(distintivo);
        boton.addEventListener('click', function () { abrirCorrida(corrida.id); });

        fila.appendChild(boton);
        lista.appendChild(fila);
      });

      $('tarjeta-historial').hidden = false;
    }).catch(function () {
      // El historial es una comodidad: si no llega, la pantalla principal
      // tiene que seguir sirviendo para lanzar una corrida.
    });
  }

  /* ----------------------------------------------------------------------
     Envío del formulario
     ---------------------------------------------------------------------- */

  function manejarEnvio(evento) {
    evento.preventDefault();

    var entradaOrigen = $('campo-origen');
    var entradaDestino = $('campo-destino');

    var okOrigen = validarCampoCarpeta(entradaOrigen, $('error-origen'));
    var okDestino = validarCampoCarpeta(entradaDestino, $('error-destino'));

    var cajaEnvio = $('error-envio');
    cajaEnvio.hidden = true;

    if (!okOrigen || !okDestino) {
      // El foco va al primer campo con problema: quien navega con teclado
      // no tiene que buscar dónde está el error.
      (okOrigen ? entradaDestino : entradaOrigen).focus();
      return;
    }

    var etiqueta = $('campo-etiqueta').value.trim();
    var cliente = $('campo-cliente').value;

    var cuerpo = {
      origen: entradaOrigen.value.trim(),
      destino: entradaDestino.value.trim(),
      etiqueta: etiqueta ? etiqueta : null,
      cliente: cliente ? cliente : null,
      simular: $('campo-simular').checked,
      forzar_carga: $('campo-forzar').checked
    };

    guardarRutas(cuerpo.origen, cuerpo.destino);

    var boton = $('boton-ejecutar');
    boton.disabled = true;
    boton.textContent = 'Lanzando…';

    api.crearCorrida(cuerpo).then(function (respuesta) {
      boton.disabled = false;
      boton.textContent = 'Ejecutar';
      abrirCorrida(respuesta.corrida_id, cuerpo.etiqueta);
    }).catch(function (error) {
      boton.disabled = false;
      boton.textContent = 'Ejecutar';

      // Si el servidor explicó el problema, se muestra su texto tal cual:
      // ya viene redactado para quien no es técnico.
      var texto;
      if (error && error.cuerpo && error.cuerpo.motivo) {
        texto = error.cuerpo.motivo;
        if (error.cuerpo.accion) { texto += ' ' + error.cuerpo.accion; }
      } else {
        texto = 'No se pudo hablar con el servidor. Comprueba que el programa sigue abierto ' +
          'en tu equipo y vuelve a pulsar Ejecutar.';
      }
      cajaEnvio.textContent = texto;
      cajaEnvio.hidden = false;
      cajaEnvio.focus();
    });
  }

  /* ----------------------------------------------------------------------
     Apertura de una corrida y sondeo
     ---------------------------------------------------------------------- */

  function abrirCorrida(id, etiquetaProvisional) {
    detenerSondeo();

    idCorrida = id;
    mensajesPintados = 0;
    firmaPrevalidacion = null;
    fallosSeguidos = 0;
    seguirAlFinal = true;
    nodosPorLote = {};

    $('lotes').textContent = '';
    $('mensajes').textContent = '';
    $('prevalidacion').textContent = '';
    $('tarjeta-prevalidacion').hidden = true;
    $('tarjeta-resumen').hidden = true;
    $('aviso-conexion').hidden = true;
    $('boton-bajar').hidden = true;
    $('boton-nueva').hidden = true;
    $('boton-cancelar').hidden = false;
    restablecerBotonCancelar();

    $('avance-etiqueta').textContent = etiquetaProvisional || 'Corrida ' + id;
    $('avance-id').textContent = id;
    $('avance-inicio').textContent = '';
    ponerDistintivo('en_curso');

    mostrarVista('avance');
    sondear();
  }

  function detenerSondeo() {
    if (temporizador) {
      window.clearTimeout(temporizador);
      temporizador = null;
    }
  }

  function programarSondeo(ms) {
    detenerSondeo();
    temporizador = window.setTimeout(sondear, ms);
  }

  function sondear() {
    var idPedido = idCorrida;

    api.obtenerCorrida(idPedido).then(function (datos) {
      // Si mientras tanto se abrió otra corrida, esta respuesta ya no sirve.
      if (idPedido !== idCorrida) { return; }

      fallosSeguidos = 0;
      $('aviso-conexion').hidden = true;
      pintarCorrida(datos);

      if (datos.estado === 'en_curso') {
        programarSondeo(MS_SONDEO);
      } else {
        detenerSondeo();
        $('boton-cancelar').hidden = true;
        $('boton-nueva').hidden = false;
      }
    }).catch(function () {
      if (idPedido !== idCorrida) { return; }

      // No se borra nada de lo ya pintado: el usuario conserva el contexto
      // mientras la conexión vuelve.
      fallosSeguidos += 1;
      $('aviso-conexion').hidden = false;
      var indice = Math.min(fallosSeguidos - 1, ESPERAS_REINTENTO.length - 1);
      programarSondeo(ESPERAS_REINTENTO[indice]);
    });
  }

  /* ----------------------------------------------------------------------
     Pintado de la corrida
     ---------------------------------------------------------------------- */

  function pintarCorrida(datos) {
    if (!datos) { return; }

    var primerLote = (datos.lotes && datos.lotes.length) ? datos.lotes[0] : null;
    var titulo = (primerLote && primerLote.etiqueta) ? primerLote.etiqueta : ('Corrida ' + datos.id);
    if ($('avance-etiqueta').textContent !== titulo) {
      $('avance-etiqueta').textContent = titulo;
    }
    $('avance-id').textContent = datos.id;
    $('avance-inicio').textContent = 'Inicio ' + formatearMarca(datos.inicio) +
      (datos.fin ? ' · fin ' + soloHora(datos.fin) : '');

    ponerDistintivo(datos.estado);
    pintarPrevalidacion(datos.prevalidacion || []);
    pintarLotes(datos.lotes || []);
    pintarMensajes(datos.mensajes || []);
    pintarResumen(datos);
  }

  function ponerDistintivo(estado) {
    var traduccion = ESTADOS_CORRIDA[estado] || { texto: estado || 'Desconocido', clase: 'neutro' };
    var nodo = $('avance-distintivo');
    nodo.className = 'distintivo distintivo--' + traduccion.clase;
    nodo.textContent = traduccion.texto;
  }

  function pintarPrevalidacion(hallazgos) {
    // Solo interesan avisos y errores: la lista de comprobaciones correctas
    // es ruido para quien está mirando si su corrida arrancó bien.
    var relevantes = hallazgos.filter(function (h) { return h.nivel === 'aviso' || h.nivel === 'error'; });

    var firma = JSON.stringify(relevantes);
    if (firma === firmaPrevalidacion) { return; }
    firmaPrevalidacion = firma;

    var lista = $('prevalidacion');
    lista.textContent = '';

    if (!relevantes.length) {
      $('tarjeta-prevalidacion').hidden = true;
      return;
    }

    relevantes.forEach(function (hallazgo) {
      var elemento = document.createElement('li');
      elemento.className = 'hallazgo hallazgo--' + (hallazgo.nivel || 'aviso');

      var area = document.createElement('span');
      area.className = 'hallazgo__area';
      area.textContent = (hallazgo.nivel === 'error' ? 'Error' : 'Aviso') +
        (hallazgo.area ? ' · ' + hallazgo.area : '');

      var mensaje = document.createElement('p');
      mensaje.className = 'hallazgo__mensaje';
      mensaje.textContent = hallazgo.mensaje || '';

      elemento.appendChild(area);
      elemento.appendChild(mensaje);

      if (hallazgo.accion) {
        var accion = document.createElement('p');
        accion.className = 'hallazgo__accion';
        accion.textContent = 'Qué hacer: ' + hallazgo.accion;
        elemento.appendChild(accion);
      }

      lista.appendChild(elemento);
    });

    $('tarjeta-prevalidacion').hidden = false;
  }

  function pintarLotes(lotes) {
    lotes.forEach(function (lote, indice) {
      var clave = lote.clave || ('lote-' + indice);
      var nodos = nodosPorLote[clave];
      if (!nodos) {
        nodos = crearTarjetaLote(lote);
        nodosPorLote[clave] = nodos;
        $('lotes').appendChild(nodos.tarjeta);
      }
      actualizarTarjetaLote(nodos, lote);
    });
  }

  function crearTarjetaLote(lote) {
    var tarjeta = document.createElement('section');
    tarjeta.className = 'tarjeta';

    var cabecera = document.createElement('div');
    cabecera.className = 'lote__cabecera';

    var titulo = document.createElement('h3');
    titulo.className = 'lote__titulo';
    titulo.textContent = lote.etiqueta || lote.clave || 'Lote';

    var cliente = document.createElement('span');
    cliente.className = 'lote__cliente';

    var destino = document.createElement('a');
    destino.className = 'lote__destino';
    destino.target = '_blank';
    destino.rel = 'noopener';
    destino.hidden = true;

    cabecera.appendChild(titulo);
    cabecera.appendChild(cliente);
    cabecera.appendChild(destino);
    tarjeta.appendChild(cabecera);

    var lista = document.createElement('ul');
    lista.className = 'pasos';
    var pasos = {};

    PASOS.forEach(function (definicion) {
      var fila = document.createElement('li');
      fila.className = 'paso paso--pendiente';

      var icono = document.createElement('span');
      icono.className = 'paso__icono';
      icono.setAttribute('aria-hidden', 'true');

      var nombre = document.createElement('span');
      nombre.className = 'paso__nombre';
      nombre.textContent = definicion.nombre;

      var estado = document.createElement('span');
      estado.className = 'paso__estado';

      var cuerpo = document.createElement('div');
      cuerpo.className = 'paso__cuerpo';

      var detalle = document.createElement('p');
      detalle.className = 'paso__detalle';

      var barra = document.createElement('div');
      barra.className = 'paso__barra';
      barra.setAttribute('role', 'progressbar');
      barra.setAttribute('aria-valuemin', '0');
      barra.setAttribute('aria-valuemax', '100');
      barra.setAttribute('aria-label', 'Avance de ' + definicion.nombre);
      barra.hidden = true;

      var relleno = document.createElement('div');
      relleno.className = 'paso__barra-relleno';
      barra.appendChild(relleno);

      var progreso = document.createElement('p');
      progreso.className = 'paso__progreso';
      progreso.hidden = true;

      var contador = document.createElement('span');
      contador.className = 'paso__contador';

      var mensaje = document.createElement('span');
      mensaje.className = 'paso__mensaje';

      progreso.appendChild(contador);
      progreso.appendChild(mensaje);

      cuerpo.appendChild(detalle);
      cuerpo.appendChild(barra);
      cuerpo.appendChild(progreso);

      fila.appendChild(icono);
      fila.appendChild(nombre);
      fila.appendChild(estado);
      fila.appendChild(cuerpo);
      lista.appendChild(fila);

      pasos[definicion.clave] = {
        fila: fila, icono: icono, estado: estado, detalle: detalle,
        barra: barra, relleno: relleno, progreso: progreso,
        contador: contador, mensaje: mensaje
      };
    });

    tarjeta.appendChild(lista);

    return {
      tarjeta: tarjeta, titulo: titulo, cliente: cliente,
      destino: destino, pasos: pasos
    };
  }

  function actualizarTarjetaLote(nodos, lote) {
    ponerTexto(nodos.titulo, lote.etiqueta || lote.clave || 'Lote');
    ponerTexto(nodos.cliente, lote.cliente ? 'Cliente: ' + lote.cliente : '');

    if (lote.destino_enlace) {
      nodos.destino.href = lote.destino_enlace;
      ponerTexto(nodos.destino, 'Abrir ' + (lote.destino_nombre || 'la carpeta de destino') + ' en Drive');
      nodos.destino.hidden = false;
    } else {
      nodos.destino.hidden = true;
    }

    var pasos = lote.pasos || {};
    PASOS.forEach(function (definicion) {
      actualizarPaso(nodos.pasos[definicion.clave], pasos[definicion.clave]);
    });
  }

  function actualizarPaso(nodos, datos) {
    if (!nodos) { return; }

    var estado = (datos && datos.estado) ? datos.estado : 'pendiente';
    var traduccion = ESTADOS_PASO[estado] || ESTADOS_PASO.pendiente;

    var clase = 'paso paso--' + estado;
    if (nodos.fila.className !== clase) { nodos.fila.className = clase; }
    ponerTexto(nodos.icono, traduccion.icono);
    ponerTexto(nodos.estado, traduccion.texto);
    ponerTexto(nodos.detalle, (datos && datos.detalle) ? datos.detalle : '');

    var progreso = (datos && datos.progreso) ? datos.progreso : null;
    var total = progreso ? Number(progreso.total) || 0 : 0;

    // La barra solo aparece mientras el paso corre y se sabe cuántos
    // elementos hay: una barra sin total sería una promesa falsa.
    if (estado === 'en_curso' && total > 0) {
      var hechos = Number(progreso.hechos) || 0;
      var porcentaje = (typeof progreso.porcentaje === 'number')
        ? progreso.porcentaje
        : Math.round((hechos / total) * 100);
      porcentaje = Math.max(0, Math.min(100, porcentaje));

      nodos.barra.hidden = false;
      nodos.relleno.style.width = porcentaje + '%';
      nodos.barra.setAttribute('aria-valuenow', String(porcentaje));

      nodos.progreso.hidden = false;
      ponerTexto(nodos.contador, hechos + ' de ' + total);
      ponerTexto(nodos.mensaje, progreso.mensaje || '');
    } else {
      nodos.barra.hidden = true;
      // Se limpia el avance anterior para que un paso ya terminado no deje
      // una barra a medias si vuelve a mostrarse (por ejemplo con --rehacer).
      nodos.relleno.style.width = '0%';
      nodos.barra.removeAttribute('aria-valuenow');
      // Con el paso en curso pero sin total todavía, el mensaje del momento
      // sigue siendo útil (por ejemplo "buscando la carpeta").
      if (estado === 'en_curso' && progreso && progreso.mensaje) {
        nodos.progreso.hidden = false;
        ponerTexto(nodos.contador, '');
        ponerTexto(nodos.mensaje, progreso.mensaje);
      } else {
        nodos.progreso.hidden = true;
      }
    }
  }

  // Escribe solo si el texto cambió: así la región aria-live no repite
  // lo mismo en cada sondeo.
  function ponerTexto(nodo, texto) {
    var valor = texto == null ? '' : String(texto);
    if (nodo.textContent !== valor) { nodo.textContent = valor; }
  }

  /* ----------------------------------------------------------------------
     Consola de mensajes
     ---------------------------------------------------------------------- */

  function pintarMensajes(mensajes) {
    var consola = $('mensajes');

    // Si la lista encogió, el servidor reinició el historial: se repinta.
    if (mensajes.length < mensajesPintados) {
      consola.textContent = '';
      mensajesPintados = 0;
    }

    // Solo se añaden los nuevos: añadir es barato, repintar 500 líneas no.
    for (var i = mensajesPintados; i < mensajes.length; i += 1) {
      consola.appendChild(crearLinea(mensajes[i]));
    }
    mensajesPintados = mensajes.length;

    if (seguirAlFinal) {
      consola.scrollTop = consola.scrollHeight;
    }
  }

  function crearLinea(mensaje) {
    var linea = document.createElement('div');
    linea.className = 'linea linea--' + (mensaje.nivel || 'info');

    var hora = document.createElement('span');
    hora.className = 'linea__hora';
    hora.textContent = mensaje.hora || '';

    var texto = document.createElement('span');
    texto.className = 'linea__texto';
    texto.textContent = mensaje.texto || '';

    linea.appendChild(hora);
    linea.appendChild(texto);
    return linea;
  }

  function vigilarDesplazamiento() {
    var consola = $('mensajes');
    consola.addEventListener('scroll', function () {
      // Margen de 24 px: si el usuario está prácticamente al final se
      // considera que quiere seguir viendo lo último.
      var alFinal = consola.scrollHeight - consola.scrollTop - consola.clientHeight < 24;
      seguirAlFinal = alFinal;
      $('boton-bajar').hidden = alFinal;
    });

    $('boton-bajar').addEventListener('click', function () {
      seguirAlFinal = true;
      consola.scrollTop = consola.scrollHeight;
      $('boton-bajar').hidden = true;
    });
  }

  /* ----------------------------------------------------------------------
     Resumen final y enlaces
     ---------------------------------------------------------------------- */

  function pintarResumen(datos) {
    if (datos.estado === 'en_curso') {
      $('tarjeta-resumen').hidden = true;
      return;
    }

    var caja = $('resumen');
    caja.textContent = '';

    var motivo = document.createElement('p');
    motivo.className = 'resumen__motivo';
    // El texto del servidor se muestra tal cual: ya está escrito en
    // lenguaje sencillo, con el qué pasó y el qué hacer.
    motivo.textContent = (datos.resumen && datos.resumen.motivo)
      ? datos.resumen.motivo
      : 'La corrida terminó.';
    caja.appendChild(motivo);

    if (datos.resumen && datos.resumen.accion) {
      var accion = document.createElement('p');
      accion.className = 'resumen__accion';
      accion.textContent = 'Qué hacer: ' + datos.resumen.accion;
      caja.appendChild(accion);
    }

    var lista = $('enlaces');
    lista.textContent = '';

    (datos.lotes || []).forEach(function (lote) {
      if (lote.destino_enlace) {
        agregarEnlace(lista, 'Carpeta de destino en Drive' +
          (lote.destino_nombre ? ' (' + lote.destino_nombre + ')' : ''), lote.destino_enlace);
      }
    });

    var enlaces = datos.enlaces || {};
    if (enlaces.sheet) { agregarEnlace(lista, 'Hoja de inventario en Google Sheets', enlaces.sheet); }
    if (enlaces.estado_xlsx) { agregarEnlace(lista, 'Excel de estado de la corrida', enlaces.estado_xlsx); }

    $('tarjeta-resumen').hidden = false;
  }

  function agregarEnlace(lista, texto, destino) {
    var elemento = document.createElement('li');

    // El Excel de estado puede llegar como ruta del disco y no como URL;
    // en ese caso se muestra la ruta para copiarla, no un enlace roto.
    if (/^https?:\/\//.test(destino)) {
      var enlace = document.createElement('a');
      enlace.href = destino;
      enlace.target = '_blank';
      enlace.rel = 'noopener';
      enlace.textContent = texto;
      elemento.appendChild(enlace);
    } else {
      elemento.textContent = texto + ': ' + destino;
    }

    lista.appendChild(elemento);
  }

  /* ----------------------------------------------------------------------
     Cancelación
     Confirmación en dos pulsaciones en vez de un diálogo del navegador:
     un modal del sistema es fácil de aceptar sin leerlo.
     ---------------------------------------------------------------------- */

  function restablecerBotonCancelar() {
    confirmandoCancelacion = false;
    if (relojConfirmacion) { window.clearTimeout(relojConfirmacion); relojConfirmacion = null; }
    $('boton-cancelar').textContent = 'Cancelar';
  }

  function manejarCancelacion() {
    var boton = $('boton-cancelar');

    if (!confirmandoCancelacion) {
      confirmandoCancelacion = true;
      boton.textContent = 'Pulsa otra vez para cancelar';
      relojConfirmacion = window.setTimeout(restablecerBotonCancelar, 5000);
      return;
    }

    restablecerBotonCancelar();
    boton.disabled = true;
    api.cancelar(idCorrida).then(function () {
      boton.disabled = false;
      // El estado real llega en el siguiente sondeo; no se adelanta nada.
      sondear();
    }).catch(function () {
      boton.disabled = false;
      $('aviso-conexion').hidden = false;
    });
  }

  /* ----------------------------------------------------------------------
     Arranque
     ---------------------------------------------------------------------- */

  function iniciar() {
    $('formulario').addEventListener('submit', manejarEnvio);

    // Se revalida al salir del campo, no mientras se escribe: marcar en rojo
    // un enlace a medio pegar sería molesto.
    $('campo-origen').addEventListener('blur', function () {
      if (this.value.trim()) { validarCampoCarpeta(this, $('error-origen')); }
    });
    $('campo-destino').addEventListener('blur', function () {
      if (this.value.trim()) { validarCampoCarpeta(this, $('error-destino')); }
    });
    $('campo-origen').addEventListener('input', function () {
      limpiarError(this, $('error-origen'));
    });
    $('campo-destino').addEventListener('input', function () {
      limpiarError(this, $('error-destino'));
    });

    $('boton-nueva').addEventListener('click', function () {
      detenerSondeo();
      idCorrida = null;
      mostrarVista('formulario');
      cargarHistorial();
      $('campo-origen').focus();
    });

    $('boton-cancelar').addEventListener('click', manejarCancelacion);

    vigilarDesplazamiento();

    var rutas = recuperarRutas();
    if (rutas) {
      if (rutas.origen) { $('campo-origen').value = rutas.origen; }
      if (rutas.destino) { $('campo-destino').value = rutas.destino; }
    }

    if (MODO_DEMO) {
      $('pie-modo').textContent = 'Modo demostración: los datos son inventados y no se toca Drive ' +
        'ni la base de datos. Quita ?demo=1 de la dirección para usarlo de verdad.';
    }

    cargarSalud();
    cargarHistorial();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', iniciar);
  } else {
    iniciar();
  }

  /* ======================================================================
     MODO DEMOSTRACIÓN
     Sirve para revisar la interfaz sin backend. El estado se calcula a
     partir de los segundos transcurridos, así que responde bien a
     cualquier ritmo de sondeo y a recargar la página a media corrida.

     Variantes por dirección:
       ?demo=1                      corrida normal, termina con pendientes
       ?demo=1&fallo=1              la clonación falla a mitad
       ?demo=1&prevalidacion=error  no arranca por prevalidación
       ?demo=1&esquema=fabrica      simula estar apuntando a producción
     ====================================================================== */

  function construirApiDemo() {
    var arranque = null;
    var idActual = null;
    var cancelada = false;
    var variante = parametros.get('fallo') === '1' ? 'fallo'
      : (parametros.get('prevalidacion') === 'error' ? 'prevalidacion' : 'normal');

    // Hitos de la línea de tiempo, en segundos desde el arranque.
    var T = {
      destino: [0, 3],
      clonacion: [3, 16],
      formato: [16, 24],
      verificacion: [24, 30],
      carga: [30, 36]
    };
    var TOTAL_ARCHIVOS = 375;
    var TOTAL_IMAGENES = 48;
    var FALLO_EN = 9; // segundo en que revienta la clonación con ?fallo=1

    var GUION = [
      { s: 0, n: 'info', t: 'Prevalidación: 6 comprobaciones, 1 aviso.' },
      { s: 1, n: 'info', t: 'Buscando la carpeta de destino dentro de la carpeta indicada.' },
      { s: 3, n: 'info', t: 'Carpeta de destino lista: PSICOLOGIA_UNIDAD_3.' },
      { s: 4, n: 'info', t: 'Clonación: 375 archivos por copiar, 0 ya existentes.' },
      { s: 6, n: 'info', t: 'Copiando CONTENIDOS/unidad_3/pieza_014.jpg' },
      { s: 9, n: 'info', t: 'Copiando GUION GRAFICO/guion_unidad_3.pdf' },
      { s: 12, n: 'aviso', t: 'SCORM/paquete_2.zip ya estaba en el destino, no se vuelve a copiar.' },
      { s: 16, n: 'info', t: 'Clonación terminada: 375 archivos copiados.' },
      { s: 17, n: 'info', t: 'Conversión a PNG: 48 imágenes JPG encontradas en la copia.' },
      { s: 20, n: 'info', t: 'Convirtiendo PORTADA MATERIA/portada_psicologia.jpg' },
      { s: 24, n: 'info', t: 'Conversión terminada: 48 imágenes pasadas a PNG.' },
      { s: 25, n: 'info', t: 'Verificación: comparando la carpeta de origen con la copia.' },
      { s: 29, n: 'aviso', t: 'Verificación: 9 archivos de ACTIVIDADES MOODLE no son .txt.' },
      { s: 30, n: 'info', t: 'Carga a la base: modo prueba, no se escribe nada.' },
      { s: 36, n: 'aviso', t: 'La corrida terminó con pendientes por revisar.' }
    ];

    var GUION_FALLO = [
      { s: 0, n: 'info', t: 'Prevalidación: 6 comprobaciones, 1 aviso.' },
      { s: 3, n: 'info', t: 'Carpeta de destino lista: PSICOLOGIA_UNIDAD_3.' },
      { s: 4, n: 'info', t: 'Clonación: 375 archivos por copiar.' },
      { s: 6, n: 'info', t: 'Copiando CONTENIDOS/unidad_3/pieza_014.jpg' },
      { s: FALLO_EN, n: 'error', t: 'Google dejó de responder al copiar CONTENIDOS/unidad_3/pieza_021.jpg.' }
    ];

    function responder(valor, retraso) {
      // Un pequeño retraso imita la latencia real y deja ver los estados
      // intermedios de la interfaz (botón deshabilitado, etc.).
      return new Promise(function (resolver) {
        window.setTimeout(function () { resolver(valor); }, retraso == null ? 150 : retraso);
      });
    }

    function segundos() {
      if (arranque == null) { return 0; }
      return (Date.now() - arranque) / 1000;
    }

    function marcaIso(desfaseSeg) {
      var fecha = new Date((arranque || Date.now()) + (desfaseSeg || 0) * 1000);
      function dos(n) { return (n < 10 ? '0' : '') + n; }
      return fecha.getFullYear() + '-' + dos(fecha.getMonth() + 1) + '-' + dos(fecha.getDate()) +
        'T' + dos(fecha.getHours()) + ':' + dos(fecha.getMinutes()) + ':' + dos(fecha.getSeconds());
    }

    function horaDe(desfaseSeg) {
      return marcaIso(desfaseSeg).split('T')[1];
    }

    // Construye un paso a partir de su ventana de tiempo.
    function paso(seg, ventana, total, estadoFinal, detalleFinal, plantillaMensaje) {
      var inicio = ventana[0];
      var fin = ventana[1];

      if (seg < inicio) {
        return { estado: 'pendiente', detalle: '', progreso: { hechos: 0, total: 0, mensaje: '', porcentaje: 0 } };
      }
      if (seg >= fin) {
        return {
          estado: estadoFinal, detalle: detalleFinal,
          progreso: { hechos: total, total: total, mensaje: '', porcentaje: 100 }
        };
      }

      var avance = (seg - inicio) / (fin - inicio);
      var hechos = Math.floor(avance * total);
      return {
        estado: 'en_curso',
        detalle: '',
        progreso: {
          hechos: hechos,
          total: total,
          mensaje: plantillaMensaje ? plantillaMensaje(hechos) : '',
          porcentaje: Math.round(avance * 100)
        }
      };
    }

    var NOMBRES_DEMO = [
      'CONTENIDOS/unidad_3/pieza_014.jpg',
      'GUION GRAFICO/guion_unidad_3.pdf',
      'ACTIVIDADES MOODLE/actividad_02.txt',
      'PODCAST/episodio_04.mp3',
      'REVISTA/revista_unidad_3.pdf',
      'SCORM/paquete_2.zip'
    ];

    function nombreArchivo(hechos) {
      return 'copiando ' + NOMBRES_DEMO[hechos % NOMBRES_DEMO.length];
    }

    function nombreImagen(hechos) {
      return 'convirtiendo pieza_' + (hechos + 1) + '.jpg';
    }

    function prevalidacionDemo() {
      if (variante === 'prevalidacion') {
        return [
          { nivel: 'ok', area: 'Token', mensaje: 'El acceso a Google está vigente.', accion: '' },
          {
            nivel: 'error', area: 'Carpeta de origen',
            mensaje: 'La cuenta de la fábrica de contenidos no puede abrir la carpeta de origen.',
            accion: 'Pide al dueño de la carpeta que la comparta con la cuenta que aparece arriba y vuelve a intentarlo.'
          },
          {
            nivel: 'error', area: 'Base de datos',
            mensaje: 'No hubo respuesta de la base de datos.',
            accion: 'Comprueba que estás conectado a la red de la CUN o a la VPN.'
          }
        ];
      }
      return [
        { nivel: 'ok', area: 'Token', mensaje: 'El acceso a Google está vigente.', accion: '' },
        { nivel: 'ok', area: 'Carpeta de origen', mensaje: 'La carpeta de origen se puede leer.', accion: '' },
        {
          nivel: 'aviso', area: 'Cliente',
          mensaje: 'El cliente se dedujo de Drive como PRODUCTO porque no se indicó ninguno.',
          accion: 'Si no es correcto, cancela la corrida y elige el cliente a mano en el formulario.'
        }
      ];
    }

    function estadoDemo(id) {
      // Una corrida del historial (otro id) se muestra ya terminada.
      var seg = (id === idActual) ? segundos() : 999;

      if (variante === 'prevalidacion') {
        return {
          id: id, estado: 'fallido_prevalidacion',
          inicio: marcaIso(0), fin: marcaIso(1),
          prevalidacion: prevalidacionDemo(),
          lotes: [lotePlano(id)],
          mensajes: [
            { hora: horaDe(0), nivel: 'error', texto: 'La corrida no pudo empezar: hay 2 problemas que resolver antes.' }
          ],
          resumen: {
            motivo: 'La corrida no llegó a empezar porque faltan permisos sobre la carpeta de origen y no hubo respuesta de la base de datos.',
            accion: 'Resuelve los dos puntos de arriba y vuelve a pulsar Ejecutar. No se copió ni se borró nada.'
          },
          enlaces: { sheet: null, estado_xlsx: null }
        };
      }

      if (cancelada) {
        return armar(id, seg, 'cancelada', {
          motivo: 'La corrida se detuvo porque alguien pulsó Cancelar.',
          accion: 'Lo que ya se copió sigue en la carpeta de destino. Puedes volver a lanzarla y continuará donde quedó.'
        });
      }

      if (variante === 'fallo' && seg >= FALLO_EN) {
        return armar(id, seg, 'fallido', {
          motivo: 'La copia se interrumpió porque Google dejó de responder a mitad de la clonación.',
          accion: 'Espera unos minutos y vuelve a lanzar la misma corrida: continuará desde donde se quedó, sin duplicar archivos.'
        });
      }

      if (seg >= T.carga[1]) {
        return armar(id, seg, 'con_pendientes', {
          motivo: 'Se copió y se convirtió todo, pero la verificación encontró 9 archivos mal ubicados.',
          accion: 'Revisa la carpeta ACTIVIDADES MOODLE en la copia: debe contener solo archivos .txt. Corrígelo y vuelve a lanzar la corrida.'
        });
      }

      return armar(id, seg, 'en_curso', null);
    }

    function lotePlano(id) {
      return {
        clave: 'lote-1', etiqueta: 'Psicología · unidad 3', cliente: 'PRODUCTO',
        destino_nombre: null, destino_enlace: null,
        pasos: {
          destino: vacio(), clonacion: vacio(), formato: vacio(),
          verificacion: vacio(), carga: vacio()
        }
      };
    }

    function vacio() {
      return { estado: 'pendiente', detalle: '', progreso: { hechos: 0, total: 0, mensaje: '', porcentaje: 0 } };
    }

    function armar(id, seg, estadoGeneral, resumen) {
      var hayFallo = (variante === 'fallo' && seg >= FALLO_EN) || (cancelada && seg < T.carga[1]);

      var pasos = {
        destino: paso(seg, T.destino, 1, 'ok', 'Se creó la carpeta PSICOLOGIA_UNIDAD_3 dentro del destino.'),
        clonacion: paso(seg, T.clonacion, TOTAL_ARCHIVOS, 'ok', '375 archivos copiados, 0 duplicados.', nombreArchivo),
        formato: paso(seg, T.formato, TOTAL_IMAGENES, 'ok', '48 imágenes pasadas de JPG a PNG.', nombreImagen),
        verificacion: paso(seg, T.verificacion, TOTAL_ARCHIVOS, 'con_diferencias',
          '9 archivos de ACTIVIDADES MOODLE no son .txt.'),
        carga: paso(seg, T.carga, 366, 'ok', 'Modo prueba: no se escribió en la base de datos.')
      };

      if (hayFallo) {
        // Al fallar, el paso en curso queda en rojo y lo que venía detrás
        // se marca como omitido en vez de quedarse "pendiente" para siempre.
        var orden = ['destino', 'clonacion', 'formato', 'verificacion', 'carga'];
        var yaFallo = false;
        orden.forEach(function (clave) {
          if (yaFallo) {
            pasos[clave] = { estado: 'omitido', detalle: 'No se llegó a ejecutar.', progreso: vacio().progreso };
            return;
          }
          if (pasos[clave].estado === 'en_curso') {
            yaFallo = true;
            pasos[clave] = {
              estado: cancelada ? 'omitido' : 'fallido',
              detalle: cancelada ? 'Detenido por el usuario.' : 'Google dejó de responder.',
              progreso: pasos[clave].progreso
            };
          }
        });
      }

      var guion = (variante === 'fallo') ? GUION_FALLO : GUION;
      var mensajes = guion.filter(function (linea) { return linea.s <= seg; })
        .map(function (linea) {
          return { hora: horaDe(linea.s), nivel: linea.n, texto: linea.t };
        });

      if (cancelada) {
        mensajes.push({ hora: horaDe(seg), nivel: 'aviso', texto: 'La corrida se detuvo a petición del usuario.' });
      }

      var terminada = estadoGeneral !== 'en_curso';

      return {
        id: id,
        estado: estadoGeneral,
        inicio: marcaIso(0),
        fin: terminada ? marcaIso(Math.min(seg, T.carga[1])) : null,
        prevalidacion: prevalidacionDemo(),
        lotes: [{
          clave: 'lote-1',
          etiqueta: 'Psicología · unidad 3',
          cliente: 'PRODUCTO',
          destino_nombre: seg >= T.destino[1] ? 'PSICOLOGIA_UNIDAD_3' : null,
          destino_enlace: seg >= T.destino[1] ? 'https://drive.google.com/drive/folders/1DEMOdestino000000000' : null,
          pasos: pasos
        }],
        mensajes: mensajes,
        resumen: resumen,
        enlaces: {
          sheet: (terminada && !hayFallo) ? 'https://docs.google.com/spreadsheets/d/1DEMOinventario0000000/edit' : null,
          estado_xlsx: terminada ? 'C:\\Dev\\...\\corridas\\RUTAS.estado.xlsx' : null
        }
      };
    }

    return {
      salud: function () {
        return responder({
          ok: true,
          cuenta: 'fabrica.contenidos@cun.edu.co',
          esquema: parametros.get('esquema') || 'fabrica_pruebas'
        });
      },

      listarCorridas: function () {
        return responder({
          corridas: [
            { id: '20260922_101500', inicio: '2026-09-22T10:15:00', fin: '2026-09-22T10:41:00', estado: 'ok', etiqueta: 'Contabilidad · unidad 1' },
            { id: '20260921_173000', inicio: '2026-09-21T17:30:00', fin: '2026-09-21T17:52:00', estado: 'con_pendientes', etiqueta: 'Derecho · unidad 4' },
            { id: '20260921_090000', inicio: '2026-09-21T09:00:00', fin: '2026-09-21T09:04:00', estado: 'fallido', etiqueta: 'Enfermería · unidad 2' },
            { id: '20260920_154500', inicio: '2026-09-20T15:45:00', fin: '2026-09-20T16:20:00', estado: 'ok', etiqueta: 'Administración · unidad 7' },
            { id: '20260920_080000', inicio: '2026-09-20T08:00:00', fin: null, estado: 'cancelada', etiqueta: 'Prueba interna' }
          ]
        });
      },

      crearCorrida: function (cuerpo) {
        // Atajo para revisar cómo se ve un rechazo del servidor sin backend:
        // basta con escribir "sinacceso" en cualquiera de los dos enlaces.
        var texto = (cuerpo.origen || '') + ' ' + (cuerpo.destino || '');
        if (texto.indexOf('sinacceso') !== -1) {
          return responder(null).then(function () {
            var error = new Error('demo 409');
            error.codigo = 409;
            error.cuerpo = {
              motivo: 'La cuenta de la fábrica de contenidos no puede abrir esa carpeta de Drive.',
              accion: 'Pide al dueño de la carpeta que la comparta con esa cuenta y vuelve a intentarlo.'
            };
            throw error;
          });
        }

        arranque = Date.now();
        cancelada = false;
        idActual = marcaIso(0).replace(/[-:]/g, '').replace('T', '_').slice(0, 15);
        return responder({ corrida_id: idActual }, 400);
      },

      obtenerCorrida: function (id) {
        return responder(estadoDemo(id));
      },

      cancelar: function () {
        cancelada = true;
        return responder({});
      }
    };
  }

}());
