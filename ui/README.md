# UI Design

The UI is the only thing the engine talks to. Its job is to faithfully
present what the engine asks for and to relay decisions back. There are two
surfaces:

- **Local UI** — runs on the same machine as the engine. This is the
  Storyteller's instrument panel. Almost all interaction happens here.
- **Mobile UI** — runs in a phone browser on the same local network. Each
  player can connect to it (via QR code) when they need to be shown
  information privately during the night.

The UI never decides anything on its own. It renders Prompts the engine
sends, and posts decisions back. State is owned by the engine.


## Two-surface model

| What                                           | Local UI | Mobile UI |
|------------------------------------------------|:--------:|:---------:|
| Setup: seats, names, character selection       | yes      | no        |
| Town square (state tokens, seat panels)        | yes      | no        |
| Day: nominations, votes, executions, day abilities | yes  | no        |
| Storyteller arbitrations                       | yes      | no        |
| Wake / select prompts to a player              | yes (entry) | yes (display) |
| Show information privately to a player        | yes (entry) | yes (display) |
| Replay / end-of-game summary                   | yes      | no        |

Anything the player needs to *see* privately goes to the Mobile UI.
Everything else stays on the Local UI.

The Local UI displays a QR code so a phone can join. The Mobile UI is
shown on the player's phone (or on the Storyteller's spare phone passed to
the player) when their turn comes up at night.


## Communication

- Engine <-> Local UI: a local socket (HTTP + WebSocket on a localhost
  port). The Local UI subscribes to a stream of Prompts and emits decisions.
- Local UI <-> Mobile UI: same server, on the same port, accessible from
  the LAN. The Mobile UI is paired via QR code to a specific player seat.

The engine does not address the Mobile UI directly. When a Prompt is
flagged as `target_audience = mobile`, the Local UI relays it to the
correct phone and shows a thin local mirror with a "Next" button so the
Storyteller can advance once the player has seen it.


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

- Player name (always visible to the Storyteller, never sent to other
  players' phones).
- The Character token (only visible on the Local UI, this is the
  Storyteller's Grimoire).
- **State tokens** stacked next to the seat: alive/dead, drunk, poisoned,
  dead-vote remaining, character-specific reminders ("Townsfolk" /
  "Wrong" for Washerwoman, "Red Herring" for Fortune Teller, "Master" for
  Butler, "Safe" for Monk, "Died Today" for Undertaker, "No Ability" for
  Virgin/Slayer once spent, "Is the Drunk" for the Drunk player, etc.).

State tokens update as Events flow through the engine. The Storyteller
should never need to manually move tokens; if an Event changes a state,
the engine logs that Event and the UI re-renders the affected seat.

A **prompt panel** sits below or beside the town square. It shows the
current Prompt the engine is waiting on:

- For SelectPlayer / SelectCharacter prompts, eligible options are
  highlighted on the town square or in a character grid; selecting one
  posts back to the engine. A **Randomize** button picks a random eligible
  option for the Storyteller (used when the Storyteller doesn't care, e.g.
  picking which Townsfolk the Washerwoman sees).
- For YesNo prompts, two large buttons.
- For ShowInformation prompts targeted at a player, the Storyteller sees
  a description of what is being shown to which phone and a "Next" button.
- For Arbitrate prompts, a free-form choice with the relevant option set
  (e.g. "Recluse registers as: [Imp / Poisoner / Spy / Baron / Scarlet
  Woman / Recluse]").

A **Next** button at the foot of the prompt panel advances the engine to
the next Event when the current one needs no further input. The
Storyteller can always read the current Event in plain language above the
button.

Tapping a seat opens a **seat panel** with:

- The player's character and current states.
- Available actions for the current phase: nominate, vote on (during a
  vote), execute, or trigger a daytime ability (Slayer shoots, Mayor
  declares, etc.).

These Storyteller-initiated actions are posted to the engine as
Storyteller-sourced Events.

A side panel shows the **Event log** — a running, scrollable list of
state-changing Events with timestamps. This is what the Storyteller can
narrate from at end of game. It is also useful mid-game to undo a misclick
(supported via "rewind to event N").


## Mobile UI

A phone connects via QR code to a specific seat. From that point on, the
phone speaks for that one player.

### When the engine is silent

The phone is dim with a placeholder ("Eyes closed.") and listens for
prompts. Most of the time it shows nothing — players are awake during the
day, in which case talk happens around the table, not on the phone.

### When a Prompt arrives for this player

The phone shows:

- **The player's name.** Never the player's character. Even the player's
  own phone never displays "You are the Empath." Players know their
  character from the physical token.
- **Question or information.** Plain language, written from the
  Storyteller's perspective ("You learn that one of these two players is
  the Imp", "Pick a player to protect").
- **Eligible selections** (when applicable). For Select prompts, the seat
  list is shown with eligibles highlighted and non-eligibles greyed out.
  Tapping is illustrative only — the player physically points at the
  table; the Storyteller transcribes the choice on the Local UI.
- **No "I don't know my character" reveal.** Even Information prompts
  that *come from* a character ability ("Empath: 1") never spell the
  character name on the phone. They show the data only.

The Mobile UI does not have decision-making controls. The Storyteller is
the gatekeeper. The phone exists to display secret information privately.
The Storyteller's "Next" on the Local UI dismisses the phone display.

### Drunk / poisoned source players

When the prompt's source player is drunk or poisoned, the engine first
issues an Arbitrate prompt to the Storyteller on the Local UI to compose
the false information. Only that constructed information is sent to the
phone. The phone has no idea anything is different.


## Information hiding rules (binding)

These are absolute rules the UI must enforce:

1. The Mobile UI never displays a character to its player by name. Player
   identity at the table is established via physical tokens, not the
   phone.
2. Highlighting on the Mobile UI shows only the Prompt's eligible set,
   not extra context. If the Washerwoman is shown two players and a
   Townsfolk token, the eligible set is exactly those two players plus
   the one token; nothing else is highlighted, nothing else is even
   listed.
3. State tokens are visible only on the Local UI. The Mobile UI never
   sees the Grimoire.
4. Player decisions made on the Mobile UI (if we ever choose to allow
   them as a convenience) are advisory only; the Storyteller's
   transcription on the Local UI is authoritative.
5. The Spy is the only character whose Mobile UI ever shows the
   Grimoire. That is a deliberate feature, not a leak — it is rendered
   exactly as the Storyteller's local Grimoire so the Spy player can
   memorise what they need.


## Replay

After GameOver, the Local UI replays the EventLog as a narrated timeline
the Storyteller can walk the table through ("on Night 1, the Imp killed
Bob; the Empath learned 1; Charlie nominated Dave"). Each event in the
log links to the resulting state diff, so the Storyteller can answer
questions like "wait, why was Charlie poisoned on day 3?".
