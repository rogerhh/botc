"""Blood on the Clocktower — top-level entry point.

The engine is the driver: it is constructed first, then handed to the
UI server. The UI is purely a renderer / input relay over the engine
state.

Run::

    python3 botc.py [--host 0.0.0.0] [--port 8000]
                    [--players N] [--access-code [CODE]]

This is the only intended way to start the system. ``python3 -m ui.ui``
still works as a legacy shortcut (it builds a default engine and forwards
to the same server), but it shouldn't be used in new docs.
"""

from __future__ import annotations

import argparse
import os
import sys

# Make the repo importable when run as ``python3 botc.py`` from any cwd.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from engine.engine import Engine  # noqa: E402
from ui import ui as ui_module  # noqa: E402


def _make_engine(default_seats: int) -> Engine:
    """Construct the engine with ``default_seats`` empty seats pre-seeded.

    The engine seeds its own ``ChairStore`` from this count (see
    ``engine/chairs.py``). The UI is a thin renderer over the resulting
    ``engine.chairs`` instance.
    """
    return Engine(default_seats=default_seats)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0",
                        help="Interface to bind (default: all interfaces).")
    parser.add_argument("--port", type=int, default=8000,
                        help="TCP port (default: 8000).")
    parser.add_argument("--players", type=int, default=8,
                        help="Default number of seats to start with (default: 8).")
    # --access-code defaults to auto-generated. The combination of
    # default-on tunnel + no auth would publish the BotC server to the
    # open internet, which is unsafe; defaulting an access code on
    # closes that hole. Pass `--no-access-code` to explicitly disable
    # (e.g. LAN-only games on a trusted home network).
    parser.add_argument(
        "--access-code", nargs="?", const="__AUTO__", default="__AUTO__",
        metavar="CODE",
        help="Require an access code to visit the site. With no value, "
             "auto-generates a 6-character code (DEFAULT). Pass an "
             "explicit value to use a fixed code, or --no-access-code "
             "to disable.",
    )
    parser.add_argument(
        "--no-access-code",
        action="store_const", dest="access_code", const=None,
        help="Disable the access code; anyone with the URL can join.",
    )
    # --tunnel defaults to ON: phones can connect from off-network and
    # the BotC traffic is wrapped in TLS by Cloudflare's edge. Pass
    # --no-tunnel to fall back to LAN-only operation. If `cloudflared`
    # isn't installed the spawn fails non-fatally and the QR codes
    # fall back to the LAN IP automatically.
    parser.add_argument(
        "--tunnel", dest="tunnel", action="store_true",
        help="Enable the Cloudflare Quick Tunnel. This is the DEFAULT; "
             "the flag is accepted for clarity. Requires `cloudflared` "
             "on PATH (brew install cloudflared / "
             "https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/).",
    )
    parser.add_argument(
        "--no-tunnel", dest="tunnel", action="store_false",
        help="Disable the Cloudflare Quick Tunnel. QR codes will use "
             "the LAN IP, so phones must be on the same WiFi.",
    )
    parser.set_defaults(tunnel=True)
    args = parser.parse_args()

    code = None
    if args.access_code == "__AUTO__":
        code = ui_module._make_random_code()
    elif args.access_code is not None:
        code = args.access_code

    engine = _make_engine(default_seats=args.players)
    ui_module.serve(engine, host=args.host, port=args.port, access_code=code,
                    tunnel=args.tunnel)


if __name__ == "__main__":
    main()
