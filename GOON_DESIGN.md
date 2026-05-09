# Goon Architectural Design — Audit & Plan

## TL;DR

The current `Goon.reaction()` listens on every `SELECT` event during night
phases and synchronously drunkens the source the moment a `SELECT` carrying
the Goon's seat is dispatched. That works for single-target abilities (Imp,
Monk, Pukka) because `has_ability` flips False before the ability's
resolution code runs. It is **wrong for every multi-target ability** (one
`SELECT` carries N targets; the Goon's reaction fires once for the whole
batch; ordering collapses).

The fix is two new primitives plus per-character touch-ups:

1. **`Engine.notify_goon_chosen(source, target_player)`** — called by a
   character's own ability code, once per actively chosen target, after the
   `SELECT` dispatch. Resolves to `Goon.choose_me` (gated on Goon
   `has_ability` and the first-per-night gate).
2. **`Character.process_targets_with_goon_break(...)`** — base-class loop
   helper for multi-target action abilities. Iterates targets in the
   player's click order (already preserved by the prompt UI), runs the
   per-target `action_fn`, fires the notify after each, and breaks if the
   source loses `has_ability`.

The Goon's global `SELECT` listener is removed; the body moves into
`Goon.choose_me`. Goon-specific behavior stays in `goon.py`. Click order
from the multi-select prompt is the ordering — never an extra ST prompt.

---

## Audit — final grouping

### Group A — Default flow correct (single-target, plus Courtier)

`imp`, `pukka`, `poisoner`, `monk`, `devils_advocate`, `exorcist`,
`ravenkeeper`, `butler`, `sailor`, `gambler`, `godfather`, `zombuul`,
`professor`, `courtier`.

Each dispatches a single `SELECT` against an actively-chosen seat (or, for
Courtier, a chosen character name that the engine maps to a seat). The
Goon's drunkening fires synchronously via `notify_goon_chosen`; the
source's `has_ability` becomes False before the ability resolves, so:

* For action abilities (Imp, Pukka, Poisoner, Monk, Devil's Advocate,
  Exorcist, Butler, Sailor, Godfather, Zombuul, Professor, Courtier):
  the action self-skips because `has_ability` is False.
* For info abilities (Ravenkeeper, Gambler): the existing drunk/poisoned
  branch fires — random-wrong default, ST may change before the player
  sees it (per CLAUDE.md "Drunk / Poisoned Information").

**Courtier specifics.** Courtier picks a character *name* via
`SelectCharacterPrompt`; today the SELECT event targets `[self.player]`
(line 220). We **do not change the SELECT shape**. Instead, when the
chosen character resolves to a seated player and that player is the Goon,
the Courtier's ability calls `engine.notify_goon_chosen(self, goon_seat)`
directly. If the Goon character is not seated (or Courtier picked a
different name), the call is a no-op.

### Group B — Multi-target, ordering matters

`innkeeper`, `shabaloth`, `po`, `chambermaid`, `fortune_teller`.

Two sub-shapes:

* **Action abilities (Innkeeper, Shabaloth, Po):** `ability()` loops over
  picked targets (or applies effects per target). Use
  `process_targets_with_goon_break` and the player's click order from the
  prompt — kills/effects land for targets picked before the Goon, the
  notify drunkens the source on the Goon's turn, and the loop breaks for
  any targets picked after the Goon.
* **Info abilities (Chambermaid, Fortune Teller):** `ability()` computes
  one info value from all targets. No loop, no ordering decision. Use
  `engine.notify_goon_chosen_for_any(self, targets)` — fires the notify
  iff the Goon is among the targets — and **re-read** drunk/poisoned
  state *after* the notify. The existing drunk-info ST prompt (random
  wrong default, ST may change) handles the rest.

### Group C — Special

`assassin`. Force-kill must land before the Goon's drunkening blocks it,
but the Goon's alignment-flip and the Assassin getting drunk must still
happen. Override: dispatch `SELECT`, fire `engine.kill(force=True)`
*before* `notify_goon_chosen`, then call notify. The kill is unstoppable;
the alignment flip and source-drunkening still land afterwards.

### Group D — No active player-selection, no Goon interaction

