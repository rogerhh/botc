# Project Rules

Rules to follow when working on this Blood on the Clocktower server.

## Drunk / Poisoned Information

When a character is drunk or poisoned and the ability shows them
information, the Storyteller is always given a chance to set the
shown answer. The engine pre-fills a *wrong* default so the
Storyteller can simply hit Next and the player gets bad info.

- **Binary info (yes/no, exactly two options):** the engine pre-fills
  the *flipped* (wrong) answer. The Storyteller sees the prompt with
  the wrong answer highlighted and may change it; either way it is
  sent on Next.

- **Range of options (3+ choices, e.g. Empath 0/1/2, Washerwoman
  picking a character):** the engine pre-fills a *random wrong*
  option. The Storyteller may change it before it goes to the player.

- **UI language:** never use the words "confirm" or "override"
  anywhere the Storyteller sees. The drunk/poisoned info prompt has
  the wrong answer pre-selected and is dispatched by hitting Next (or
  Yes/No for binary prompts, with the wrong answer highlighted as the
  default).

This applies to every info character (Empath, Fortune Teller,
Washerwoman, Librarian, Investigator, Chef, Undertaker, Ravenkeeper,
etc.) and to any future info ability added to the script.

## End-of-game announcement timing

The game can only end **after a night, at dawn**. When the engine
detects a winning condition it parks the result on
`Engine.pending_winner` / `Engine.pending_win_reason` and keeps
playing:

- If the win triggers **during the day**, players may continue to use
  abilities and nominate. `advance_to_night` still moves to night.
- The **next night runs no abilities** — every character step,
  Minion Info, Demon Info, the setup-action pass, and the
  `NIGHT_START` / `NIGHT_END` event dispatches are all suppressed.
  The storyteller still sees the **Dusk** and **Dawn** preset
  announcements so the night has its normal rhythm.
- The **win is announced at dawn**: `_finalize_pending_win` flips the
  phase to FINISHED, copies the pending values onto `winner` /
  `win_reason`, and emits the `game_end` console event.

The first win condition to fire wins; subsequent triggers don't
overwrite it. This applies to every win/loss condition (Demon
killed, two players left, Saint executed, Mayor's
three-alive-no-execution, etc.).

## Minion Info / Demon Info always run

The engine deliberately diverges from the canonical Trouble Brewing
"7 or more players" gate on these two first-night steps:

- **Minion Info** runs in every game that has at least one seated
  Minion and one seated Demon, regardless of player count. The
  consolidated prompt wakes every Minion together and shows them
  the `THIS IS THE DEMON` token.
- **Demon Info** runs in every game that has at least one seated
  Demon, regardless of player count. The Demon sees their Minion
  list (which may be a single name on smaller scripts) plus 3
  not-in-play good roles to bluff as.

This means 5- and 6-player Teensyville games still get the reveal
and the bluff list. The preset description text in
`assets/presets/*/first_night.txt` no longer says "If there are 7 or
more players"; the engine ignores that wording either way.

## Bluff pool / "all roles on the script" come from the preset

When an info ability needs the list of "every Townsfolk on the
script", "every Minion on the script", etc. (Demon Info bluff pool,
Washerwoman / Librarian / Investigator misregistration candidates,
Drunk's pretend role pool, Spy / Recluse misregistration list, …),
the engine sources that list from the **active preset's
`characters.csv` roster**, not from the global Trouble Brewing
constants in `engine/script.py`.

Implementation: `Engine.all_character_names()` /
`all_character_names_by_type(...)` consult `self._preset` first and
only fall back to `engine.script` when no preset is installed (used
by tests that don't set one) or when the preset shipped without a
roster file. Every character-side caller (`engine.characters.*`)
goes through those engine helpers, so the scoping happens once.
