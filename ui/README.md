# UI Design

The UI is the only thing the engine talks to. Its job is to faithfully
present what the engine asks for and to relay decisions back. There are
three surfaces, named by *who the audience is* (not by what device they
happen to run on):

- **Local UI** (`index.html`) — runs on the same machine as the engine.
  This is the Storyteller's full instrument panel. Almost all
  interaction happens here.
- **Storyteller UI** (`storyteller.html`, served at `/storyteller`) — a second Storyteller
  surface, designed to fit on a phone the Storyteller carries. It
  mirrors the Local UI's grimoire and prompt panel in a portrait
  layout. **Audience: the Storyteller.** It is *not* handed to
  players — the Storyteller uses it to keep an eye on the game when
  they're away from the laptop, and (for in-game info reveals) to
  display player-facing prompts that the player then reads off the
  Storyteller's screen.
- **Player UI** (`player.html`) — runs in each player's own phone
  browser, paired to a specific seat. **Audience: that one player.**
  By design, the Player UI never displays a player's character
  information during the game (see "Information hiding rules" below).

The UI never decides anything on its own. It renders Prompts the engine
sends, and posts decisions back. State is owned by the engine.


## Three-surface model

| What                                           | Local UI | Storyteller UI | Player UI |
|------------------------------------------------|:--------:|:--------------:|:---------:|
| Setup: seats, names, character selection       | yes      | view-only      | no        |
| Town square (state tokens, seat panels)        | yes      | yes (full grimoire) | no   |
| Day: nominations, votes, executions, day abilities | yes  | yes            | no        |
| Storyteller arbitrations                       | yes      | yes            | no        |
| Wake / select prompts to a player              | yes (entry) | yes (entry, hand-over) | yes (display) |
| Show information privately to a player        | yes (entry) | yes (display, hand-over) | yes (display) |
| Replay / end-of-game summary                   | yes      | yes            | no        |
| Player's own character name                    | yes      | yes            | **never** |
| Other players' characters / Grimoire           | yes      | yes            | **never** (Spy excepted) |

The Storyteller UI is just a portable mirror of the Local UI. The
Player UI is the only surface designed to be looked at by a player.

The Local UI displays QR codes so phones can join: one for the
Storyteller's own phone (Storyteller UI) and one per seat for the
players (Player UI). Each player's QR is tied to their seat.


## Communication

- Engine <-> Local UI: a local socket (HTTP + WebSocket on a localhost
  port). The Local UI subscribes to a stream of Prompts and emits
  decisions.
- Local UI <-> Storyteller UI: same server, accessible from the LAN.
  The Storyteller UI is just another view onto the same engine state;
  it can answer Storyteller prompts the same way the Local UI does.
- Local UI <-> Player UI: same server, accessible from the LAN. Each
  Player UI is paired via per-seat QR to a specific player.

The engine does not address the Player UI directly. When a Prompt is
flagged as `target_audience = player`, the Local UI relays it to the
correct seat's Player UI and shows a thin local mirror with a "Next"
button so the Storyteller can advance once the player has seen it. The
same prompt also surfaces on the Storyteller UI, which is what the
Storyteller can hand to the player as a fallback if the player's own
phone isn't available.


## Local UI

### Setup phase

1. **Seats.** Draw a town square, let the Storyteller add or remove seats
   to match the player count, drag them into the table layout. Seats are
   ordered clockwise.
2. **Names.** Each seat gets a player name typed in. This is local UI
   bookkeeping — the engine receives the seating order only when setup is
   complete.
3. **Character selection.** A panel shows the script roster (Trouble
   Brewing) grouped by type (Townsfolk, Outsiders, Minions, Demon). The
   Storyteller picks the chosen characters for this game. The number of
   each type is shown so the Storyteller can match the setup sheet.
4. **Setup-time abilities.** Once selection is finalised, the engine
   replays setup abilities as Prompts: Baron's "+2 Outsiders" is an
   Arbitrate prompt asking which two Townsfolk to remove and which two
   Outsiders to add; Drunk slot is an Arbitrate prompt picking which
   Townsfolk slot in the bag is secretly the Drunk; Fortune Teller's Red
   Herring is an Arbitrate prompt picking a good player.
