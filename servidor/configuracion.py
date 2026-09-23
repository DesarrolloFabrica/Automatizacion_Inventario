"""
Configuración del servicio web, leída del entorno.

Los nombres de las variables son los mismos que documenta
`despliegue/variables.env.example`; si cambia uno, hay que cambiarlo en ambos
sitios. Todo tiene un valor por defecto razonable para poder levantar el
servidor en un equipo sin configurar nada.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from flujo_lib import ROOT, drive

MODOS_CREDENCIALES = ("token", "cuenta_servicio")


def _verdadero(valor: str | None, defecto: bool) -> bool:
    """Acepta 1/0, si/no, true/false. Vacío o desconocido -> el valor por defecto."""
    texto = (valor or "").strip().lower()
    if texto in {"1", "si", "sí", "true", "on", "yes"}:
        return True
    if texto in {"0", "no", "false", "off"}:
        return False
    return defecto


@dataclass(frozen=True)
class Configuracion:
    """Lo que el servicio necesita saber para trabajar."""

    schema: str
    almacen: str  # "gs://bucket/prefijo" o una ruta; vacío = solo disco local
    dir_corridas: Path
    simular_por_defecto: bool
    simular_forzado: bool
    forzar_carga_por_defecto: bool
    modo_credenciales: str
    ruta_token: Path
    ruta_cuenta_servicio: Path | None
    usuario_suplantado: str

    @property
    def es_produccion(self) -> bool:
        return self.schema == "fabrica"



def cargar(env: Mapping[str, str] | None = None) -> Configuracion:
    """Arma la configuración desde las variables de entorno."""
    env = os.environ if env is None else env

    almacen = (env.get("ALMACEN_CORRIDAS") or "").strip()
    if not almacen:
        bucket = (env.get("BUCKET_ESTADO") or "").strip()
        if bucket:
            almacen = f"gs://{bucket}/corridas"

    modo = (env.get("GOOGLE_CREDENCIALES_MODO") or "token").strip().lower()
    if modo not in MODOS_CREDENCIALES:
        modo = "token"

    ruta_sa = (env.get("GOOGLE_SA_JSON") or "").strip()

    return Configuracion(
        schema=(env.get("SCHEMA_POR_DEFECTO") or env.get("LMS_SCHEMA") or "fabrica_pruebas").strip(),
        almacen=almacen,
        dir_corridas=Path((env.get("DIR_CORRIDAS") or str(ROOT / "corridas")).strip()),
        simular_por_defecto=_verdadero(env.get("SIMULAR_POR_DEFECTO"), True),
        # Modo prueba impuesto por el despliegue: la página no puede apagarlo.
        # Sirve para publicar el servicio cuando todavía no puede escribir en la
        # base (por ejemplo, sin permiso de Cloud SQL) sin que nadie lo intente.
        simular_forzado=_verdadero(env.get("SIMULAR_FORZADO"), False),
        forzar_carga_por_defecto=_verdadero(env.get("FORZAR_CARGA_POR_DEFECTO"), False),
        modo_credenciales=modo,
        ruta_token=Path((env.get("GOOGLE_TOKEN_JSON") or str(drive.RUTA_TOKEN)).strip()),
        ruta_cuenta_servicio=Path(ruta_sa) if ruta_sa else None,
        usuario_suplantado=(env.get("GOOGLE_USUARIO_SUPLANTADO") or "").strip(),
    )


def cargar_credenciales(cfg: Configuracion, *, interactivo: bool = False):
    """
    Credenciales de Google del servicio, según el modo configurado.

    - cuenta_servicio: clave de una cuenta de servicio que suplanta al buzón de
      la fábrica. Es lo correcto a largo plazo y necesita que el administrador
      de Google Workspace la autorice.
    - token: el token OAuth que ya existe. Funciona de inmediato, pero es una
      credencial de persona y puede caducar o ser revocada.
    """
    if cfg.modo_credenciales == "cuenta_servicio":
        from google.oauth2 import service_account

        if not cfg.ruta_cuenta_servicio or not cfg.ruta_cuenta_servicio.is_file():
            raise FileNotFoundError(
                f"No se encontró la clave de la cuenta de servicio en {cfg.ruta_cuenta_servicio}."
            )
        creds = service_account.Credentials.from_service_account_file(
            str(cfg.ruta_cuenta_servicio), scopes=drive.SCOPES
        )
        if cfg.usuario_suplantado:
            creds = creds.with_subject(cfg.usuario_suplantado)
        return creds
    return drive.cargar_credenciales(interactivo=interactivo, ruta_token=cfg.ruta_token)
