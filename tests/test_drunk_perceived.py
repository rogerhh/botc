"""The Drunk's *perceived* character runs its ability on the Drunk's seat.

Per :doc:`CLAUDE.md`, when a player is the Drunk and believes they are
some Townsfolk, the storyteller should still walk through the
*Townsfolk's* ability — but with wrong-info pre-fills surfaced for the
ST to dispatch on Next.

This module exercises the engine path that:

  * Runs the perceived character's ``setup_ability`` at setup time
    (Drunk-as-Fortune-Teller picks a red herring).
  * Wakes the Drunk at the perceived character's slot in the night
    sheet (Drunk-as-Empath wakes at Empath's order, sees a
    drunk/poisoned-pre-filled count).
  * Dispatches reactions to the perceived character so role-specific
    bookkeeping (Drunk-as-Undertaker tracking executions) keeps
    running on the Drunk's chair.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import Engine
from engine.enums import Phase


def drain(engine: Engine, scripted: List[Tuple[dict, Any]],
          timeout: float = 5.0) -> None:
    """Mini broker — answer prompts in order; assert no extras."""
    deadline = time.time() + timeout
    answered = 0
    while engine._night_thread and engine._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError(
                f"Night didn't finish; answered={answered}, "
                f"pending={engine.pending_prompt()}"
            )
        p = engine.pending_prompt()
        if p is None:
            time.sleep(0.01)
            continue
        if answered >= len(scripted):
            raise AssertionError(
                f"Unexpected extra prompt: {p.text!r} meta={p.meta}"
            )
        matcher, response = scripted[answered]
        for k, v in matcher.items():
            if p.meta.get(k) != v:
                raise AssertionError(
                    f"Prompt #{answered+1} did not match: "
                    f"expected meta[{k!r}]={v!r}, got meta={p.meta}, "
                    f"text={p.text!r}"
                )
        engine.respond(p.id, response)
        answered += 1
        time.sleep(0.01)
    if answered != len(scripted):
        raise AssertionError(
            f"Night ended with {answered} answered, expected {len(scripted)}."
        )


def test_drunk_as_empath_runs_drunk_empath_ability() -> None:
    """Drunk-as-Empath: ST sees the wrong-count pre-fill and dispatches it."""
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Drunk (thinks they're the Empath)
    b = e.add_seat("Bob")      # 2 — Soldier  (good neighbour, no ability)
    c = e.add_seat("Cara")     # 3 — Mayor    (good neighbour)
    d = e.add_seat("Dan")      # 4 — Poisoner (evil; not a neighbour)
    f = e.add_seat("Eve")      # 5 — Imp      (evil)
    e.assign_character(a.id, "Drunk")
    e.assign_character(b.id, "Soldier")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    # Pre-set the Drunk's perceived role via the UI's setup data path.
    e.apply_setup_data({"drunk_fake": "Empath"})

    e.start_game()
    assert e.phase is Phase.FIRST_NIGHT

    drunk_char = e.get_player(a.id).character
    assert drunk_char.name == "Drunk"
    assert drunk_char.members and drunk_char.members[0].name == "Empath"

    e.start_night()
    drain(e, [
        # Poisoner picks themselves so no other character is poisoned —
        # we want to isolate the *drunk* path on Alice.
        ({"character": "Poisoner",  "step": "select_player"}, d.id),
        # Drunk-as-Empath wakes at the Empath slot. ``has_ability`` is
        # False for the Drunk's seat, so the engine pre-picks a wrong
        # count and prompts the ST. The two alive neighbours are
        # Bob (Soldier, good) and Eve (Imp, evil), so the *true* count
        # is 1 — the engine pre-fills 0 or 2. Send "2" to make sure the
        # ST's response wins.
        ({"character": "Empath",    "step": "select_count",
          "due_to_drunk_poison": True}, "2"),
        ({"character": "Empath",    "step": "information"}, None),
        # No FT, no Butler, no Imp action on first night. Done.
    ])

    # The perceived Empath instance is wired to the Drunk's seated
    # player so its self.player checks all resolve to Alice's chair.
    perceived = drunk_char.members[0]
    assert perceived.player is e.get_player(a.id)

    deaths = e.advance_to_day()
    assert deaths == []
    assert e.phase is Phase.DAY


def test_drunk_as_fortune_teller_picks_red_herring_at_setup() -> None:
    """Drunk-as-FT fills the red-herring slot with the None placeholder.

    Per :doc:`CLAUDE.md` and project rules: when the Drunk impersonates
    a Townsfolk that needs a setup-time character pick (e.g. the
    Fortune Teller's red herring), the engine fills the slot with the
    no-op :class:`NoneCharacter` placeholder rather than prompting the
    storyteller. The reminder token is not placed since the Drunk has
    no real ability — this just spares the ST an unneeded input.
    """
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Drunk (thinks they're the FT)
    b = e.add_seat("Bob")      # 2 — Soldier
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp
    e.assign_character(a.id, "Drunk")
    e.assign_character(b.id, "Soldier")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")

    # The Drunk's pretend role is the Fortune Teller — picked via the
    # UI setup-data path so the Drunk's own setup_ability fast-forwards.
    # The FT-as-Drunk's red-herring pick is NOT pre-populated (no
    # ft_red_herring key); the engine should auto-pick rather than
    # prompting the storyteller (per CLAUDE.md / project rules).
    e.apply_setup_data({"drunk_fake": "Fortune Teller"})

    e.start_game()
    e.start_night()

    drain(e, [
        # No setup_select_red_herring prompt — the perceived FT slots
        # in the None placeholder because it's running on a Drunk's
        # chair.
        # ---- First night ----
        # Poisoner self-poisons.
        ({"character": "Poisoner",  "step": "select_player"}, d.id),
        # Drunk-as-FT acts at the FT slot. has_ability=False → drunk
        # branch of select-2-and-pre-fill-flipped-yes-no. The ST picks
        # Bob and Cara to point at; the engine's drunk path then asks
        # the ST for the answer with the *flipped* default highlighted.
        ({"character": "Fortune Teller",
          "step": "select_players"}, [b.id, c.id]),
        ({"character": "Fortune Teller",
          "step": "select_yes_no",
          "due_to_drunk_poison": True}, False),
        ({"character": "Fortune Teller",
          "step": "information"}, None),
    ])

    # The perceived FT carries the None placeholder as its red-herring
    # role. No seated player has character.name == "None", so the
    # resolved red-herring player is None — which is exactly what we
    # want for a Drunk-as-FT (the FT's nightly read collapses to
    # "demon-only" before the drunk/poison flip kicks in).
    perceived_ft = e.get_player(a.id).character.members[0]
    assert perceived_ft.name == "Fortune Teller"
    assert perceived_ft.red_herring_role is not None
    assert perceived_ft.red_herring_role.name == "None"
    assert perceived_ft._red_herring is None


if __name__ == "__main__":
    test_drunk_as_empath_runs_drunk_empath_ability()
    print("drunk-as-empath test passed.")
    test_drunk_as_fortune_teller_picks_red_herring_at_setup()
    print("drunk-as-ft test passed.")
    print("All drunk-perceived tests passed.")
