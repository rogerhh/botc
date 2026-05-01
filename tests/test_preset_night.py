"""Preset-driven night ordering + auto-advance to day.

Boots a 5-player engine with the trouble_brewing preset and walks the
engine's night sheet. Confirms that:

  * The engine emits one preset_step prompt per character or
    Dusk/Dawn entry.
  * Characters in the preset that aren't seated are skipped silently.
  * After the night sheet is exhausted, the engine auto-advances to
    DAY (because ``set_auto_advance_to_day(True)`` was called).
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import Engine
from engine.enums import Phase
from engine import preset as preset_module


def make_engine() -> Engine:
    e = Engine()
    p = preset_module.load_preset(
        preset_module.default_presets_root(), "trouble_brewing"
    )
    assert p is not None, "trouble_brewing preset should load"
    e.set_preset(p)
    e.set_auto_advance_to_day(True)
    a = e.add_seat("Alice")    # 1 — Washerwoman
    b = e.add_seat("Bob")      # 2 — Empath
    c = e.add_seat("Cara")     # 3 — Soldier
    d = e.add_seat("Dan")      # 4 — Poisoner
    f = e.add_seat("Eve")      # 5 — Imp
    e.assign_character(a.id, "Washerwoman")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Soldier")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")
    return e


def drain(engine: Engine, scripted: List[Tuple[dict, Any]],
          timeout: float = 8.0) -> None:
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


def test_first_night_preset_order_and_auto_dawn() -> None:
    e = make_engine()
    e.start_game()
    assert e.phase is Phase.FIRST_NIGHT

    # Project rule: Minion Info and Demon Info always run, regardless
    # of player count (deliberately diverging from the canonical 7+
    # threshold). For this 5-player Trouble Brewing game the preset
    # order is:
    #
    #   Dusk → Minion Info → Demon Info (st_pre + info) → Poisoner
    #   → Washerwoman → Empath → Dawn
    #
    # Characters that aren't seated (Librarian, Investigator, Chef,
    # Fortune Teller, Butler, Spy) are silently skipped.

    e.start_night()
    drain(e, [
        # Dusk announce — storyteller-only InformationPrompt (Dawn/Dusk
        # still emit prompts because there's no follow-up ability to
        # absorb the rulebook line).
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        # Minion Info: one consolidated prompt that wakes every Minion
        # together (just Poisoner here).
        ({"step_kind": "minion_info"}, None),
        # Demon Info: ST stage 1 (bluffs), then the auto-info to player.
        # Respond with None so the engine keeps its random defaults.
        ({"step_kind": "demon_info", "stage": "st_pre"}, None),
        ({"step_kind": "demon_info", "stage": "info"}, None),
        # Poisoner: no announce prompt — character steps roll the
        # rulebook description into the first ability prompt's meta
        # (engine._announce_step now dispatches a STEP_START event
        # instead of blocking the storyteller).
        ({"character": "Poisoner", "step": "select_player"}, 4),
        # Washerwoman: same — sober WW only needs select_character +
        # select_wrong_player + information.
        ({"character": "Washerwoman", "step": "select_character"}, "Empath"),
        ({"character": "Washerwoman", "step": "select_wrong_player"}, 3),
        ({"character": "Washerwoman", "step": "information"}, None),
        # Empath: sober + healthy → no ST count prompt; just the
        # information prompt fires.
        ({"character": "Empath", "step": "information"}, None),
        # Dawn announce.
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])

    # Auto-advance should have happened — engine is in DAY now.
    deadline = time.time() + 2.0
    while e.phase is Phase.FIRST_NIGHT and time.time() < deadline:
        time.sleep(0.02)
    assert e.phase is Phase.DAY, f"expected DAY, got {e.phase.value}"
    assert e.day_number == 1


def test_pending_win_night_runs_only_dusk_and_dawn() -> None:
    """When a win is pending, the next night should still emit the Dusk
    and Dawn announcements but skip every ability / Minion Info /
    Demon Info / setup-action.

    Walk the engine through to the day, register a pending win
    directly, then run one more night with the preset still in place.
    Only the Dusk and Dawn preset_step prompts should appear.
    """
    e = make_engine()
    e.start_game()

    # Drain the first night normally so we land in DAY. Minion Info
    # and Demon Info fire even at 5 players (project rule).
    e.start_night()
    drain(e, [
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        ({"step_kind": "minion_info"}, None),
        ({"step_kind": "demon_info", "stage": "st_pre"}, None),
        ({"step_kind": "demon_info", "stage": "info"}, None),
        ({"character": "Poisoner", "step": "select_player"}, 4),
        ({"character": "Washerwoman", "step": "select_character"}, "Empath"),
        ({"character": "Washerwoman", "step": "select_wrong_player"}, 3),
        ({"character": "Washerwoman", "step": "information"}, None),
        ({"character": "Empath", "step": "information"}, None),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])
    deadline = time.time() + 2.0
    while e.phase is Phase.FIRST_NIGHT and time.time() < deadline:
        time.sleep(0.02)
    assert e.phase is Phase.DAY

    # Day-time win trigger. Use the engine helper directly so we don't
    # have to seat a Saint specifically — this test is about the night
    # behavior, not the trigger.
    from engine.enums import Alignment
    e._register_pending_win(Alignment.GOOD, "Test trigger")
    assert e.pending_winner is Alignment.GOOD
    assert e.phase is Phase.DAY, "Day phase should not change on pending."

    # Advance to night and run it. The night must produce ONLY the
    # Dusk and Dawn preset announcements — no abilities, no
    # Minion/Demon info, no character prompts. Then auto-dawn
    # finalizes the win.
    e.advance_to_night()
    assert e.phase is Phase.NIGHT
    e.start_night()
    drain(e, [
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])

    deadline = time.time() + 2.0
    while e.phase is Phase.NIGHT and time.time() < deadline:
        time.sleep(0.02)

    assert e.phase is Phase.FINISHED, (
        f"Game must end at dawn after the no-action night; got {e.phase}."
    )
    assert e.winner is Alignment.GOOD
    assert e.win_reason == "Test trigger"


def test_scarlet_woman_promotion_reveal_at_next_night() -> None:
    """When the SW reaction promotes a player to the Demon, the next
    night's "Scarlet Woman" preset step must run the YOU-ARE / Demon-
    token reveals — even though the seated player's character has been
    changed to the Demon class (so the generic
    ``in_play.get('Scarlet Woman')`` lookup would otherwise miss).

    Also verifies that ``_sw_promoted_player_ids`` (the persistent
    grimoire reminder list) and ``_sw_pending_demon_reveal`` (the
    queue drained by the night step) are populated by the reaction
    and that the queue is cleared once the reveal runs.
    """
    e = Engine()
    p = preset_module.load_preset(
        preset_module.default_presets_root(), "trouble_brewing"
    )
    assert p is not None
    e.set_preset(p)
    e.set_auto_advance_to_day(True)
    # 7 players so the SW takeover threshold (5+ alive at demon death)
    # is met after the kill, and so Minion Info / Demon Info both fire.
    a = e.add_seat("Alice")    # 1 — Soldier
    b = e.add_seat("Bob")      # 2 — Empath
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Chef
    f = e.add_seat("Eve")      # 5 — Poisoner (Minion)
    g = e.add_seat("Fay")      # 6 — Scarlet Woman (Minion)
    h = e.add_seat("Gus")      # 7 — Imp (Demon)
    e.assign_character(a.id, "Soldier")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Chef")
    e.assign_character(f.id, "Poisoner")
    e.assign_character(g.id, "Scarlet Woman")
    e.assign_character(h.id, "Imp")
    e.start_game()

    sw_pid = g.id
    imp_pid = h.id

    # Drain first night. With 7 non-traveler players, Minion Info and
    # Demon Info both fire. Don't-care responses for the Demon Info
    # stage 1 (st_pre bluff selection) — accept the random default by
    # responding with None so the engine falls back to its picks.
    e.start_night()
    drain(e, [
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        # Minion Info: one consolidated prompt that wakes every Minion
        # together (Poisoner + SW are both woken at once).
        ({"step_kind": "minion_info"}, None),
        # Demon Info: ST stage 1 (bluffs), then the auto-info to player.
        ({"step_kind": "demon_info", "stage": "st_pre"}, None),
        ({"step_kind": "demon_info", "stage": "info"}, None),
        # Poisoner picks Cara (no relevance).
        ({"character": "Poisoner", "step": "select_player"}, c.id),
        # Chef sober + healthy → auto information.
        ({"character": "Chef", "step": "information"}, None),
        # Empath sober + healthy → auto information.
        ({"character": "Empath", "step": "information"}, None),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])
    deadline = time.time() + 2.0
    while e.phase is Phase.FIRST_NIGHT and time.time() < deadline:
        time.sleep(0.02)
    assert e.phase is Phase.DAY

    # Storyteller kills the Imp during the day. With 7 players still
    # alive (including the Imp at the moment of death), alive_before
    # is 7 ≥ 5 so the Scarlet Woman reaction promotes Fay to the Imp.
    from engine.enums import DeathCause
    e.kill(imp_pid, DeathCause.STORYTELLER)
    assert sw_pid in e._sw_promoted_player_ids, (
        "SW reaction should have recorded the promoted player id."
    )
    assert sw_pid in e._sw_pending_demon_reveal, (
        "SW reaction should have queued the demon-token reveal."
    )
    fay = e.get_player(sw_pid)
    assert fay.character is not None
    assert fay.character.name == "Imp", (
        "Fay should now be the Imp (the actual dying demon's class)."
    )

    # Night 2: the engine's SW step should fire the consolidated
    # "YOU ARE the <Demon>" reveal. No DEMON INFO is run for the SW
    # promotion (per project request and the trouble-brewing night
    # sheet wording).
    e.advance_to_night()
    e.start_night()
    drain(e, [
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        # No Minion Info or Demon Info on subsequent nights.
        # Poisoner: pick Cara again — keeps her ability suppressed so
        # the Imp's kill on her below doesn't trigger Mayor redirect.
        ({"character": "Poisoner", "step": "select_player"}, c.id),
        # Scarlet Woman step: announce + 1 InformationPrompt — the
        # consolidated "YOU ARE the <Demon>" reveal targeting the
        # freshly-promoted player.
        ({"step_kind": "preset_step", "step_name": "Scarlet Woman"}, None),
        ({"step_kind": "scarlet_woman_reveal", "reveal": "demon_role",
          "demon_character": "Imp"}, None),
        # Imp (now Fay) picks Cara. Cara is poisoned, so the Mayor
        # death-redirect ability is gated off — no redirect prompt.
        ({"character": "Imp", "step": "select_target"}, c.id),
        # Empath sober + healthy → auto information.
        ({"character": "Empath", "step": "information"}, None),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])
    deadline = time.time() + 2.0
    while e.phase is Phase.NIGHT and time.time() < deadline:
        time.sleep(0.02)
    assert e.phase is Phase.DAY
    # Reveal queue cleared, persistent reminder list still populated.
    assert e._sw_pending_demon_reveal == [], (
        "Demon-reveal queue should have drained at the SW night step."
    )
    assert sw_pid in e._sw_promoted_player_ids, (
        "Persistent reminder list should still record the promotion."
    )


def test_imp_self_kill_promotes_regular_minion_with_reveal() -> None:
    """When the Imp self-kills, the Storyteller picks any alive Minion
    to take over (no Scarlet Woman special-casing). The picked seat's
    character is changed to "Imp" via :meth:`Engine.change_character`
    and the new Imp is woken THIS NIGHT and shown the consolidated
    "YOU ARE the Imp." reveal. The pending "Demon is dead" win that
    ``engine.kill`` registered between the death and the promotion
    must be retracted. Because the chair role was the Poisoner (not
    the Scarlet Woman), nothing is added to ``_sw_promoted_player_ids``
    and the next-night "Scarlet Woman" preset step is silent.
    """
    e = Engine()
    p = preset_module.load_preset(
        preset_module.default_presets_root(), "trouble_brewing"
    )
    assert p is not None
    e.set_preset(p)
    e.set_auto_advance_to_day(True)
    # 7 players, two Minions (Poisoner + Baron), no Scarlet Woman, so
    # ``_handle_self_kill`` actually has a real choice and fires the
    # ``select_new_imp`` prompt (single-eligible would auto-resolve).
    # Baron has no nightly action, keeping the night sheet noise low.
    a = e.add_seat("Alice")    # 1 — Soldier
    b = e.add_seat("Bob")      # 2 — Empath
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Chef
    f = e.add_seat("Eve")      # 5 — Poisoner (Minion #1)
    g = e.add_seat("Fay")      # 6 — Baron (Minion #2)
    h = e.add_seat("Gus")      # 7 — Imp
    e.assign_character(a.id, "Soldier")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Chef")
    e.assign_character(f.id, "Poisoner")
    e.assign_character(g.id, "Baron")
    e.assign_character(h.id, "Imp")
    e.start_game()

    poisoner_pid = f.id
    imp_pid = h.id

    # Drain first night. With 7 non-traveler players, Minion Info and
    # Demon Info fire.
    e.start_night()
    drain(e, [
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        # Minion Info: one consolidated prompt that wakes every Minion
        # together (Poisoner + Baron are both woken at once).
        ({"step_kind": "minion_info"}, None),
        # Demon Info: ST stage 1 (bluffs) then auto-info.
        ({"step_kind": "demon_info", "stage": "st_pre"}, None),
        ({"step_kind": "demon_info", "stage": "info"}, None),
        # Poisoner picks Alice (no relevance — keeping Cara healthy
        # so the Mayor death-redirect works as expected).
        ({"character": "Poisoner", "step": "select_player"}, a.id),
        # Chef sober + healthy → auto information.
        ({"character": "Chef", "step": "information"}, None),
        # Empath sober + healthy → auto information.
        ({"character": "Empath", "step": "information"}, None),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])
    deadline = time.time() + 2.0
    while e.phase is Phase.FIRST_NIGHT and time.time() < deadline:
        time.sleep(0.02)
    assert e.phase is Phase.DAY

    # Pre-condition: no promotion yet.
    assert e._sw_promoted_player_ids == []
    assert e._sw_pending_demon_reveal == []

    # Night 2: the Imp picks themselves. With no Scarlet Woman in
    # play, the SW reaction does not fire. ``_handle_self_kill``
    # therefore prompts the storyteller to pick a Minion — pick the
    # Poisoner — and then immediately wakes the new Imp and shows
    # the consolidated "YOU ARE the Imp." reveal THIS night.
    e.advance_to_night()
    e.start_night()
    drain(e, [
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        # Poisoner picks Alice again.
        ({"character": "Poisoner", "step": "select_player"}, a.id),
        # Imp self-kills.
        ({"character": "Imp", "step": "select_target"}, imp_pid),
        # Storyteller picks the new Imp from the alive Minions.
        ({"character": "Imp", "step": "select_new_imp",
          "stage": "st_post"}, poisoner_pid),
        # Inline "YOU ARE the Imp." reveal (same night) — wakes the
        # new Imp and shows them their new role. No DEMON INFO.
        ({"step_kind": "imp_self_kill_reveal", "reveal": "demon_role",
          "demon_character": "Imp"}, None),
        # Empath sober + healthy → auto information.
        ({"character": "Empath", "step": "information"}, None),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])
    deadline = time.time() + 2.0
    while e.phase is Phase.NIGHT and time.time() < deadline:
        time.sleep(0.02)
    assert e.phase is Phase.DAY

    # Bookkeeping.
    poisoner_player = e.get_player(poisoner_pid)
    assert poisoner_player.character is not None
    assert poisoner_player.character.name == "Imp", (
        "Poisoner should now be a fresh Imp instance."
    )
    # Chair role was Poisoner — no Scarlet Woman grimoire reminder.
    assert poisoner_pid not in e._sw_promoted_player_ids, (
        "Promoting a non-SW seat must NOT add to the SW reminder list."
    )
    # Reveal happened inline this same night — nothing queued for the
    # next night.
    assert poisoner_pid not in e._sw_pending_demon_reveal, (
        "Imp self-kill reveal happens inline; nothing should be queued."
    )
    assert e._sw_pending_demon_reveal == [], (
        "Imp self-kill must not queue a next-night reveal."
    )
    # The transient "Demon is dead" pending win must have been retracted
    # (a new Demon is alive again).
    assert e.pending_winner is None, (
        "Imp self-kill promotion must clear the pending good win."
    )

    # Night 3: the "Scarlet Woman" preset step is silent because the
    # reveal queue is empty (nothing was queued — the reveal already
    # happened inline on night 2). The new Imp (Eve) takes a normal
    # kill.
    e.advance_to_night()
    e.start_night()
    drain(e, [
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        # The "Scarlet Woman" preset step is skipped: the queue is
        # empty, so no announce + no reveal prompt. (Compare the SW
        # promotion-from-execution test where the queue IS populated
        # and the announce+reveal pair fires.)
        # Eve (the new Imp / former Poisoner) kills the Empath.
        ({"character": "Imp", "step": "select_target"}, b.id),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])
    deadline = time.time() + 2.0
    while e.phase is Phase.NIGHT and time.time() < deadline:
        time.sleep(0.02)
    assert e.phase is Phase.DAY
    assert e._sw_pending_demon_reveal == []
    assert poisoner_pid not in e._sw_promoted_player_ids


def test_imp_self_kill_with_sw_only_minion_auto_resolves() -> None:
    """When the Imp self-kills and the only alive Minion is the
    Scarlet Woman, the storyteller's pick auto-resolves (single
    eligible option). The reveal still happens inline THIS night
    and, because the chair role is "Scarlet Woman", the grimoire
    reminder list records the promotion. Nothing is queued for the
    next night.

    Arrange a 4-player scenario at the moment of Imp self-kill (so
    alive_before = 4 — the SW reaction's >=5 guard would reject
    even if it weren't being suppressed by the self-kill flag). The
    Storyteller is "asked" to pick from the only eligible Minion
    (the SW), which auto-resolves.
    """
    e = Engine()
    p = preset_module.load_preset(
        preset_module.default_presets_root(), "trouble_brewing"
    )
    assert p is not None
    e.set_preset(p)
    e.set_auto_advance_to_day(True)
    # 5 seats; we'll execute one player on day 1 to drop alive_before
    # to 4 at the moment of the Imp's self-kill.
    a = e.add_seat("Alice")    # Soldier
    b = e.add_seat("Bob")      # Empath
    c = e.add_seat("Cara")     # Mayor
    d = e.add_seat("Dan")      # Scarlet Woman
    f = e.add_seat("Eve")      # Imp
    e.assign_character(a.id, "Soldier")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Scarlet Woman")
    e.assign_character(f.id, "Imp")
    e.start_game()

    sw_pid = d.id
    imp_pid = f.id

    # Drain first night. Minion Info / Demon Info fire even at 5p
    # (project rule).
    e.start_night()
    drain(e, [
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        ({"step_kind": "minion_info"}, None),
        ({"step_kind": "demon_info", "stage": "st_pre"}, None),
        ({"step_kind": "demon_info", "stage": "info"}, None),
        ({"character": "Empath", "step": "information"}, None),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])
    deadline = time.time() + 2.0
    while e.phase is Phase.FIRST_NIGHT and time.time() < deadline:
        time.sleep(0.02)
    assert e.phase is Phase.DAY

    # Storyteller-kill someone harmless during day 1 so alive_before
    # is 4 (not 5) at the moment of the night-2 self-kill.
    from engine.enums import DeathCause
    e.kill(a.id, DeathCause.STORYTELLER)
    assert not e.get_player(a.id).alive
    # Sanity: SW must be alive and have ability (not drunk/poisoned).
    sw_player = e.get_player(sw_pid)
    assert sw_player.alive
    assert sw_player.has_ability

    # Night 2: Imp self-kills. The SW reaction does not promote here
    # (alive_before is 4, below the >=5 threshold), so the deferred
    # post-DEATH self-kill handler sees no new Demon yet and prompts
    # the storyteller to pick a Minion. The only alive Minion is the
    # SW, so the ``select_new_imp`` prompt auto-resolves to her. The
    # inline "YOU ARE the Imp." reveal then fires.
    e.advance_to_night()
    e.start_night()
    drain(e, [
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        # Imp self-kills. select_new_imp auto-resolves (single
        # eligible: the Scarlet Woman).
        ({"character": "Imp", "step": "select_target"}, imp_pid),
        # Inline "YOU ARE the Imp." reveal targeting the SW.
        ({"step_kind": "imp_self_kill_reveal", "reveal": "demon_role",
          "demon_character": "Imp"}, None),
        ({"character": "Empath", "step": "information"}, None),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])
    deadline = time.time() + 2.0
    while e.phase is Phase.NIGHT and time.time() < deadline:
        time.sleep(0.02)
    assert e.phase is Phase.DAY

    # SW seat now holds a fresh Imp instance.
    sw_player = e.get_player(sw_pid)
    assert sw_player.character is not None
    assert sw_player.character.name == "Imp"
    # Chair role was "Scarlet Woman" — grimoire reminder is recorded.
    assert sw_pid in e._sw_promoted_player_ids, (
        "When the picked seat's chair role is Scarlet Woman, the "
        "promotion must be recorded for the IS THE DEMON reminder."
    )
    # No next-night reveal queued (the inline reveal already ran).
    assert sw_pid not in e._sw_pending_demon_reveal
    assert e._sw_pending_demon_reveal == []


def test_imp_self_kill_5plus_alive_sw_must_promote() -> None:
    """Per the Scarlet Woman wiki: "If five or more players are alive
    when the Imp kills themself at night, the Scarlet Woman must
    become the new Imp." The Storyteller is NOT prompted in this
    case — the SW is auto-picked. The reveal happens inline this
    same night and the grimoire reminder is recorded.
    """
    e = Engine()
    p = preset_module.load_preset(
        preset_module.default_presets_root(), "trouble_brewing"
    )
    assert p is not None
    e.set_preset(p)
    e.set_auto_advance_to_day(True)
    # 7 players: Soldier, Empath, Mayor, Chef, Poisoner (a second
    # Minion so SW is NOT the only eligible — proves we're hitting
    # the wiki-rule auto-pick, not a single-eligible auto-resolve),
    # Scarlet Woman, Imp.
    a = e.add_seat("Alice")    # 1 — Soldier
    b = e.add_seat("Bob")      # 2 — Empath
    c = e.add_seat("Cara")     # 3 — Mayor
    d = e.add_seat("Dan")      # 4 — Chef
    f = e.add_seat("Eve")      # 5 — Poisoner (Minion #1)
    g = e.add_seat("Fay")      # 6 — Scarlet Woman (Minion #2)
    h = e.add_seat("Gus")      # 7 — Imp
    e.assign_character(a.id, "Soldier")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Chef")
    e.assign_character(f.id, "Poisoner")
    e.assign_character(g.id, "Scarlet Woman")
    e.assign_character(h.id, "Imp")
    e.start_game()

    sw_pid = g.id
    imp_pid = h.id

    # Drain first night with 7 players (Minion + Demon Info fire).
    e.start_night()
    drain(e, [
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        # Consolidated minion info: one prompt for every Minion at once.
        ({"step_kind": "minion_info"}, None),
        ({"step_kind": "demon_info", "stage": "st_pre"}, None),
        ({"step_kind": "demon_info", "stage": "info"}, None),
        ({"character": "Poisoner", "step": "select_player"}, a.id),
        ({"character": "Chef", "step": "information"}, None),
        ({"character": "Empath", "step": "information"}, None),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])
    deadline = time.time() + 2.0
    while e.phase is Phase.FIRST_NIGHT and time.time() < deadline:
        time.sleep(0.02)
    assert e.phase is Phase.DAY

    # Sanity: SW alive and healthy.
    sw_player = e.get_player(sw_pid)
    assert sw_player.alive and sw_player.has_ability

    # Night 2: Imp self-kills with 7 alive (alive_before = 7 ≥ 5).
    # Per the wiki rule, the Scarlet Woman MUST become the new Imp —
    # no select_new_imp prompt.
    e.advance_to_night()
    e.start_night()
    drain(e, [
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        ({"character": "Poisoner", "step": "select_player"}, a.id),
        # Imp self-kills — no select_new_imp prompt; SW auto-promotes
        # via the wiki rule.
        ({"character": "Imp", "step": "select_target"}, imp_pid),
        # Inline "YOU ARE the Imp." reveal targeting the SW.
        ({"step_kind": "imp_self_kill_reveal", "reveal": "demon_role",
          "demon_character": "Imp"}, None),
        ({"character": "Empath", "step": "information"}, None),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])
    deadline = time.time() + 2.0
    while e.phase is Phase.NIGHT and time.time() < deadline:
        time.sleep(0.02)
    assert e.phase is Phase.DAY

    # SW seat is now the Imp.
    sw_player = e.get_player(sw_pid)
    assert sw_player.character is not None
    assert sw_player.character.name == "Imp"
    # Grimoire reminder recorded; no next-night reveal queued.
    assert sw_pid in e._sw_promoted_player_ids
    assert e._sw_pending_demon_reveal == []
    # Pending good win retracted (a new Demon is alive).
    assert e.pending_winner is None


def test_change_character_syncs_chair_and_drops_old_ability() -> None:
    """``Engine.change_character`` is the single mutation point for a
    seat's role. It must (a) update ``chair.character`` so the player
    circle reflects the new role (no stale "two copies" between
    ``chair.character`` and ``player.character``) and (b) replace the
    Player's ``character`` so the old Minion's ability is gone (the
    Poisoner step is silently skipped on the night following the
    promotion — the now-Imp seat doesn't fire the Poisoner ability).
    """
    e = Engine()
    p = preset_module.load_preset(
        preset_module.default_presets_root(), "trouble_brewing"
    )
    assert p is not None
    e.set_preset(p)
    e.set_auto_advance_to_day(True)
    # Build chairs WITH chair.character populated (mirrors what
    # _sync_chairs_to_engine does on start_game).
    a = e.add_seat("Alice")    # Soldier
    b = e.add_seat("Bob")      # Empath
    c = e.add_seat("Cara")     # Mayor
    d = e.add_seat("Dan")      # Poisoner (Minion — pre-promotion)
    f = e.add_seat("Eve")      # Imp
    e.assign_character(a.id, "Soldier")
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Imp")
    # Wire up chair.character + chair.player_id like
    # _sync_chairs_to_engine would (the engine API used in pure-engine
    # tests doesn't touch the chair store on its own).
    chairs_in_order = sorted(e.chairs.list(), key=lambda x: x["id"])
    for chair, player in zip(chairs_in_order, [a, b, c, d, f]):
        e.chairs.update(
            chair["id"],
            name=player.name,
            character=player.character.name,
            player_id=player.id,
        )
    e.start_game()

    poisoner_pid = d.id
    imp_pid = f.id

    # Drain first night to land in DAY. Minion Info / Demon Info fire
    # even at 5p (project rule).
    e.start_night()
    drain(e, [
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        ({"step_kind": "minion_info"}, None),
        ({"step_kind": "demon_info", "stage": "st_pre"}, None),
        ({"step_kind": "demon_info", "stage": "info"}, None),
        ({"character": "Poisoner", "step": "select_player"}, a.id),
        ({"character": "Empath", "step": "information"}, None),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])
    deadline = time.time() + 2.0
    while e.phase is Phase.FIRST_NIGHT and time.time() < deadline:
        time.sleep(0.02)
    assert e.phase is Phase.DAY

    # Sanity: chair.character matches player.character before the swap.
    poisoner_chair = next(
        c for c in e.chairs.list() if c["player_id"] == poisoner_pid
    )
    assert poisoner_chair["character"] == "Poisoner"
    assert e.get_player(poisoner_pid).character.name == "Poisoner"

    # Night 2: Imp self-kills. With only one Minion (the Poisoner)
    # alive, ``select_new_imp`` auto-resolves to the Poisoner — no
    # prompt sent.
    e.advance_to_night()
    e.start_night()
    drain(e, [
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        # Poisoner picks Alice again.
        ({"character": "Poisoner", "step": "select_player"}, a.id),
        # Imp self-kills. select_new_imp auto-resolves (single
        # eligible: the Poisoner).
        ({"character": "Imp", "step": "select_target"}, imp_pid),
        # Inline "YOU ARE the Imp." reveal.
        ({"step_kind": "imp_self_kill_reveal", "reveal": "demon_role",
          "demon_character": "Imp"}, None),
        # Empath sober + healthy → auto information.
        ({"character": "Empath", "step": "information"}, None),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])
    deadline = time.time() + 2.0
    while e.phase is Phase.NIGHT and time.time() < deadline:
        time.sleep(0.02)
    assert e.phase is Phase.DAY

    # Single source of truth: chair.character matches player.character.
    poisoner_chair = next(
        c for c in e.chairs.list() if c["player_id"] == poisoner_pid
    )
    assert poisoner_chair["character"] == "Imp", (
        f"Chair character must update to 'Imp' on promotion; got "
        f"{poisoner_chair['character']!r}."
    )
    assert e.get_player(poisoner_pid).character.name == "Imp"

    # The old Poisoner ability is gone: night 3 must NOT prompt the
    # Poisoner to pick a player (the seat is the Imp now). The new Imp
    # asks for a kill; that's the only character prompt this night
    # (besides the Empath info).
    e.advance_to_night()
    e.start_night()
    drain(e, [
        ({"step_kind": "preset_step", "step_name": "Dusk"}, None),
        # NO Poisoner step — minion lost its ability.
        # Imp (Dan, the former Poisoner) picks Bob (Empath).
        ({"character": "Imp", "step": "select_target"}, b.id),
        ({"step_kind": "preset_step", "step_name": "Dawn"}, None),
    ])
    deadline = time.time() + 2.0
    while e.phase is Phase.NIGHT and time.time() < deadline:
        time.sleep(0.02)
    assert e.phase is Phase.DAY


def test_change_character_produces_fresh_instance() -> None:
    """``Engine.change_character`` must produce a fully-fresh new
    character: per-Character internal flags (e.g. Slayer._used,
    Virgin._triggered) are at their __init__ defaults, and every
    per-role Player flag (once_per_game_used, mad_about,
    protected_from_demon) is cleared. Identity (alignment, alive,
    drunk, poisoned) is preserved.
    """
    e = Engine()
    a = e.add_seat("Alice")
    b = e.add_seat("Bob")
    c = e.add_seat("Cara")
    d = e.add_seat("Dan")
    f = e.add_seat("Eve")
    e.assign_character(a.id, "Slayer")     # has _used + once_per_game
    e.assign_character(b.id, "Empath")
    e.assign_character(c.id, "Mayor")
    e.assign_character(d.id, "Scarlet Woman")
    e.assign_character(f.id, "Imp")
    e.start_game()

    # Mark the Slayer's once-per-game as spent on the player AND on
    # the character instance. Pile on a few unrelated flags
    # (mad_about, protected_from_demon) too, plus poisoned (which is
    # supposed to PERSIST across a role swap because it's a seat
    # condition the Poisoner inflicted).
    slayer_player = e.get_player(a.id)
    slayer_player.once_per_game_used = True
    slayer_player.character._used = True
    slayer_player.mad_about.append("Empath")
    slayer_player.protected_from_demon = True
    e.poison(a.id)

    # Save a reference to the OLD character instance so we can
    # confirm it's not the same object after the swap.
    old_char = slayer_player.character

    # Convert the Slayer into the Imp (regardless of game logic; the
    # API itself is what we're testing here).
    e.change_character(a.id, "Imp")

    # New Character is a fresh instance.
    new_char = slayer_player.character
    assert new_char is not None
    assert new_char.name == "Imp"
    assert new_char is not old_char, (
        "change_character must build a brand-new Character; got the "
        "same instance as before."
    )
    # The Imp class doesn't carry any once-per-game state of its own,
    # but spot-check a different replacement: turn the Empath (a
    # plain player) into a Slayer. The fresh Slayer must have
    # _used=False even though the previous Slayer had _used=True.
    e.change_character(b.id, "Slayer")
    new_slayer = e.get_player(b.id).character
    assert new_slayer is not None and new_slayer.name == "Slayer"
    assert getattr(new_slayer, "_used", None) is False, (
        "Fresh Slayer instance must start with _used=False."
    )

    # Per-role Player flags reset on the seat that just got swapped
    # to Imp.
    assert slayer_player.once_per_game_used is False, (
        "change_character must reset once_per_game_used."
    )
    assert slayer_player.mad_about == [], (
        "change_character must clear mad_about (no inherited madness)."
    )
    assert slayer_player.protected_from_demon is False, (
        "change_character must clear protected_from_demon."
    )

    # Identity preserved.
    assert slayer_player.alive is True
    assert slayer_player.alignment is not None
    # Poisoned status is a seat condition (the Poisoner inflicted
    # it); it persists across the role swap. Storyteller can clear
    # it explicitly via cure_poison if a ruling calls for it.
    assert slayer_player.poisoned is True


if __name__ == "__main__":
    test_first_night_preset_order_and_auto_dawn()
    print("preset-night test passed.")
    test_pending_win_night_runs_only_dusk_and_dawn()
    print("pending-win-night test passed.")
    test_scarlet_woman_promotion_reveal_at_next_night()
    print("scarlet-woman-promotion-reveal test passed.")
    test_imp_self_kill_promotes_regular_minion_with_reveal()
    print("imp-self-kill-regular-minion-promotion test passed.")
    test_imp_self_kill_with_sw_only_minion_auto_resolves()
    print("imp-self-kill-sw-only-minion-auto-resolve test passed.")
    test_imp_self_kill_5plus_alive_sw_must_promote()
    print("imp-self-kill-5plus-alive-sw-must-promote test passed.")
    test_change_character_syncs_chair_and_drops_old_ability()
    print("change-character-syncs-chair test passed.")
    test_change_character_produces_fresh_instance()
    print("change-character-fresh-instance test passed.")
