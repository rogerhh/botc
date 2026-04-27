# Blood on the Clocktower — Storyteller Server

A self-hosted server that runs a Blood on the Clocktower game (currently the
Trouble Brewing edition). The server is the source of truth for game state.
The Storyteller drives it from a local browser GUI, and players connect their
phones to a separate page that shows them whatever the engine wants them to
see at night — Empath count, Washerwoman tokens, Fortune Teller answer, and
so on.

The point is a calm, in-person Storytelling experience: the rulebook is in
the engine, the bookkeeping is on the screen, and the table is free to focus
on social play.


## What's in the box

```
botc/
  botc.py          Top-level entry point. Builds the engine, then hands
                   it to the UI server.
  engine/          The game engine. Single source of truth for chairs,
                   pool, players, characters, and the night/day loop.
    characters/    One module per Trouble Brewing role.
    engine.py      Phase machine, event loop, on-setup orchestration.
    character.py   Character base class with on_setup_ability().
    player.py      Player (seat + states + character).
    chairs.py      Town-square layout (chair positions, names, binding).
    pool.py        Character pool + four setup picks (Drunk fake, FT
                   red herring, WW seen-Townsfolk, WW wrong).
    event.py       Event dataclass + EventType enum.
    prompt.py      Prompt subtypes (YesNo, SelectPlayer, …).
    enums.py       Phase, Alignment, CharType, DeathCause, SetupMode.
    script.py      Trouble Brewing roster + recommended counts.
    preset.py      Loads a per-edition preset (night sheets, etc.).
    runner.py      Out-of-process engine mirror (subprocess foundation).
    README.md      Engine design notes.
  ui/              The local web UI — a thin renderer over engine state.
    ui.py          stdlib HTTP server, JSON API, and request handlers.
    static/        index.html (Storyteller GUI), phone.html (mobile view),
                   enter.html (access-code gate), QR-code library.
    README.md      UI design notes.
  logger/          End-of-game narration / audit trail (design doc).
  clocktower/      Earlier prototype kept around for reference.
  server/          Earlier prototype kept around for reference.
  assets/
    characters/    One PDF per character (description sheets).
    tokens/        PNG token images, transparent background.
    presets/       e.g. trouble_brewing/characters.csv.
    rules/         Official Blood on the Clocktower rule PDFs.
  scripts/         One-off helpers (token scraper, PDF printer, etc.).
  tests/           pytest suite.
  CLAUDE.md        Project-specific authoring rules.
```


## Running it

The server is stdlib-only Python 3.10+. No `pip install` step is required
for the engine and UI themselves. The helper scripts under `scripts/` have
their own dependencies (Pillow, ReportLab, Playwright); they aren't needed
to play.

Start the local server:

```
python3 botc.py --host 0.0.0.0 --port 8000 --players 8
```

Optional flags:

- `--players N` — number of seats to seed the engine with on startup
  (default 8). The Storyteller can still add/remove seats via the UI.
- `--access-code` — require an access code on every non-localhost request.
  Pass it explicitly (`--access-code letmein`) or let the server generate
  one (`--access-code` with no argument prints the random code on stdout).
  Localhost is always allowed.

`python3 -m ui.ui` still works as a legacy shortcut (it builds a
default engine and forwards to the same server).

Then visit:

- **Storyteller GUI:** `http://localhost:8000/`
- **Player phone:** `http://<lan-ip>:8000/phone` (the GUI shows a QR code)


## How the pieces talk

The engine and UI live in the same Python process. The engine is the
single source of truth: chairs, pool, setup picks, players, characters,
and the selected preset all live on the `Engine` instance. `ui.py` is
a thin renderer / HTTP shim over that state — the legacy `STORE` and
`POOL` symbols still exist but are now lookup-at-call-time proxies that
forward to `ENGINE.chairs` and `ENGINE.pool` respectively.

`Engine.snapshot()` includes the entire visible state (chairs, storyteller
position, pool, setup picks, selected preset, players, log tail), so any
consumer of a snapshot can reconstruct the full UI without external state.

The engine never imports from `ui/`. Communication runs through `Prompt`
objects on a queue and a `respond(prompt_id, value)` channel.


## Setup flow

