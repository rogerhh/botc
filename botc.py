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

    The seat-seeding lives on the engine post-refactor; for now the
    chair seeding still happens UI-side at server startup (see
    ``ui.ChairStore.__init__``). Once Phase 2 of the refactor lands,
    this function will instead push the seats onto the engine directly.
    """
    engine = Engine()
    # NOTE: per the staged refactor, the engine doesn't yet own chair
    # seating. That migration is Phase 2 of the refactor described in
    # the project README. Until then, the UI's ChairStore (which seeds
    # 8 chairs by default) and the engine remain decoupled at startup;
    # the chair → engine.Player mapping happens on /api/engine/start_game.
    # The ``default_seats`` arg is plumbed here so the entry-point
    # contract is correct from day one.
    engine._default_seats = default_seats  # noqa: SLF001 — staged refactor
    return engine


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0",
                        help="Interface to bind (default: all interfaces).")
    parser.add_argument("--port", type=int, default=8000,
                        help="TCP port (default: 8000).")
    parser.add_argument("--players", type=int, default=8,
                        help="Default number of seats to start with (default: 8).")
    parser.add_argument(
        "--access-code", nargs="?", const="__AUTO__", default=None,
        metavar="CODE",
        help="Require an access code to visit the site.",
    )
    args = parser.parse_args()

    code = None
    if args.access_code == "__AUTO__":
        code = ui_module._make_random_code()
    elif args.access_code is not None:
        code = args.access_code

    engine = _make_engine(default_seats=args.players)
    ui_module.serve(engine, host=args.host, port=args.port, access_code=code)


if __name__ == "__main__":
    main()
