"""
Acceso a la página: entrar con Google y lista de personas autorizadas.

Por qué el flujo de redirección y no el botón de Google (Google Identity
Services): ese botón exige cargar un script desde `accounts.google.com`, y la
página se hizo entera sin dependencias externas justo porque la red de la CUN
filtra lo que viene de fuera. Aquí el navegador solo visita Google cuando la
persona pulsa «Entrar», que es algo que tiene que poder hacer de todos modos;
el resto —el canje del código y la comprobación del identificador— lo hace el
servidor.

La sesión no se guarda en ningún sitio: viaja en una cookie firmada con HMAC.
Así el servicio sigue sin estado y un reinicio de Cloud Run no echa a nadie,
siempre que `SESION_SECRETO` sea el mismo entre reinicios (por eso se pide como
secreto en el despliegue; ver despliegue/variables.env.example).

El identificador de Google se comprueba siempre contra `GOOGLE_OAUTH_CLIENT_ID`
y solo se acepta el correo si está en `USUARIOS_PERMITIDOS`. Quien no esté en la
lista ve un mensaje claro y no entra: no hay registro automático.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import os
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

AUTORIZACION = "https://accounts.google.com/o/oauth2/v2/auth"
CANJE = "https://oauth2.googleapis.com/token"
# `openid` y `email` son los que importan; `profile` solo sirve para saludar a
# la persona por su nombre en la cabecera.
ALCANCES = "openid email profile"

COOKIE = "sesion_fabrica"
# Diez minutos de margen para completar el viaje de ida y vuelta a Google.
VIDA_ESTADO = 600


class AccesoDenegado(Exception):
    """El correo se comprobó bien, pero no está autorizado."""

    def __init__(self, correo: str):
        super().__init__(correo)
        self.correo = correo


class AccesoInvalido(Exception):
    """Google no devolvió una identidad utilizable (código caducado, etc.)."""


@dataclass(frozen=True)
class ConfiguracionAcceso:
    """Lo que hace falta para dejar entrar a alguien."""

    client_id: str
    client_secret: str
    correos_permitidos: frozenset[str]
    secreto_sesion: str
    horas_sesion: int
    url_publica: str
    cookie_segura: bool

    @property
    def configurado(self) -> bool:
        """Sin estos tres datos no hay forma de iniciar sesión."""
        return bool(self.client_id and self.client_secret and self.secreto_sesion)


def normalizar_correo(correo: str) -> str:
    return (correo or "").strip().lower()


def cargar(env: Mapping[str, str] | None = None) -> ConfiguracionAcceso:
    """Arma la configuración de acceso desde las variables de entorno."""
    env = os.environ if env is None else env

    permitidos = {
        normalizar_correo(parte)
        for parte in (env.get("USUARIOS_PERMITIDOS") or "").replace(";", ",").split(",")
        if parte.strip()
    }

    secreto = (env.get("SESION_SECRETO") or "").strip()
    if not secreto:
        # Se genera uno al vuelo para no impedir el arranque, pero cada reinicio
        # invalida las sesiones abiertas: en el despliegue tiene que venir fijo.
        secreto = secrets.token_urlsafe(32)
        logger.warning(
            "SESION_SECRETO no está definido: se usa uno aleatorio y cada "
            "reinicio del servicio cerrará las sesiones abiertas."
        )

    try:
        horas = int((env.get("HORAS_SESION") or "8").strip())
    except ValueError:
        horas = 8
    horas = max(1, min(horas, 24 * 7))

    return ConfiguracionAcceso(
        client_id=(env.get("GOOGLE_OAUTH_CLIENT_ID") or "").strip(),
        client_secret=(env.get("GOOGLE_OAUTH_CLIENT_SECRET") or "").strip(),
        correos_permitidos=frozenset(permitidos),
        secreto_sesion=secreto,
        horas_sesion=horas,
        url_publica=(env.get("URL_PUBLICA") or "").strip().rstrip("/"),
        # En local se trabaja por http://localhost, donde una cookie «Secure»
        # no se guardaría nunca.
        cookie_segura=(env.get("COOKIE_SEGURA") or "").strip().lower()
        not in {"0", "no", "false", "off"},
    )


def permitido(cfg: ConfiguracionAcceso, correo: str) -> bool:
    """¿Está este correo en la lista de la variable USUARIOS_PERMITIDOS?"""
    return normalizar_correo(correo) in cfg.correos_permitidos


# ---------------------------------------------------------------------------
# Fichas firmadas (sesión y estado anti-CSRF)
# ---------------------------------------------------------------------------
# Las dos son lo mismo: un JSON con fecha de caducidad y una firma HMAC-SHA256.
# No se cifra nada porque no hay nada secreto dentro; lo que importa es que
# nadie pueda fabricar una ficha ni cambiarle el correo.


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode("ascii")


def _desb64(texto: str) -> bytes:
    relleno = "=" * (-len(texto) % 4)
    return base64.urlsafe_b64decode(texto + relleno)


def firmar(secreto: str, datos: dict, *, segundos: int) -> str:
    """Devuelve «contenido.firma», válido durante `segundos`."""
    cuerpo = dict(datos, exp=int(time.time()) + segundos)
    crudo = _b64(json.dumps(cuerpo, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    firma = hmac.new(secreto.encode("utf-8"), crudo.encode("ascii"), sha256).digest()
    return f"{crudo}.{_b64(firma)}"


def verificar(secreto: str, ficha: str | None) -> dict | None:
    """Devuelve el contenido si la firma cuadra y no ha caducado; si no, None."""
    if not ficha or "." not in ficha:
        return None
    crudo, _, firma = ficha.partition(".")
    esperada = hmac.new(secreto.encode("utf-8"), crudo.encode("ascii"), sha256).digest()
    try:
        if not hmac.compare_digest(_desb64(firma), esperada):
            return None
        cuerpo = json.loads(_desb64(crudo))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(cuerpo, dict) or cuerpo.get("exp", 0) < time.time():
        return None
    return cuerpo


def crear_sesion(cfg: ConfiguracionAcceso, correo: str, nombre: str) -> str:
    return firmar(
        cfg.secreto_sesion,
        {"correo": normalizar_correo(correo), "nombre": nombre},
        segundos=cfg.horas_sesion * 3600,
    )


def leer_sesion(cfg: ConfiguracionAcceso, ficha: str | None) -> dict | None:
    """
    La sesión vale si la firma cuadra, no ha caducado y el correo SIGUE en la
    lista: quitar a alguien de `USUARIOS_PERMITIDOS` lo deja fuera en el acto,
    sin esperar a que caduque su cookie.
    """
    cuerpo = verificar(cfg.secreto_sesion, ficha)
    if cuerpo is None or not permitido(cfg, cuerpo.get("correo", "")):
        return None
    return cuerpo


# ---------------------------------------------------------------------------
# Conversación con Google
# ---------------------------------------------------------------------------


def direccion_de_retorno(cfg: ConfiguracionAcceso, base: str) -> str:
    """
    A dónde vuelve Google tras el consentimiento. Tiene que coincidir letra por
    letra con una de las «URI de redireccionamiento autorizadas» del cliente
    OAuth; por eso se prefiere `URL_PUBLICA` cuando está definida, en vez de
    deducirla de la petición (detrás de Cloud Run la petición llega por http).
    """
    return f"{cfg.url_publica or base.rstrip('/')}/api/sesion/callback"


def url_autorizacion(cfg: ConfiguracionAcceso, retorno: str, estado: str) -> str:
    parametros = {
        "client_id": cfg.client_id,
        "redirect_uri": retorno,
        "response_type": "code",
        "scope": ALCANCES,
        "state": estado,
        # Siempre la pantalla de elegir cuenta: en los equipos compartidos de la
        # fábrica es habitual tener varias sesiones de Google abiertas.
        "prompt": "select_account",
    }
    return f"{AUTORIZACION}?{urlencode(parametros)}"


def canjear_codigo(cfg: ConfiguracionAcceso, codigo: str, retorno: str) -> dict:
    """
    Cambia el código de un solo uso por la identidad de quien entró.

    Devuelve los datos ya comprobados por Google (`email`, `name`). Lanza
    `AccesoInvalido` si el código no sirve o el identificador no es de fiar.
    """
    import requests  # local: solo hace falta al iniciar sesión
    from google.auth.transport import requests as transporte_google
    from google.oauth2 import id_token as identificador_google

    try:
        respuesta = requests.post(
            CANJE,
            data={
                "code": codigo,
                "client_id": cfg.client_id,
                "client_secret": cfg.client_secret,
                "redirect_uri": retorno,
                "grant_type": "authorization_code",
            },
            timeout=30,
        )
    except Exception as e:  # red caída, certificado, proxy…
        raise AccesoInvalido(f"No se pudo hablar con Google: {e}") from e

    if respuesta.status_code != 200:
        raise AccesoInvalido(f"Google rechazó el código ({respuesta.status_code}).")

    bruto = respuesta.json().get("id_token")
    if not bruto:
        raise AccesoInvalido("Google no devolvió el identificador de la cuenta.")

    try:
        datos = identificador_google.verify_oauth2_token(
            bruto, transporte_google.Request(), cfg.client_id
        )
    except Exception as e:
        raise AccesoInvalido(f"El identificador de Google no es válido: {e}") from e

    correo = normalizar_correo(datos.get("email", ""))
    # Una cuenta sin el correo confirmado podría no ser de quien dice ser.
    if not correo or not datos.get("email_verified"):
        raise AccesoInvalido("La cuenta de Google no tiene un correo confirmado.")
    return {"correo": correo, "nombre": (datos.get("name") or correo).strip()}