`empath`, `chef`, `clockmaker`, `washerwoman`, `librarian`, `investigator`,
`undertaker`, `virgin`, `mayor`, `soldier`, `drunk`, `fool`, `lunatic`,
`minstrel`, `pacifist`, `tinker`, `tea_lady`, `saint`, `artist`, `recluse`,
`spy`, `baron`, `scarlet_woman`, `mastermind`, `sage`, `grandmother`.

Sage and Grandmother are explicitly here: their two-of-something / red-
herring targets are chosen by the storyteller, not by the player actively
selecting. Per the wiki: "The Storyteller choosing the Goon due to an
ability, such as the Grandmother's, doesn't count." Daytime abilities
(`slayer`, `gossip`, `klutz`, `moonchild`) also fall here because the
Goon's "each night" wording excludes daytime selections.

---

## Architecture

### Pieces

#### 1. `Engine.notify_goon_chosen(source: Character, target: Player)`

```python
def notify_goon_chosen(self, source, target):
    """Active-selection notification: ``source`` chose ``target`` with
    its night ability. If ``target`` is the seated Goon, route to
    ``Goon.choose_me``; otherwise no-op.

    No-ops in any of:
      * No seated Goon character in the game.
      * ``target`` is not the Goon's seat.
      * Goon's ``has_ability`` is False (drunk/poisoned/dead).
      * The first-per-night gate has already fired tonight (registry
        check for an active GoonDrunkEffect sourced by Goon).
      * Phase is not night (daytime selections don't fire Goon).

    The Goon's gate AND its ``has_ability`` apply for every notify call,
    including the second/third caller in the same night.
    """
```

The engine looks up `Goon` by class name once per call. No name-matching
anywhere else in the codebase.

#### 2. `Goon.choose_me(source, source_player, engine)`

The current `Goon.reaction(SELECT)` body moves here verbatim — registry
gate check, `add_effect(GoonDrunkEffect(...))`, alignment flip, log. The
class no longer overrides `reaction()` for `SELECT`.

#### 3. `Character.process_targets_with_goon_break(engine, targets, action_fn)`

```python
def process_targets_with_goon_break(self, engine, targets, action_fn):
    """Iterate targets in the player's click order, calling
    ``action_fn(target)`` for each, then ``engine.notify_goon_chosen(
    self, target)``. After each iteration, if ``self.player.has_ability``
    is now False, stop — remaining targets are skipped (the source has
    been drunkened mid-loop)."""
```

The click order comes straight from `SelectPlayerPrompt`'s response —
`ui/static/index.html:5952` / `5958` push picks in tap order, and
`storyteller.html:2115` does the same on the ST side. No prompt changes
needed.

#### 4. `Engine.notify_goon_chosen_for_any(source, targets)`

Convenience wrapper for info abilities: calls `notify_goon_chosen` for
the first Goon-seat in `targets` (or no-op if none). Saves the per-call
loop in Chambermaid / FT.

### Per-character call shapes

**Single-target action template** (Imp, Monk, Pukka, …):

```python
engine.dispatch(Event(SELECT, source=self, targets=[target]))
engine.notify_goon_chosen(self, target)
if self.player.has_ability:
    engine.kill(target.id, ...)        # or whatever
engine.dispatch(Event(RESOLUTION, ...))
```

**Single-target info template** (Ravenkeeper, Gambler):

```python
engine.dispatch(Event(SELECT, source=self, targets=[target]))
engine.notify_goon_chosen(self, target)
is_drunk_or_poisoned = self.player.drunk or self.player.poisoned   # post-notify
... existing drunk-info path picks wrong default, ST may change ...
```

