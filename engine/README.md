# Engine Design

This document describes the design of the Blood on the Clocktower game engine.
The engine is a turn-based action resolver. It does not draw anything, it does
not talk to players directly, and it does not enforce rules that are the
Storyteller's prerogative. It tracks game state, walks the game forward one
discrete step at a time, and emits prompts whenever a human (the Storyteller,
ultimately a player) needs to make a decision.

The Storyteller drives the engine through the UI. Every step of an ability is
exposed as an Event. The Storyteller clicks "Next" to advance to the following
Event. The engine never leaps ahead on its own.


## Goals

- **Faithful to the rulebook.** The internal model matches the way the rules
  are written: players have states, characters have abilities, abilities work
  immediately, and "the player is drunk or poisoned, not the character".
- **Turn-based and inspectable.** At any moment the game is paused on a
  specific Event. The Storyteller sees what is happening and approves the next
  step.
- **Composable abilities.** Abilities are not monolithic procedures. They are
  short ordered sequences of small Events. This is what makes interactions
  between characters tractable and what makes adding new characters cheap.
- **Open to new characters.** Adding a character does not require editing the
  engine. It requires writing a subclass that emits its own Events and
  optionally reacts to a few Event types it cares about.
- **Auditable.** Every Event that changed game state is logged, so the
  Storyteller can summarise the game at the end and players can see how
  history actually unfolded.


## Module Layout

```
engine/
  enums.py          Phase, Alignment, CharType, DeathCause, EventType, ...
  player.py         Player: identity + states + ownership of a Character
  character.py      Character: base class with ability() and reaction()
  event.py          Event: the unit of game progression
  prompt.py         Prompt: a request for Storyteller input or a player display
  script.py         Script: roster, night sheets, edition metadata
  engine.py         Engine: phase machine and event loop
  log.py            EventLog: ordered record of state-changing events
  characters/       One module per character; each subclasses Character
```

The engine never imports from `ui/`. Communication runs through Prompts and a
queue of pending Events.


## Core Concepts

### Player

A `Player` is the seat at the table. It owns a name, a seat index, a
`Character` instance, and a bag of *states* that are independent of which
character it currently has. The states are:

- `alive` / `dead`
- `alignment` (good or evil)
- `char_type` (Townsfolk, Outsider, Minion, Demon, Traveler, Fabled) — a
  cached view of the current character's category, kept on the Player so that
  reactions can look at "is the player a Minion right now" without poking into
  the character.
- `drunk` (boolean)
- `poisoned` (boolean)
- `dead_vote` (boolean: whether their one post-mortem vote is still available)
- `madness` (optional structured value: who/what they are mad about)

States are flat booleans or enums because the full set is known up front and
some are mutually exclusive. They are never persisted on the Character;
swapping characters does not clear them.

The Player exposes mutators (`die`, `revive`, `set_drunk`, etc.) that the
engine and Characters call. When `dead_vote` is consumed it stays consumed
unless the Player is revived, at which point it refreshes.

The Player owns the Character because a player's character can change
mid-game (Imp self-kill into Scarlet Woman, etc.) but the seat persists.


### Character

A `Character` is a stateful object that lives inside a Player. It has access
to its `Player` (so it can mutate states) and tracks character-scoped
bookkeeping that does *not* belong on the Player:

- `first_night` flag — whether tonight is this character's first night,
  refreshed when the character is acquired (resurrection or character swap).
- `once_per_game` slots — refreshed on character swap or resurrection per the
  rulebook.
- private notes the ability needs (e.g. the Fortune Teller's red herring
  player, the Washerwoman's two seen players, the Butler's Master).

Two methods are the engine's whole contract with a character:

- `ability(engine, context) -> None`
  Called when this character's turn comes up on the night sheet, or when a
  daytime ability is triggered. The implementation pushes a sequence of Events
  onto the engine's queue. It does *not* do the work itself; it describes
  the work as Events. If the character has no ability tonight (e.g. the
  Washerwoman after the first night), it pushes nothing.