5. **Token distribution.** Done at the table, off-screen. The Storyteller
   types each player's drawn character into their seat panel.
6. **Start.** Click "Start" and the engine moves into the first night.

### Game phase

The main view is the **Town Square**: a ring of seat tokens in seating
order. Each seat shows:

- Player name (always visible to the Storyteller; a player's own name
  also appears on their Player UI, but no player ever sees another
  player's name on the Player UI).
- The Character token (visible only on the Storyteller's surfaces —
  the Local UI and the Storyteller UI. This is the Grimoire and is
  never shown on the Player UI; see "Information hiding rules" below).
- **State tokens** stacked next to the seat: alive/dead, drunk, poisoned,
  dead-vote remaining, character-specific reminders ("Townsfolk" /
  "Wrong" for Washerwoman, "Red Herring" for Fortune Teller, "Master" for
  Butler, "Safe" for Monk, "Died Today" for Undertaker, "No Ability" for
  Virgin/Slayer once spent, "Is the Drunk" for the Drunk player, etc.).

State tokens update as Events flow through the engine. The Storyteller
should never need to manually move tokens; if an Event changes a state,
the engine logs that Event and the UI re-renders the affected seat.

A **prompt panel** sits below or beside the town square. It is the
six-section layout shared by every character ability and every
engine-driven step that talks to a player (Minion Info, Demon Info):

1. **Title (CHARACTER)** — frozen at the start of the panel session.
2. **Description (rulebook ability text)** — frozen at the start of
   the panel session.
3. **ST input stage 1** — pre-wake decisions the Storyteller can lock
   in before the player physically wakes (Washerwoman / Librarian /
   Investigator WRONG player, Undertaker shown character when
   drunk/poisoned, Demon's three not-in-play bluff roles, …). Driven
   by prompts whose `meta["stage"]` is `"st_pre"`. Defaults are
   pre-filled so a single Next click resolves them.
4. **Wake up X (Player)** — synthesized by the UI from the prompt's
   `meta` between the last `st_pre` prompt and the first non-`st_pre`
   prompt. Hidden for Dawn / Dusk / other non-character preset
   steps.
5. **Player input stage** — appears when the player makes a decision
   (Fortune Teller picks 2, Imp picks a kill target, Monk picks a
   protection target, Butler picks a master, Ravenkeeper picks
   whose role to learn). Driven by prompts whose `meta["stage"]` is
   `"player"`. Becomes a highlighted answer pill once given.
6. **ST input stage 2** — appears when the Storyteller's pick depends
   on what the player picked (Fortune Teller drunk/poisoned Yes/No,
   Ravenkeeper drunk/poisoned shown character, Imp self-kill →
   choose new Imp). Driven by prompts whose `meta["stage"]` is
   `"st_post"`. Same answer-pill treatment as stage 1.
7. **Show this to player** — auto-displays the info tokens
   (highlight tokens like *THESE ARE YOUR MINIONS*, *THESE
   CHARACTERS ARE NOT IN PLAY*, the Washerwoman's character token
   alongside the two highlighted chairs). Driven by an
   `InformationPrompt` with `shown_to_player = True` and
   `meta["stage"] = "info"`. Reaches the player either on their own
   Player UI, or — as a fallback — on the Storyteller UI handed
   physically across the table. The Storyteller clicks Next to dismiss.

UI language note (per the project rules in `CLAUDE.md`): never use
the words "confirm" or "override" on any prompt the Storyteller
sees. Drunk/poisoned prompts pre-fill the wrong answer and are
dispatched by clicking Next (or Yes/No for binary prompts, with the
wrong answer highlighted as the default).

Prompt sub-types and the controls they render:

- For SelectPlayer / SelectCharacter prompts, eligible options are
  highlighted on the town square or in a character grid; selecting one
  posts back to the engine. A **Randomize** button picks a random eligible
  option for the Storyteller (used when the Storyteller doesn't care, e.g.
  picking which Townsfolk the Washerwoman sees).
- For YesNo prompts, two large buttons.
- For ShowInformation prompts targeted at a player, the Storyteller sees
  a description of what is being shown on which Player UI (and on the
  Storyteller UI mirror) and a "Next" button.
- For Arbitrate prompts, a free-form choice with the relevant option set
  (e.g. "Recluse registers as: [Imp / Poisoner / Spy / Baron / Scarlet
  Woman / Recluse]").

A **Next** button at the foot of the prompt panel advances the engine to
the next Event when the current one needs no further input. The
Storyteller can always read the current Event in plain language above the
button.

### Player side panel

Once the game has started (any phase that is not `setup`), tapping a
chair on the town square opens a **player side panel** that slides in
from the left edge of the screen. During Setup the chair-tap still
opens the Setup-phase chair editor; the side panel is strictly an
in-game surface.

The side panel is intentionally a **pure read of the engine
snapshot** — it owns no state of its own. Every value it shows comes
from a field on `Engine.snapshot()["players"][i]`. The relevant
fields, all surfaced explicitly so the panel can be a thin renderer:

- Identity: `name`, `seat`, `character`, `perceived_character`,
  `char_type`, `alignment`.
- Life: `alive`, `death_cause`, `has_dead_vote`.
- Conditions: `drunk`, `poisoned`, `protected_from_demon`,
  `once_per_game` / `once_per_game_used`, `mad_about`, `notes`.
- Today: `has_nominated_today`, `has_been_nominated_today`.
- Affordances: `can_nominate`, `can_be_nominated`, `can_vote`,
  `has_daytime_ability`.

The panel re-renders on every `/api/state` poll, so changes from
elsewhere — a Virgin executing the nominator, the grimoire marking
someone drunk, an end-of-game flip — show up live without the
Storyteller having to close and reopen the panel.

Below the state read-out is a row of four action buttons. Each one
posts to a small per-action endpoint on the engine, then re-polls the
snapshot and refreshes the panel. The buttons are gated client-side
by the same affordance flags shown above so disabled buttons are
self-explanatory.

- **Use ability.** Calls `POST /api/engine/use_ability` →
  `Engine.use_daytime_ability(player_id)`. The engine spawns a worker
  thread that calls the player character's `daytime_ability(engine)`,
  which then drives the same prompt flow used at night (the
  Storyteller answers the spawned `SelectPlayer` / Information /
  follow-up prompts in the prompt panel). Disabled when the player
  is dead, the character has no `daytime_ability` override
  (`has_daytime_ability == false`), or a once-per-game ability has
  already been spent.

- **Nominate.** Calls `POST /api/engine/nominate` with
  `nominator_id` + `nominee_id`. Clicking Nominate opens an inline
  player picker inside the panel (sorted by seat); selecting a
  nominee dispatches an `EventType.NOMINATION` so character
  reactions fire — most importantly the Virgin's "first nomination
  by a Townsfolk → execute the nominator" trigger. Dead players
  appear in the picker but are visually marked; they cannot be
  nominated only if they were already nominated today (per
  `can_be_nominated`). The Nominate button itself is disabled for
  the open player when they are dead, when they have already
  nominated today, or when the phase is not Day.

- **Voted.** Calls `POST /api/engine/vote`. For a living player this
  is logged but otherwise informational. For a dead player it
  consumes their single dead-vote token (`has_dead_vote → false`).
  Disabled for dead players who have already spent their dead vote.

- **Execute.** Calls `POST /api/engine/execute` →
  `Engine.execute_player`. Confirms via a browser dialog, then kills
  the player with cause `EXECUTION` and dispatches an
  `EventType.EXECUTION` so reactions and end-of-game checks run.
  Disabled outside Day phase, and on already-dead players.

UI language: the panel uses no "confirm" or "override" wording, in
line with the project rule for drunk/poisoned info prompts. Buttons
are simple verbs (Use ability / Nominate / Voted / Execute); their
disabled state is the single source of truth about whether the
action is currently legal.

A side panel shows the **Event log** — a running, scrollable list of
state-changing Events with timestamps. This is what the Storyteller can
narrate from at end of game. It is also useful mid-game to undo a misclick
(supported via "rewind to event N").


## Storyteller UI

The Storyteller UI (`storyteller.html`, served at `/storyteller`) is a portrait-phone mirror
of the Local UI for the Storyteller's own use. **Audience: the
Storyteller.** It is reached by the Storyteller scanning the
Storyteller QR shown on the Local UI; it is *not* the page a player's
phone lands on.

It shows the same town square, the same per-seat side panel, and the
same Storyteller prompt panel as the Local UI, restyled for a portrait
phone screen. Tapping a chair on the Storyteller UI opens the same
side panel; selecting a player answers the active SELECT_PLAYER
prompt. The Storyteller can hand this phone *physically* to a player
during an Information step (the prompt panel collapses to a clean
"show this to the player" card so the surrounding Grimoire is hidden
during the hand-over) — but the Storyteller UI is not paired to that
player and reverts to the full grimoire view as soon as it returns to
the Storyteller.

Because the audience is the Storyteller, the Storyteller UI is allowed
to display the full Grimoire (player names, characters, status
reminder tokens, dead shrouds). The information hiding rules below
apply only to the Player UI.


## Player UI

The Player UI (`player.html`) is the page a single player loads on
their own phone after scanning their per-seat QR. **Audience: that one
player.** It speaks for one seat for the rest of the game.

### When the engine is silent

The Player UI is dim with a placeholder ("Eyes closed.") and listens
for prompts. Most of the time it shows nothing — players are awake
during the day, in which case talk happens around the table, not on the
phone.

### When a Prompt arrives for this player

The Player UI shows:

- **The player's name.** Never the player's character. Even the
  player's own Player UI never displays "You are the Empath." Players
  know their own character from the physical token they were dealt.
- **Question or information.** Plain language, written from the
  Storyteller's perspective ("You learn that one of these two players is
  the Imp", "Pick a player to protect").
- **Eligible selections** (when applicable). For Select prompts, the
  seat list is shown with eligibles highlighted and non-eligibles
  greyed out. Tapping is illustrative only — the player physically
  points at the table; the Storyteller transcribes the choice on the
  Local UI.
- **No "I don't know my character" reveal.** Even Information prompts
  that *come from* a character ability ("Empath: 1") never spell the
  source character name on the Player UI. They show the data only.

The Player UI has no decision-making controls. The Storyteller is the
gatekeeper. The Player UI exists to display secret information
privately. The Storyteller's "Next" on the Local UI dismisses the
Player UI display.

### Drunk / poisoned source players

When the prompt's source player is drunk or poisoned, the engine first
issues an Arbitrate prompt to the Storyteller on the Local UI to
compose the false information. Only that constructed information is
sent to the Player UI. The Player UI has no idea anything is different.


## Information hiding rules (binding)

These rules apply to the **Player UI** — the only surface a player
ever looks at. The Local UI and the Storyteller UI are both
Storyteller-audience surfaces and are allowed to show the full
Grimoire.

1. **The Player UI never displays a player's character information
   during the game.** Not their own character, not anyone else's. Even
   the player's own Player UI never says "You are the Empath." Player
   identity at the table is established via physical tokens, not the
   phone.
2. Highlighting on the Player UI shows only the Prompt's eligible set,
   not extra context. If the Washerwoman is shown two players and a
   Townsfolk token, the eligible set on her Player UI is exactly those
   two players plus that one token; nothing else is highlighted,
   nothing else is even listed.
3. State tokens (the Grimoire) are visible only on the Storyteller's
   surfaces — the Local UI and the Storyteller UI. The Player UI
   never sees the Grimoire.
4. Player taps on the Player UI (if we ever choose to allow them as a
   convenience) are advisory only; the Storyteller's transcription on
   the Local UI is authoritative.
5. The Spy is the only character whose Player UI ever shows the
   Grimoire. That is a deliberate feature, not a leak — it is
   rendered exactly as the Storyteller's local Grimoire so the Spy
   player can memorise what they need. This is the *only* exception
   to rules 1 and 3.


## Replay

After GameOver, the Local UI replays the EventLog as a narrated timeline
the Storyteller can walk the table through ("on Night 1, the Imp killed
Bob; the Empath learned 1; Charlie nominated Dave"). Each event in the
log links to the resulting state diff, so the Storyteller can answer
questions like "wait, why was Charlie poisoned on day 3?".
