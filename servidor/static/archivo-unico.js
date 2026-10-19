/* Formulario independiente para reemplazar un archivo. */
(function () {
  'use strict';
  window.ArchivoUnico = { iniciar: function (opciones) {
    function $(id) { return document.getElementById(id); }
  document.getElementById('formulario-archivo-unico').addEventListener('submit', async function (evento) {
    evento.preventDefault();
    var formulario = evento.currentTarget;
    var salida = $('unico-resultado');
    var simular = !evento.submitter || evento.submitter.value !== 'reemplazar';
    if (opciones.demo) {
      salida.textContent = 'El reemplazo de archivo único requiere una sesión real; no está disponible en la demostración.';
      return;
    }
    var botones = formulario.querySelectorAll('button');
    botones.forEach(function (b) { b.disabled = true; });
    salida.textContent = simular ? 'Validando archivo y destino…' : 'Reemplazando el archivo; espera el resultado…';
    try {
      var resultado = await opciones.pedirJson('/api/archivo-unico', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          origen: $('unico-origen').value, nombre: $('unico-nombre').value,
          destino: $('unico-destino').value, nombre_destino: $('unico-nombre-destino').value,
          simular: simular
        })
      });
      salida.textContent = resultado.simulado
        ? 'Validación correcta: se encontró «' + resultado.nombre + '». No se modificó ningún archivo.'
        : 'Se reemplazó únicamente «' + resultado.nombre + '». Se conservó su enlace en destino.';
    } catch (error) {
      salida.textContent = error.cuerpo
        ? (error.cuerpo.motivo || 'No se pudo completar el reemplazo.') + ' ' + (error.cuerpo.accion || '')
        : 'No se recibió el resultado. Valida el destino antes de volver a intentar.';
    } finally {
      botones.forEach(function (b) { b.disabled = false; });
    }
  });

  } };
}());
