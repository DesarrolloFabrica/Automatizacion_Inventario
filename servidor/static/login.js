/* ==========================================================================
   Pantalla de entrada — lógica mínima
   Dos cosas: explicar por qué se llegó aquí y conservar el destino original
   para volver a él después de entrar.
   ========================================================================== */

(function () {
  'use strict';

  // Cada motivo se traduce a un texto que diga qué pasó y qué hacer ahora.
  var AVISOS = {
    sin_permiso: {
      clase: 'entrada__aviso--error',
      texto: function (correo) {
        return (correo ? 'La cuenta ' + correo + ' no tiene' : 'Esa cuenta no tiene') +
          ' acceso a esta herramienta. Pide que la habiliten, o entra con otra cuenta.';
      }
    },
    cerrada: {
      clase: 'entrada__aviso--neutro',
      texto: function () { return 'Cerraste la sesión.'; }
    },
    caducado: {
      clase: 'entrada__aviso--error',
      texto: function () {
        return 'El intento de entrada tardó demasiado. Vuelve a pulsar «Entrar con Google».';
      }
    },
    cancelado: {
      clase: 'entrada__aviso--neutro',
      texto: function () { return 'No se completó la entrada con Google.'; }
    },
    fallo: {
      clase: 'entrada__aviso--error',
      texto: function () {
        return 'No se pudo comprobar la cuenta con Google. Inténtalo de nuevo; ' +
          'si vuelve a fallar, avisa a quien administra el servicio.';
      }
    },
    sin_configurar: {
      clase: 'entrada__aviso--error',
      texto: function () {
        return 'El acceso con Google todavía no está configurado en este servicio. ' +
          'Avisa a quien administra el despliegue.';
      }
    }
  };

  var parametros = new URLSearchParams(window.location.search);

  var motivo = AVISOS[parametros.get('error')];
  if (motivo) {
    var aviso = document.getElementById('aviso');
    aviso.textContent = motivo.texto(parametros.get('correo') || '');
    aviso.classList.add(motivo.clase);
    aviso.hidden = false;
  }

  // Si se llegó aquí intentando abrir otra página, se vuelve allí al entrar.
  // Solo rutas internas: un destino absoluto permitiría usar esta pantalla
  // para mandar a la gente a cualquier sitio.
  var destino = parametros.get('destino') || '';
  if (destino.charAt(0) === '/' && destino.charAt(1) !== '/') {
    var boton = document.getElementById('boton-entrar');
    boton.href = '/api/sesion/iniciar?destino=' + encodeURIComponent(destino);
  }
})();
