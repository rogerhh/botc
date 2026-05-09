# Blood on the Clocktower — Storyteller Server

A self-hosted server for running Blood on the Clocktower.
The Storyteller drives the game from a laptop; players connect their phones
for night info. The engine is the single source of truth.

## Setup

Requires Python 3.10+.

```
git clone <repo-url>
cd botc
pip install -r requirements.txt
python3 botc.py --host 0.0.0.0 --port 8000 --players 8
```

`requirements.txt` pulls `boto3` and `python-dotenv`, used to fetch
character-token PNGs from R2 on first access. Both imports are guarded,
so the server still runs without them — you just won't get token images
unless they're already on disk. R2 credentials go in a local `.env`
(`R2_ENDPOINT`, `R2_BUCKET`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`).

Flags:

- `--players N` — seats to seed on startup (default 8).
- `--access-code [CODE]` — require a code on non-localhost requests. Omit
  the value to auto-generate one (printed to stdout).

Then open:

- **Local UI** (Storyteller's laptop): `http://localhost:8000/`
- **Storyteller UI** (Storyteller's phone): `http://<lan-ip>:8000/phone`
- **Player UI** (each player's phone): `http://<lan-ip>:8000/player`

The Local UI displays QR codes for the phone surfaces.

## Tests

```
python3 -m pytest
```

## More

- `engine/README.md` — engine design and night/day loop.
- `ui/README.md` — UI surfaces and prompt staging.
- `CLAUDE.md` — project authoring rules.
