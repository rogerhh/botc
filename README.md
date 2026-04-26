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
  engine/          The game engine. Owns players, characters, the night/day
                   loop, and the prompt broker the UI talks to.
    characters/    One module per Trouble Brewing role.
    engine.py      Phase machine and event loop.
    character.py   Character base class.
    player.py      Player (seat + states + character).
    event.py       Event dataclass + EventType enum.
    prompt.py      Prompt subtypes (YesNo, SelectPlayer, …).
    script.py      Trouble Brewing roster + recommended counts.
    preset.py      Loads a per-edition preset (night sheets, etc.).
    runner.py      Out-of-process engine mirror (subprocess foundation).
    README.md      Engine design notes.
  ui/              The local web UI.
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
python3 -m ui.ui --host 0.0.0.0 --port 8000
```

Optional flags:

- `--access-code` — require an access code on every non-localhost request.
  Pass it explicitly (`--access-code letmein`) or let the server generate
  one (`--access-code` with no argument prints the random code on stdout).
  Localhost is always allowed.

Then visit:

- **Storyteller GUI:** `http://localhost:8000/`
- **Player phone:** `http://<lan-ip>:8000/phone` (the GUI shows a QR code)


## How the pieces talk

The engine and UI live in the same Python process. They share state through
a single `ENGINE` instance plus two side stores in `ui.py`: `STORE` (chair
layout) and `POOL` (character pool + setup picks). The browser drives
everything by hitting JSON HTTP endpoints; each handler mutates the relevant
store, then returns a snapshot.

The engine never imports from `ui/`. Communication runs through `Prompt`
objects on a queue and a `respond(prompt_id, value)` channel.


## Setup flow

1. **Town square.** The Storyteller opens the GUI, adds chairs, types player
   names, and drags chairs into seating order. Chairs live in `STORE`; the
   engine knows nothing about them yet.
2. **Pool selection.** The GUI shows the Trouble Brewing roster. The
   Storyteller picks the roles in play — by hand, or with a "Randomize"
   button that asks the script preset for a legal distribution. Setup
   reminder tokens (Drunk's fake Townsfolk, Fortune Teller's red herring,
   Washerwoman's seen Townsfolk) auto-fill but are draggable.
3. **Token assignment.** The Storyteller distributes physical tokens at the
   table, then types each player's drawn role into their seat panel.
4. **Start Game.** A POST to `/api/engine/start_game`:
   - walks `STORE` clockwise, calls `ENGINE.add_seat()` and
     `ENGINE.assign_character()` for each chair;
   - calls `ENGINE.apply_setup_data(...)` to push the Drunk / FT / WW picks
     onto the right `Character` instances so their first-night abilities
     skip the usual prompts;
   - installs the preset's night sheet on the engine;
   - validates (≥5 players, every player has a role, exactly one Demon);
   - flips the phase to `FIRST_NIGHT` and kicks off the night thread.


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
