"""
renovar_token.py — vuelve a autorizar la cuenta fábrica de contenidos en Google.
--------------------------------------------------------------------------------
Regenera token.json (Drive + Sheets + Gmail) en la raíz del repo usando
credentials.json. Solo se ejecuta a mano cuando el flujo avisa que la sesión
venció o que hay que autorizar la cuenta.

Uso:  python renovar_token.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flujo_lib import drive  # noqa: E402
from flujo_lib.mensajes import ErrorFlujo  # noqa: E402


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8", errors="replace")
    print(
        "Se va a autorizar el flujo en Google. En el navegador elige la cuenta "
        "FÁBRICA DE CONTENIDOS (no tu cuenta personal) y acepta los permisos de "
        "Drive, Sheets y Gmail.",
        flush=True,
    )
    try:
        creds = drive.autorizar()
        correo = drive.quien_soy(drive.construir_servicio(creds))
    except ErrorFlujo as e:
        print(f"No se pudo autorizar. {e}", file=sys.stderr, flush=True)
        return 1
    print(f"Cuenta autorizada: {correo}", flush=True)
    print(f"Token guardado en {drive.RUTA_TOKEN}. Ya puedes ejecutar run_flujo.py.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
