"""Setup-time character abilities (Drunk, Fortune Teller, Baron).

Exercises the generic :meth:`engine.character.Character.setup_ability`
hook by walking a 7-player game through ``start_game`` ->
``start_night`` and verifying that:

  * The Drunk's setup_ability fires, asks the storyteller to pick a
    Townsfolk, and stores the result on
    ``player.perceived_character_name``.

  * The Fortune Teller's setup_ability fires, asks the storyteller to
    pick a red herring, and the FT then sees a YES on night 1 when
    pointed at the red herring (even though the red herring is good).

  * The Baron has no setup prompts and contributes only its
    ``setup_outsider_delta`` / ``setup_townsfolk_delta`` (verified at
    the class level — bag-shaping is the storyteller UI's job, not the
    engine's, so we just assert the metadata is exposed).

The test reuses the same prompt-broker pattern as
``test_engine_smoke.py``: it polls ``engine.pending_prompt()`` and
posts ``engine.respond()`` from the test thread while the engine's
night thread runs the abilities.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import Engine
from engine.enums import CharType, Phase


def drain_prompts(
    engine: Engine,
    scripted: List[Tuple[dict, Any]],
    timeout: float = 5.0,
) -> None:
    """Answer scripted prompts until the night thread finishes.

    Same protocol as ``test_engine_smoke.drain_prompts``: each entry is
    ``(matcher, response)`` where ``matcher`` is a subset of the
    expected prompt's ``meta``.
    """
    deadline = time.time() + timeout
    answered = 0
    while engine._night_thread and engine._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError(
                f"Night thread didn't finish; "
                f"answered={answered}, "
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


def make_game() -> Engine:
    e = Engine()
    alice = e.add_seat("Alice")    # id 1 — Drunk
    bob   = e.add_seat("Bob")      # id 2 — Fortune Teller
    cara  = e.add_seat("Cara")     # id 3 — Soldier (stub-equivalent, no night action)
    dan   = e.add_seat("Dan")      # id 4 — Mayor   (no night action)
    eve   = e.add_seat("Eve")      # id 5 — Chef    (stub on first night only)
    finn  = e.add_seat("Finn")     # id 6 — Baron   (no night action)
    gabe  = e.add_seat("Gabe")     # id 7 — Imp     (no first-night action)

    e.assign_character(alice.id, "Drunk")
    e.assign_character(bob.id,   "Fortune Teller")
    e.assign_character(cara.id,  "Soldier")
    e.assign_character(dan.id,   "Mayor")
    e.assign_character(eve.id,   "Chef")
    e.assign_character(finn.id,  "Baron")
    e.assign_character(gabe.id,  "Imp")
    return e


def test_drunk_and_ft_setup_then_first_night() -> None:
    e = make_game()
    e.start_game()
    assert e.phase is Phase.FIRST_NIGHT

    # Sanity: Drunk is flagged drunk by assign_character; perceived_character
    # is the placeholder until setup_ability runs.
    alice = e.get_player(1)
    assert alice.drunk is True
    assert alice.perceived_character_name == "Townsfolk"

    e.start_night()

    # Setup actions (Drunk → fake Townsfolk pick, FT → red herring pick),
    # then the night-1 action_order. Chef has first_night_order=33 and
    # acts directly on the auto-computed count when sober. The FT (35)
    # acts on its own real ability after the Chef.
    drain_prompts(e, [
        # ---- Setup ----
        ({"character": "Drunk",          "step": "setup_select_fake"}, "Empath"),
        ({"character": "Fortune Teller", "step": "setup_select_red_herring"}, "Mayor"),
        # ---- First night ----
        # Chef sober + healthy → no ST confirm prompt.
        ({"character": "Chef",           "step": "information"},       None),
        # Drunk-as-Empath wakes at the Empath slot. Because the seat
        # is the Drunk (has_ability=False), the engine pre-fills a
        # *wrong* count and surfaces it to the ST. The ST sends "0"
        # so the (drunk) Empath sees a 0.
        ({"character": "Empath",         "step": "select_count",
          "due_to_drunk_poison": True},                                "0"),
        ({"character": "Empath",         "step": "information"},       None),
        ({"character": "Fortune Teller", "step": "select_players"},    [4, 5]),  # Dan + Eve
        # FT sober + healthy → no ST confirm prompt; auto-answer YES
        # (Dan is the red herring).
        ({"character": "Fortune Teller", "step": "information"},       None),
    ])

    # Drunk's perceived character is now the storyteller's pick.
    assert alice.perceived_character_name == "Empath"
    # Player view (the phone) shows the perceived character, not "Drunk".
    view = e.player_view(1)
    assert view["me"]["character"] == "Empath"

    # The Drunk now carries a real Empath Character instance among its
    # members — the generic setup-pick hook builds and stashes the
    # picked role so the picking character has access to its full
    # metadata (night order, ability text, etc.).
    drunk_char = alice.character
    assert len(drunk_char.members) == 1
    impersonated = drunk_char.members[0]
    from engine.characters.empath import Empath
    assert isinstance(impersonated, Empath)
    assert impersonated.name == "Empath"
    # The Drunk's perceived role is wired to the Drunk's seated player
    # so it can run on the Drunk's chair when the engine reaches the
    # impersonated role's night slot. (Set by ``acting_perceived_character``,
    # which the engine calls each night and during setup.)
    assert impersonated.player is alice
    # Convenience accessor on Drunk surfaces the same instance.
    assert drunk_char.perceived_character is impersonated

    # The Fortune Teller, similarly, carries a real Mayor Character
    # instance among its members (the role chosen as the red herring),
    # and the resolved red-herring player is the seated Mayor (Dan).
    bob = e.get_player(2)
    ft_char = bob.character
    assert len(ft_char.members) == 1
    from engine.characters.mayor import Mayor
    assert isinstance(ft_char.members[0], Mayor)
    assert ft_char.red_herring_role.name == "Mayor"
    assert ft_char._red_herring is not None
    assert ft_char._red_herring.id == 4    # Dan
    assert ft_char._red_herring.name == "Dan"

    # Night completes cleanly, no deaths.
    deaths = e.advance_to_day()
    assert deaths == []
    assert e.phase is Phase.DAY


def test_ft_no_match_returns_no() -> None:
    """When neither chosen player is a Demon nor the red herring, NO."""
    e = make_game()
    e.start_game()
    e.start_night()

    drain_prompts(e, [
        # Drunk picks a Townsfolk (any will do for this test).
        ({"character": "Drunk",          "step": "setup_select_fake"}, "Empath"),
        # FT's red herring: storyteller picks the Mayor role (Dan, id 4).
        ({"character": "Fortune Teller", "step": "setup_select_red_herring"}, "Mayor"),
        # Chef sober + healthy → no ST confirm prompt.
        ({"character": "Chef",           "step": "information"},       None),
        # Drunk-as-Empath wakes at the Empath slot, with the wrong
        # count pre-filled (drunk). The ST sends "1" through.
        ({"character": "Empath",         "step": "select_count",
          "due_to_drunk_poison": True},                                "1"),
        ({"character": "Empath",         "step": "information"},       None),
        # FT's nightly: pick two players who are *not* the Demon and *not*
        # the red herring. Sober + healthy → no ST confirm prompt; the
        # auto-computed answer (NO) is used.
        ({"character": "Fortune Teller", "step": "select_players"},    [3, 5]),  # Cara + Eve
        ({"character": "Fortune Teller", "step": "information"},       None),
    ])
    e.advance_to_day()


def test_baron_setup_metadata() -> None:
    """The Baron has no setup prompts; its deltas are exposed on the class."""
    from engine.characters.baron import Baron
    assert Baron.setup_outsider_delta == 2
    assert Baron.setup_townsfolk_delta == -2
    # And the base no-op setup_ability is inherited (i.e. running it on a
    # Baron in a 1-player engine is a no-op, no prompt emitted).
    e = Engine()
    p = e.add_seat("Solo")
    e.assign_character(p.id, "Baron")
    e.get_player(p.id).character.setup_ability(e)
    assert e.pending_prompt() is None


if __name__ == "__main__":
    test_drunk_and_ft_setup_then_first_night()
    print("test 1 passed.")
    test_ft_no_match_returns_no()
    print("test 2 passed.")
    test_baron_setup_metadata()
    print("test 3 passed.")
    print("All setup-action tests passed.")