- `reaction(event, engine) -> None`
  Called once for *every* Event the engine processes, on *every* alive
  character — including the source character. Most characters do nothing for
  most events. The base implementation handles the standard housekeeping for
  events that can affect any character (death, drunk/poisoned toggles,
  alignment change, character swap), so subclasses only need to override
  reaction for the specific Event types their ability cares about. When a
  subclass cannot fully handle the event, it returns control to the base
  reaction.

If the source player of an effect is drunk or poisoned, the base reaction is
responsible for not committing the effect to game state (the Prompt step may
still run so the player still feels like they used their ability — see
*Drunk and Poisoned* below).

### Ability

An ability is an ordered sequence of up to five Events:

1. **CheckCondition** — does the ability trigger tonight at all? If not, the
   remaining events are skipped. (E.g. Undertaker only acts on nights after a
   day with an execution; Ravenkeeper only acts the night they die.)
2. **Wakeup** — tell the Storyteller to wake the player. Night-only.
3. **Select** — collect input from the player via the Storyteller (Monk picks
   a target, Fortune Teller picks two players).
4. **Information** — show information back to the player on their phone
   (Empath count, Fortune Teller yes/no, Washerwoman token).
5. **Resolution** — apply the effect to game state (Slayer kill, Imp kill,
   Virgin's nominator dies).

Not every ability uses every step. Daytime abilities skip Wakeup. Pure-info
abilities (Empath, Chef) have no Resolution. Pure-effect abilities (Soldier
protection is implicit; Imp kill) may have only Resolution. Setup abilities
(Baron, Drunk, Spy's red herring placement) collapse to a single Resolution
fired during the Setup event.

Decomposing abilities this way is the heart of the design. It means:

- The Storyteller advances the game one Event at a time and always sees what
  the engine is about to do.
- Reactions hook precisely where the rulebook says they should ("if the Demon
  attacks the Empath but the Monk protected them, the Empath does not die" is
  the Monk's reaction to the Imp's Resolution event, run before the death is
  committed).
- Future characters that want to interrupt or modify part of an existing
  ability only need to react to the appropriate Event type.


### Event

An `Event` is an immutable record describing one atomic thing that is about
to happen, or has just happened. It carries:

- `type` — one of a closed enum (CheckCondition, Wakeup, Select, Information,
  Resolution, Nomination, Vote, Execution, Death, Resurrect, Drunken,
  Poisoned, ChangeCharacter, ChangeAlignment, Setup, DayStart, NightStart,
  DayEnd, NightEnd, ...).
- `source` — the character or player whose ability or action produced it
  (may be the Storyteller for arbitrations).
- `targets` — the players or characters being acted upon (possibly empty).
- `payload` — typed extras (e.g. the chosen number for the Empath, the
  shown character token for the Washerwoman, the cause of death).

Events are produced by:

- a Character's `ability()` (the normal source),
- a Character's `reaction()` (cascading events, e.g. Imp's Resolution
  triggers a Death event),
- the Engine itself (phase boundaries, Setup, NightStart/NightEnd),
- the Storyteller via the UI (Nomination, Vote, day-time ability triggers).

The engine maintains an event queue. The currently-processing event runs all
character reactions in turn; any events emitted by reactions are inserted
immediately after the current event so they resolve before the next planned
event. This is what gives "abilities work immediately" its meaning.

Events that change game state are appended to the EventLog. Events that are
purely procedural (CheckCondition that passed but produced no effect) need
not be logged.


### Prompt

A `Prompt` is the engine's request for human input or a request to display
information. The engine emits a Prompt when an Event needs the Storyteller
to make a decision or when the UI needs to show information. The engine
blocks until the Prompt is answered (or in the case of an Information prompt,
acknowledged with "Next").

Prompt subtypes:

- **YesNo** — for shake-head questions.
- **SelectPlayer** — choose one or more players from a highlighted set of
  eligibles. Includes a Randomize button.
- **SelectCharacter** — choose one or more characters from a highlighted
  subset (e.g. choose the Townsfolk to show the Washerwoman). Includes
  Randomize.
- **ShowInformation** — pure display. The only control is "Next". This is
  what the player phone renders.
- **Arbitrate** — free-form Storyteller decision when the rules require a
  ruling that is not one of the above (e.g. Recluse register-as choice,
  Scarlet Woman triggering, Mayor death-redirect).

A Prompt carries:

- `text` — what the Storyteller sees, plus the player-facing string for
  ShowInformation prompts (which never reveals the player's character — only
  their name).
- `eligible` — the set of players/characters that are valid choices, used
  by the UI to highlight selections.
- `allow_randomize` — true for player/character selections.
- `target_audience` — local UI (Storyteller) or mobile UI (a specific
  player's phone).

The Prompt is the *only* way information escapes the engine. The Player
selections it returns are the *only* way decisions enter the engine. This
keeps the engine deterministic given a fixed Storyteller transcript.


### Drunk and Poisoned

When a Character is about to act, the engine checks the Player's drunk and
poisoned states. If either is set:

- The Storyteller is prompted (Arbitrate) to compose false information for
  the player, before any Information event reaches the phone.
- The Resolution event is suppressed in terms of game-state mutation, but
  any Wakeup/Information events still run, so the player believes their
  ability fired normally.
- For "once per game" abilities, the slot is still consumed (per the
  rulebook).

This logic lives in the Character base class so subclasses do not have to
re-implement it.


### Script

A `Script` carries:

- the list of available characters in the edition (Trouble Brewing for now),
- the **first night sheet** — ordered list of all characters that act first
  night, regardless of whether they are in this game,
- the **other nights sheet** — ordered list for subsequent nights.

The night sheets include every character in the edition, not just the ones
in play, so the engine simply walks them and skips characters whose Player
is not in the game.


### Engine

The Engine is a phase machine over an event queue.

#### `setup`

1. The local UI lets the Storyteller arrange seats and enter player names.
   This is pre-engine; the engine is told the seating order when the
   Storyteller is done.
2. The Storyteller is prompted to pick the characters in play. The engine
   resolves any setup-time abilities here: Baron swaps two Townsfolk for two
   Outsiders; Drunk replaces a Townsfolk slot in the bag; Fortune Teller's
   Red Herring is chosen via Arbitrate; etc. These are all Setup events.
3. The Storyteller distributes character tokens at the table (offline) and
   types each player's character into the UI. Once "Start" is clicked, the
   engine constructs the initial list of Players in seating order and is
   ready to run.

#### `start_night`

1. Drain any events queued during the previous day (e.g. day-ability
   follow-ups).
2. Pick the night sheet (first vs. subsequent).
3. Walk the sheet. For each character: if a Player has that character and
   the character is eligible to act, call `character.ability()`. The events
   it emits go through the event loop one at a time. After each event, every
   alive character's `reaction()` is invoked; reactions may emit further
   events, which are inserted immediately after the current event.
4. At end of night, diff game state to a NightSummary (who died, who was
   resurrected). The Storyteller announces this. The engine checks
   end-of-game conditions.

#### `start_day`

1. Drain any events queued during the previous night.
2. Loop: wait for Storyteller-driven Events from the UI — nominations,
   votes, executions, day-ability triggers (Slayer, Virgin reaction to
   nominations, etc.). Each one goes through the same event loop with
   reactions.
3. End of day on the Storyteller's signal; check end-of-game conditions.

#### Event loop (shared by night and day)

```
while queue is not empty:
    event = queue.pop_front()
    log_if_state_changing(event)
    for character in alive_characters:
        character.reaction(event, self)        # may push to queue front
    if event needs Prompt:
        send Prompt, wait for response, possibly push more events
    advance only when Storyteller clicks Next
```

The "advance only when Storyteller clicks Next" gate is what makes the
engine turn-based. Even a chain of cascading events between Imp -> Death
-> Ravenkeeper-wakes appears to the Storyteller as a sequence of explicit
steps.


### EventLog

A simple ordered record. Each entry stores the Event, a timestamp, and a
short human-readable summary of what changed. At end of game the log is
rendered as a transcript.

We log only Events that mutated state. Procedural events (a CheckCondition
that found the condition false and short-circuited the rest) are not logged.

Format-wise, the log is structured (one row per event, with type and
payload) so the UI can replay it as a timeline. The end-of-game summary is
generated from this same log.


## Adding a New Character

To add a character:

1. Create `engine/characters/<name>.py`.
2. Subclass `Character`. Set its name, type, and any setup-time annotations.
3. Implement `ability()` to push the Events that describe the character's
   action sequence (CheckCondition, Wakeup, Select, Information, Resolution
   as appropriate).
4. Override `reaction()` only for the Event types this character cares
   about. Fall back to `super().reaction(event, engine)` for everything else.
5. Register the class in `engine/characters/__init__.py`.

The engine itself does not change. This is the entire point of the
event-and-reaction model.

Examples mapped onto the Trouble Brewing roster:

- **Washerwoman / Librarian / Investigator** — Setup event picks a target
  and a "wrong" player; first-night ability emits Wakeup, then Information.
- **Chef** — first-night Wakeup + Information.
- **Empath** — every night Wakeup + Information; reacts to Death events on
  its neighbours by recomputing eligibility.
- **Fortune Teller** — Setup picks Red Herring; every night Wakeup, Select,
  Information; reacts to ChangeCharacter to track that the Demon may move.
- **Undertaker** — CheckCondition (an execution today), Wakeup, Information.
- **Monk** — Wakeup, Select, Resolution (sets a "safe" flag); reacts to the
  Imp's Resolution by cancelling the Death event for the protected target.
- **Ravenkeeper** — reacts to its own Death event during the night by
  emitting Wakeup, Select, Information from inside the reaction.
- **Virgin** — reacts to Nomination events targeting itself by emitting
  Execution against the nominator if they're a Townsfolk.
- **Slayer** — daytime ability emitted on Storyteller trigger;
  CheckCondition, Resolution.
- **Soldier** — reacts to Resolution events from the Demon targeting itself
  by cancelling the resulting Death.
- **Mayor** — reacts to Demon Resolution by Arbitrate-ing a redirect; reacts
  to DayEnd to check the alive-three / no-execution win condition.
- **Drunk / Recluse / Saint / Butler** — Outsider behaviours largely live in
  reactions: Drunk gets ability calls but suppresses effects; Recluse hooks
  any "detect alignment" event to register evil; Saint reacts to its own
  Execution by ending the game; Butler reacts to its own Vote intent by
  checking master state.
- **Poisoner** — every night Wakeup, Select, Resolution (toggle poisoned
  flag, scheduled to clear next dusk via a queued event).
- **Spy** — every night Wakeup + ShowInformation that reveals the Grimoire;
  reacts to detect events by Arbitrate-ing register-as choices.
- **Scarlet Woman** — reacts to Demon Death by checking the alive-five
  threshold and emitting ChangeCharacter on itself.
- **Baron** — Setup-time Resolution that swaps two Townsfolk slots for two
  Outsider slots in the bag.
- **Imp** — every night except first: Wakeup, Select, Resolution (Death);
  reacts to its own Resolution-targeting-self by emitting ChangeCharacter on
  a chosen Minion.

Every one of these slots into the same `ability() + reaction()` skeleton.


## End of Game

End-of-game checks run after every Death/Execution/ChangeCharacter event and
at NightEnd / DayEnd. Conditions:

- All Demons dead -> Good wins (subject to Scarlet Woman replacement).
- Two players left alive -> Evil wins.
- Saint executed -> Evil wins.
- Mayor's three-alive-no-execution condition -> Good wins.

When end-of-game fires, the engine emits a GameOver event, the log is
rendered as a transcript, and the UI moves to a recap screen.