**Courtier** (picks a character name, may map to Goon's seat):

```python
chosen_name = engine.send_prompt(SelectCharacterPrompt(...))
engine.dispatch(Event(SELECT, source=self, targets=[self.player]))   # unchanged
goon_seat = engine.find_seat_for_character(chosen_name)              # may be None
if goon_seat is not None and chosen_name == "Goon":
    engine.notify_goon_chosen(self, goon_seat)
if self.player.has_ability:
    ... apply Courtier drunkening to the chosen character ...
```

**Multi-target action template** (Shabaloth, Po):

```python
engine.dispatch(Event(SELECT, source=self, targets=chosen_players))
self.process_targets_with_goon_break(
    engine, chosen_players,
    lambda t: engine.kill(t.id, DEMON_KILL, source=self),
)
engine.dispatch(Event(RESOLUTION, ...))
```

**Innkeeper** (notify-all-then-emit — *deliberately diverges* from
Shabaloth/Po):

```python
engine.dispatch(Event(SELECT, source=self, targets=chosen_players))

# Notify every picked target up-front. If the Goon is among them
# — in either order — the retort drunkens the Innkeeper before the
# has_ability gate below runs.
for tp in chosen_players[:2]:
    engine.notify_goon_chosen(self, tp)

if self.player.has_ability:
    engine.add_effect(InnkeeperSafeEffect(
        source=self, targets=[p.id for p in chosen_players[:2]],
    ))
    engine.add_effect(InnkeeperDrunkEffect(
        source=self, targets=[drunk_player.id],
    ))

engine.dispatch(Event(RESOLUTION, ...))
```

The Innkeeper's ability is conceptually one fire — not a per-target
loop — so it deliberately does **not** use
`process_targets_with_goon_break`. **Pick order does not matter** for
the Innkeeper-vs-Goon interaction (per the user's update: *"the Goon
ability makes the Innkeeper drunk before the Innkeeper can make
anyone else drunk"*):

* `[Goon, Other]` and `[Other, Goon]` produce the same end state.
  In both, the Goon's retort drunkens the Innkeeper synchronously
  inside the notify loop, the post-notify `has_ability` gate fails,
  and **no** `InnkeeperSafeEffect` or `InnkeeperDrunkEffect` is
  emitted on either target. No registry-cascade reactivation
  scenario exists because there is nothing to deactivate in the
  first place.

This contrasts with Shabaloth / Po, where ordering *does* matter:
their per-target kill loop must let kills picked before the Goon
land, and stop only on the Goon's iteration. Use
`process_targets_with_goon_break` for those; do **not** for the
Innkeeper.

**Multi-target info template** (Chambermaid, Fortune Teller):

```python
engine.dispatch(Event(SELECT, source=self, targets=chosen_players))
engine.notify_goon_chosen_for_any(self, chosen_players)
is_drunk_or_poisoned = self.player.drunk or self.player.poisoned   # post-notify
... existing drunk-info path picks wrong default, ST may change ...
```

**Assassin** (force-kill before notify):

```python
engine.dispatch(Event(SELECT, source=self, targets=[target]))
self._used = True
self.player.once_per_game_used = True
engine.add_effect(AssassinNoAbilityEffect(...))
engine.kill(target.id, DeathCause.ABILITY, source=self, force=True)
if not target.alive:
    engine.add_effect(AssassinDeadEffect(...))
engine.notify_goon_chosen(self, target)    # alignment flips, Assassin drunkens
engine.dispatch(Event(RESOLUTION, ...))
```

User scenario 4: Goon dies (force-kill), Goon turns evil (alignment flip),
Assassin drunkens (no further effect this slot since once-per-game is
spent and the kill already landed).

### Effect-chain re-resolution: nothing to add

User scenarios 6 / 7 are handled by the existing `Engine.resolve_droison_state`
cascade (engine.py ~3364–3551) plus the default
`purge_on_source_death=True` on `GoonDrunkEffect`. Innkeeper-sourced SAFE
deactivates when Innkeeper goes drunk; Goon dying purges the
GoonDrunkEffect; the cascade reactivates Innkeeper-sourced effects. No
new code, just tests.

---

## Behavior contract — confirmed answers

* **Drunk/poisoned Goon ⇒ no retort, no flip.** `notify_goon_chosen`
  gates on Goon `has_ability`. Same gate for the second / third caller
  the same night.
* **First-per-night gate.** Registry check for an active
  `GoonDrunkEffect` sourced by the Goon. Once fired, every subsequent
  `notify_goon_chosen` no-ops for the rest of the night.
* **Pick order = ordering.** No extra ST prompt. Multi-select prompts
  already preserve click order
  (`ui/static/index.html:5952,5958`,  `storyteller.html:2115`).
* **Daytime SELECTs do not fire Goon.** `notify_goon_chosen` checks
  `engine.phase.is_night`. Slayer / Gossip / Klutz / Moonchild safe.
* **Storyteller-driven targeting does not fire Goon.** Sage and
  Grandmother do not call `notify_goon_chosen`, so even if the chosen
  player happens to be the Goon, nothing fires.

---

## What this preserves

* **Character scalability.** Pattern: every character with a *player-
  selecting* night ability calls `notify_goon_chosen` once per actively-
  chosen target. Multi-target action abilities use
  `process_targets_with_goon_break`. New character → follow the template.
  No engine edits.
* **Preset scalability.** No preset `characters.csv` references the Goon.
  Goon-specific behavior lives entirely in `goon.py`. Removing Goon
  from a preset turns every notify call into a no-op.
* **Existing effect machinery untouched.** `GoonDrunkEffect`, the
  registry, `resolve_droison_state`, phase-boundary purges keep their
  current shape.

## What it removes

* The current global `Goon.reaction(SELECT)` listener
  (`engine/characters/goon.py` lines 91–144). The body moves into
  `Goon.choose_me`.

---

## Migration plan — separate PRs

0. **PR 0 — Multi-select pick-order gradient.** Pure UI change, no engine
   coupling. For every multi-select prompt (player chairs and character
   tiles), render the i-th pick with a color along a gradient instead of
   a single "selected" highlight. First pick → gradient start, last pick
   → gradient end. The pick index is already tracked in
   `_selectedPlayerIds` / `_selectedCharacters` (push/shift in click
   order, see `index.html:5946–5959`, `storyteller.html:1570–1573`,
   `2113–2115`, `2155–2158`). Implementation sketch:

   * On each render that highlights the selection, set an inline
     `--pick-index` (and `--pick-count`) CSS custom property on the
     element (chair `<g>`, character `.pick-item`).
   * Add a `.selected` rule that derives `border-color` /
     `background-color` from the index using `hsl(...)` or
     `color-mix(...)`.
   * Reapply on the same render hooks that set the existing `selected`
     class today (chair render, pick-item render, st-console render).
   * No labels, no legend, no copy in the prompt — the gradient is
     self-explanatory once the ST or player picks more than one seat.
   * The gradient applies to every `count >= 2` prompt, not just
     prompts that involve the Goon. Consistency keeps the rule
     invisible to the player when the Goon isn't in play, but the
     visual cue is there if they ever need it.

1. **PR 1 — Primitives.** Add `Engine.notify_goon_chosen`,
   `Engine.notify_goon_chosen_for_any`,
   `Character.process_targets_with_goon_break`, `Goon.choose_me`. Goon's
   SELECT-listener stays in place. No call sites yet. Existing tests
   unchanged.

2. **PR 2 — Migrate single-target Group A.** Insert
   `notify_goon_chosen` calls. Remove the SELECT branch from
   `Goon.reaction`. Add tests covering single-target Goon picks
   (Imp/Monk/Pukka/Poisoner/Devil's Advocate/Exorcist/Ravenkeeper/Butler/
   Sailor/Gambler/Godfather/Zombuul/Professor).

3. **PR 3 — Courtier.** Wire Courtier's name → seat lookup and notify
   call. Test: Courtier picks "Goon" with Goon seated → Goon flips +
   Courtier drunkens; Courtier picks a different name → no Goon fire.

4. **PR 4 — Multi-target action (Innkeeper, Shabaloth, Po).** Rewire to
   `process_targets_with_goon_break`. Tests for user scenarios 2, 3, 5, 6.

5. **PR 5 — Multi-target info (Chambermaid, Fortune Teller).** Rewire to
   `notify_goon_chosen_for_any` + post-notify drunk re-read. Test
   user scenario 1.

6. **PR 6 — Assassin.** Reorder kill + notify. Test user scenario 4.

7. **PR 7 — Effect-chain test.** ~~Innkeeper picks `[Other, Goon]`, Goon
   dies the next night, assert Other's SAFE reactivates.~~ Removed in
   the post-PR-7 revision: the Innkeeper switched to a notify-all-
   then-emit shape (see the Innkeeper template above) so its effects
   no longer emit when the Goon is among the picks — there is
   nothing in the registry to deactivate or reactivate. The general
   `resolve_droison_state` cascade behavior is still exercised by
   other character tests (Courtier-droisoned, Poisoner-droisoned,
   Innkeeper-droisoned-via-Poisoner, etc.).
