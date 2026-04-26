"""Pre-populated setup picks (Engine.apply_setup_data).

Exercises the new flow where the UI's pre-game picks for the Drunk,
Fortune Teller, and Washerwoman are pushed onto the engine before the
first night runs:

  * The Drunk's setup_ability fast-forwards (no SelectCharacterPrompt
    is emitted) and the Drunk's perceived_character is wired up.
  * The Fortune Teller's setup_ability fast-forwards and its red
    herring is resolved to the seated player.
  * The Washerwoman's nightly ability skips the SelectCharacterPrompt
    when ``_chosen_townsfolk`` is set, auto-derives the seated right
    player, and only asks the storyteller for the *wrong* player to
    point at.
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


def test_apply_setup_data_pre_populates_drunk_ft_ww() -> None:
    e = Engine()
    a = e.add_seat("Alice")    # 1 — Washerwoman
    b = e.add_seat("Bob")      # 2 — Empath  (also: WW's seen TF)
    c = e.add_seat("Cara")     # 3 — Drunk (thinks they are Investigator)
    d = e.add_seat("Dan")      # 4 — Mayor (FT's red herring)
    f = e.add_seat("Eve")      # 5 — Fortune Teller
    g = e.add_seat("Finn")     # 6 — Poisoner
    h = e.add_seat("Gabe")     # 7 — Imp

    e.assign_character(a.id, "Washerwoman")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Drunk")
    e.assign_character(d.id, "Mayor")
    e.assign_character(f.id, "Fortune Teller")
    e.assign_character(g.id, "Poisoner")
    e.assign_character(h.id, "Imp")

    # Push UI picks before start_game, mirroring what the new
    # /api/engine/start_game handler does.
    e.apply_setup_data({
        "drunk_fake": "Investigator",
        "ft_red_herring": "Mayor",
        "washerwoman_townsfolk": "Empath",
    })

    # Verify the engine wired everything up.
    cara = e.get_player(c.id)
    drunk_char = cara.character
    assert len(drunk_char.members) == 1, "Drunk should carry the impersonated role"
    assert drunk_char.members[0].name == "Investigator"
    assert cara.perceived_character_name == "Investigator"

    eve = e.get_player(f.id)
    ft_char = eve.character
    assert len(ft_char.members) == 1, "FT should carry the red-herring role"
    assert ft_char.members[0].name == "Mayor"
    # The seated red-herring player is Dan (id 4), the Mayor.
    assert ft_char._red_herring is not None
    assert ft_char._red_herring.id == d.id

    alice = e.get_player(a.id)
    ww_char = alice.character
    assert ww_char._chosen_townsfolk == "Empath"

    e.start_game()
    assert e.phase is Phase.FIRST_NIGHT

    # Drive the first night. Notably:
    #   * No prompts for the Drunk's pretend role (already set).
    #   * No prompts for the FT's red herring (already set).
    #   * The Washerwoman skips the SelectCharacterPrompt and only
    #     asks for the wrong player.
    e.start_night()
    drain(e, [
        # Poisoner first (order 10). Poison Finn (self) for simplicity
        # so no other character is poisoned.
        ({"character": "Poisoner",     "step": "select_player"}, g.id),
        # Washerwoman (order 30): skip SelectCharacterPrompt → straight
        # to the wrong-player prompt. Eligible = {Cara, Dan, Finn,
        # Gabe} (Alice excluded as self, Bob excluded as right player).
        ({"character": "Washerwoman",  "step": "select_wrong_player",
          "shown_character": "Empath",
          "right_player_id": b.id,
          "right_player_name": "Bob"},                            d.id),
        ({"character": "Washerwoman",  "step": "information"},   None),
        # Drunk-as-Investigator (order 32 → Investigator slot). Cara is
        # the Drunk, so the Investigator ability runs through its
        # drunk/poisoned branch: ST picks the (wrong) Minion to show
        # and the two players to point at.
        ({"character": "Investigator", "step": "select_character",
          "due_to_drunk_poison": True},                           "Spy"),
        ({"character": "Investigator", "step": "select_players",
          "due_to_drunk_poison": True,
          "shown_character": "Spy"},                              [d.id, h.id]),
        ({"character": "Investigator", "step": "information"},   None),
        # Empath (order 34): sober + healthy → no ST confirm prompt.
        ({"character": "Empath",       "step": "information"},   None),
        # Fortune Teller (order 35): pre-set red herring; sober +
        # healthy → no ST confirm prompt; auto-answer is YES because
        # Dan is the red herring.
        ({"character": "Fortune Teller", "step": "select_players"},
                                                                  [d.id, h.id]),
        ({"character": "Fortune Teller", "step": "information"}, None),
    ])

    deaths = e.advance_to_day()
    assert deaths == []
    assert e.phase is Phase.DAY


if __name__ == "__main__":
    test_apply_setup_data_pre_populates_drunk_ft_ww()
    print("setup-data test passed.")