1. **Town square.** The engine starts with `--players N` empty chairs
   (`engine.chairs`). The Storyteller adds, renames, and drags chairs
   from the GUI; every change goes straight to the engine.
2. **Pool selection.** The Storyteller picks the roles in play — by
   hand, or with a "Randomize" button that asks the script preset
   for a legal distribution. The pool's auto-fill rules keep the
   FT red herring, WW seen-Townsfolk, and WW wrong slots non-stale.
3. **Character assignment.** As soon as the Storyteller types a role
   into a chair, `Engine.assign_character` runs the new character's
   `on_setup_ability(SetupMode.SETUP_PHASE)`, which silently
   absorbs the current pool state — the Drunk picks up the
   pool's `drunk_fake`, the FT picks up `ft_red_herring`, the WW
   picks up `washerwoman_townsfolk` and `washerwoman_wrong`. No
   Storyteller prompts during this phase; the UI is in control.
   Token-drag on the grimoire re-triggers the same SETUP_PHASE pass
   so changes are reflected live.
4. **Start Game.** `Engine.start_game` validates (≥5 players, every
   player has a role, exactly one Demon), installs the preset's
   night sheet, flips the phase to `FIRST_NIGHT`, and kicks off the
   night thread. `_run_setup_actions` then runs each character's
   `on_setup_ability(SetupMode.IN_GAME)`, which **prompts the
   Storyteller** for any picks that weren't pinned down during
   SETUP. Mid-game character changes (e.g. Scarlet Woman → Imp via
   `change_character`) also use the IN_GAME branch.


## Game flow

Once the night thread is running, the engine walks the preset's first-night
sheet and emits a sequence of `Event`s. Each ability is a short ordered list
of small Events — `CheckCondition`, `Wakeup`, `Select`, `Information`,
`Resolution`. Whenever an Event needs Storyteller input (or needs to display
something to a player), the engine emits a `Prompt` and blocks until the UI
posts an answer to `/api/engine/respond`. After every Event, every alive
character's `reaction(event, engine)` is invoked, and any new Events they
emit are inserted at the front of the queue so abilities cascade in the
order the rulebook describes.

After night ends, the engine flips to `DAY` and the UI drives the day:
nominations, votes, executions, and daytime abilities (Slayer, Virgin,
Mayor) all enter the engine as Storyteller-sourced Events. End-of-game
checks run after every relevant Event.


## Drunk / poisoned info

When an information ability fires from a drunk or poisoned source, the
engine pre-fills a *wrong* default and asks the Storyteller to confirm or
edit it before the answer reaches the player's phone. Binary prompts get
the flipped answer pre-selected; range prompts get a random wrong option.
The UI never uses the words "confirm" or "override" — the prompt simply
advances on Next. This applies to every information character (Empath,
Fortune Teller, Washerwoman, Librarian, Investigator, Chef, Undertaker,
Ravenkeeper) and to any future info ability.


## Information hiding rules (binding)

Enforced by the UI:

- The phone never displays a character to its player by name. Identity at
  the table is established with physical tokens.
- Highlighting on the phone shows only the prompt's eligible set, nothing
  else.
- State tokens (the grimoire) are visible only on the local UI.
- Player taps on the phone are advisory; the Storyteller's transcription on
  the local UI is authoritative.
- The Spy is the one exception: their phone shows the grimoire verbatim.


## Tests

```
python3 -m pytest
```

The suite covers engine smoke tests, the per-character setup actions
(Drunk/FT/WW), preset-driven night ordering, reset, and an HTTP smoke test
that exercises the UI's JSON endpoints.


## Adding a new character

1. Create `engine/characters/<name>.py`.
2. Subclass `Character`. Set its name, type, and any setup-time annotations.
3. Implement `ability()` to push the Events the role wants this turn.
4. Override `reaction()` only for the Event types the role cares about, and
   delegate to `super().reaction(event, engine)` for the rest.
5. Register the class in `engine/characters/__init__.py`.

The engine itself does not change. See `engine/README.md` for the full
design rationale.


## Status

Trouble Brewing is implemented end-to-end. Bad Moon Rising and Sects &
Violets are not yet started; preset support already exists, so adding them
should mostly be character modules and a new preset file under
`assets/presets/`.
