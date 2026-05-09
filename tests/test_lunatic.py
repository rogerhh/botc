"""Lunatic — perceived-Demon shadowing, no real effects, demon info cards.

The Lunatic is a Bad Moon Rising Outsider who *thinks* they are the
Demon. The implementation reuses each demon's existing class via
:meth:`Character.acting_perceived_character` — the Lunatic's seat
shadow-runs whatever demon is currently in play. The
:attr:`Character.is_authentic` gate prevents the perceived demon's
ability from producing real kills/poisons on the Lunatic chair.

These tests cover:

* Auto-derivation of the perceived demon from the in-play demon, and
  re-derivation when the demon changes mid-setup.
* The setup-roster annotation (``Lunatic (Pukka)``) and chair
  snapshot's ``perceived_character`` field.
* Faithful Pukka shadowing on a Lunatic seat: no real poison lands,
  but the pick is recorded for the real Demon's info card and a
  ``lunatic_chosen`` reminder token is placed on the picked seat.
* Sober Lunatic picks → real Demon's wake info card carries
  ``THE LUNATIC PICKED ...``; empty picks read out as
  ``THE LUNATIC DID NOT PICK ANYONE TONIGHT``.
* Droisoned Lunatic → ST wrong-info interlude → tokens land on the
  ST-selected wrong players (per the user's spec).
* The first-night demon-info burst on the authentic Demon ends with
  ``THIS PLAYER IS THE LUNATIC``.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.engine import Engine
from engine.enums import CharType, Phase
from engine.event import Event, EventType


# ---------------------------------------------------------------------------
# Test scaffolding.
# ---------------------------------------------------------------------------

def _respond_and_settle(engine: Engine, prompt_id: int, response: Any,
                        timeout: float = 2.0) -> None:
    """Send a response and wait for the engine to consume it."""
    assert engine.respond(prompt_id, response), (
        f"engine.respond rejected prompt id {prompt_id}"
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        cur = engine.pending_prompt()
        if cur is None or cur.id != prompt_id:
            return
        time.sleep(0.005)
    raise TimeoutError(
        f"Engine did not move past prompt id {prompt_id}"
    )


def _default_response(p: Any) -> Any:
    """Sensible default answer for any prompt: the engine's pre-fill,
    or first-eligible if none. Used to skim past announcement /
    bookkeeping prompts that the test doesn't care about.
    """
    meta = p.meta or {}
    default = meta.get("default")
    if p.type in ("select_player", "select_players"):
        if isinstance(default, list) and default:
            return default if (p.count or 1) > 1 else default[0]
        if isinstance(default, int):
            return default
        eligible = list(p.eligible_player_ids or [])
        count = p.count or 1
        if not eligible:
            return None
        return eligible[0] if count == 1 else eligible[:count]
    if p.type == "select_character":
        count = getattr(p, "count", 1) or 1
        if isinstance(default, list) and default:
            return list(default) if count > 1 else default[0]
        if isinstance(default, str) and default:
            return default
        eligible = list(p.eligible_characters or [])
        if not eligible:
            return [] if count > 1 else None
        return eligible[0] if count == 1 else eligible[:count]
    if p.type == "yes_no":
        return bool(meta.get("default", False))
    return None


def _drain_prompts(
    engine: Engine,
    scripted: List[Tuple[Dict[str, Any], Any]],
    timeout: float = 5.0,
) -> List[Any]:
    """Walk through prompts and answer the *expected* ones in order.

    ``scripted`` lists matchers: each entry is ``(matcher_dict,
    response)``. Prompts that don't match the *next* expected matcher
    are auto-answered with ``_default_response`` (so dusk/dawn
    announcements, prompts the test doesn't care about, etc. are
    skimmed past). The matched prompts are returned in order so the
    caller can assert their contents.
    """
    seen: List[Any] = []
    deadline = time.time() + timeout
    while engine._night_thread and engine._night_thread.is_alive():
        if time.time() > deadline:
            raise TimeoutError(
                f"Night didn't finish; matched={len(seen)}, "
                f"pending={engine.pending_prompt()}"
            )
        p = engine.pending_prompt()
        if p is None:
            time.sleep(0.005)
            continue
        if len(seen) < len(scripted):
            matcher, response = scripted[len(seen)]
            ok = True
            for k, v in matcher.items():
                if k == "_passthrough":
                    continue
                if (p.meta or {}).get(k) != v:
                    ok = False
                    break
            if ok:
                if response is None and matcher.get("_passthrough"):
                    response = _default_response(p)
                seen.append(p)
                _respond_and_settle(engine, p.id, response)
                continue
        # Unexpected (or post-script) prompt: auto-answer with the
        # pre-filled default. The test only asserts on matched ones.
        _respond_and_settle(engine, p.id, _default_response(p))
    if len(seen) != len(scripted):
        raise AssertionError(
            f"Night ended with {len(seen)} matched, expected "
            f"{len(scripted)}."
        )
    return seen


def _make_lunatic_game(demon: str = "Pukka") -> Tuple[Engine, Dict[str, Any]]:
    """5-seat BMR-flavoured game: one Lunatic + one Demon + filler.

    Returns ``(engine, players_by_name)``. Demon defaults to Pukka so
    the Lunatic-as-Pukka acts on N1 too (no Imp first-night skip).

    The chair store is mirrored to the player assignments because the
    UI's ``_sync_chairs_to_engine`` isn't running in tests; chair_views
    needs ``chair.character`` populated to render display fields.
    """
    e = Engine()
    a = e.add_seat("Alice")    # Lunatic (perceived <demon>)
    b = e.add_seat("Bob")      # Soldier (filler)
    c = e.add_seat("Cara")     # Mayor   (filler)
    d = e.add_seat("Dan")      # Godfather (Minion)
    f = e.add_seat("Eve")      # <demon>
    bindings = [
        (a.id, "Alice", "Lunatic"),
        (b.id, "Bob", "Soldier"),
        (c.id, "Cara", "Mayor"),
        (d.id, "Dan", "Godfather"),
        (f.id, "Eve", demon),
    ]
    for pid, name, char in bindings:
        e.assign_character(pid, char)
    chairs = e.chairs.list()
    for chair, (pid, name, char) in zip(chairs, bindings):
        e.chairs.update(chair["id"], player_id=pid, name=name, character=char)
    return e, {"Alice": a, "Bob": b, "Cara": c, "Dan": d, "Eve": f}


# ---------------------------------------------------------------------------
# Module 2 / 3: setup auto-derivation + UI surface.
# ---------------------------------------------------------------------------

def test_lunatic_auto_derives_perceived_demon_from_in_play() -> None:
    """The Lunatic shadows whatever Demon is currently seated."""
    e, players = _make_lunatic_game(demon="Pukka")
    lunatic = players["Alice"].character

    assert lunatic.name == "Lunatic"
    assert lunatic._perceived_demon_name == "Pukka"
    assert lunatic.members and lunatic.members[0].name == "Pukka"
    # The perceived demon is wired to the Lunatic's seat, not a
    # detached instance.
    assert lunatic.members[0].player is e.get_player(players["Alice"].id)


def test_lunatic_perceived_demon_updates_when_demon_changes() -> None:
    """Swapping the in-play demon during setup re-derives perceived."""
    e, players = _make_lunatic_game(demon="Pukka")
    lunatic = players["Alice"].character
    assert lunatic._perceived_demon_name == "Pukka"

    # Swap the demon's seat to a different demon — the engine fires
    # the Lunatic's setup retrigger after every assign_character.
    e.assign_character(players["Eve"].id, "Imp")

    lunatic = players["Alice"].character  # same instance — character didn't swap
    assert lunatic._perceived_demon_name == "Imp"
    assert lunatic.members[0].name == "Imp"


def test_lunatic_falls_back_to_imp_when_no_demon_seated() -> None:
    """No Demon yet → fallback to Imp (per user's spec)."""
    e = Engine()
    a = e.add_seat("Alice")
    e.assign_character(a.id, "Lunatic")
    lunatic = e.get_player(a.id).character
    assert lunatic._perceived_demon_name == "Imp"
    assert lunatic.members[0].name == "Imp"


def test_lunatic_setup_picks_by_role_surfaces_perceived_demon() -> None:
    """``setup_picks_by_role`` carries ``Lunatic.fake = <demon>``.

    The UI's setup-roster list and edit-character panel both read this
    map to render the parenthetical ``Lunatic (Pukka)``.
    """
    e, players = _make_lunatic_game(demon="Po")
    picks = e._setup_picks_by_role()
    assert picks.get("Lunatic", {}).get("fake") == "Po"


def test_lunatic_chair_snapshot_has_perceived_character() -> None:
    """Chair snapshot exposes ``perceived_character`` for the edit panel."""
    e, players = _make_lunatic_game(demon="Shabaloth")
    chairs = e.chair_views()
    by_id = {c["id"]: c for c in chairs}
    lunatic_chair = by_id[players["Alice"].id]
    # Chair circle stays labeled ``Lunatic`` (display_character = Lunatic).
    assert lunatic_chair["display_character"] == "Lunatic"
    # Edit panel reads perceived_character to render "Lunatic (Shabaloth)".
    assert lunatic_chair["perceived_character"] == "Shabaloth"


# ---------------------------------------------------------------------------
# Module 1: is_authentic gate (no real effects on a Lunatic seat).
# ---------------------------------------------------------------------------

def test_perceived_demon_on_lunatic_seat_is_not_authentic() -> None:
    """``is_authentic`` is False for the Lunatic's perceived demon."""
    e, players = _make_lunatic_game(demon="Pukka")
    lunatic = e.get_player(players["Alice"].id).character
    perceived = lunatic.acting_perceived_character()
    assert perceived is not None
    assert perceived.name == "Pukka"
    # Non-authentic: the seat's actual character is the Lunatic, not
    # this perceived Pukka.
    assert perceived.is_authentic is False
    assert perceived.can_produce_real_effect is False
    # Real Pukka on Eve's seat is authentic.
    real_pukka = e.get_player(players["Eve"].id).character
    assert real_pukka.is_authentic is True
    assert real_pukka.can_produce_real_effect is True


# ---------------------------------------------------------------------------
# Module 5 / 6 / 7: end-to-end first night with Lunatic + Pukka.
# ---------------------------------------------------------------------------

def test_lunatic_as_pukka_first_night_full_flow() -> None:
    """First-night drive: demon-info for Lunatic + Pukka, then attacks.

    Walks the engine through:
      1. Lunatic Demon-Info: ST picks fake minions, fake bluffs.
         The Lunatic sees the fake info burst on their phone.
      2. Authentic Pukka Demon-Info: real minions + bluffs +
         ``THIS PLAYER IS THE LUNATIC`` token at the end.
      3. Pukka step (first_night_order = 19) — Lunatic-shadow Pukka
         goes first, picks a target. No real poison lands.
      4. The real Pukka's ``before_nightly_ability`` info card shows
         ``THE LUNATIC PICKED <name>``.
      5. Real Pukka picks her target. Real poison lands.
    """
    from engine import preset as preset_module

    e, players = _make_lunatic_game(demon="Pukka")
    p = preset_module.load_preset(
        preset_module.default_presets_root(), "bad_moon_rising"
    )
    assert p is not None
    e.set_preset(p)
    e.set_auto_advance_to_day(True)
    e.start_game()
    assert e.phase is Phase.FIRST_NIGHT

    # Sanity: Lunatic and real Pukka are both in_play under "Pukka"
    # via the new in_play multimap (the perceived shadow registers
    # alongside the real demon at the Pukka step).
    e.start_night()

    alice = players["Alice"]
    eve = players["Eve"]
    cara = players["Cara"]

    # Walk through prompts. The exact ordering inside _run_demon_info
    # is "Lunatic seats first, then authentic demons" (so the wiki's
    # "wake the Lunatic, then wake the Demon" ordering is preserved).
    seen = _drain_prompts(e, [
        # 1) Lunatic fake-minion picker. ST picks Bob and Cara as
        # fake minions (any players, dead/alive). N=1 because the
        # game has 1 real Minion (Godfather/Dan).
        ({
            "step": "select_fake_minions",
            "stage": "st_pre",
            "step_kind": "demon_info",
            "character": "Lunatic",
        }, [players["Bob"].id]),
        # 2) Lunatic fake-bluff picker (3 TF/Outsiders, in-play OK).
        ({
            "step": "select_bluffs",
            "stage": "st_pre",
            "step_kind": "demon_info",
            "_passthrough": True,
        }, None),
        # 3) Lunatic info burst (shown to Lunatic's phone).
        ({"step_kind": "demon_info", "stage": "info"}, None),
        # 4) Authentic Pukka bluff picker.
        ({
            "step": "select_bluffs",
            "stage": "st_pre",
            "step_kind": "demon_info",
            "character": "Demon",
            "_passthrough": True,
        }, None),
        # 5) Authentic Pukka info burst — should include LUNATIC card.
        ({
            "step_kind": "demon_info",
            "stage": "info",
            "character": "Demon",
        }, None),
        # 6) Pukka step: Lunatic-shadow goes first. Pick Bob.
        # The engine relabels the Lunatic-shadow's prompt so the ST
        # sees "Wake up Lunatic (<name>)" / "Lunatic (Pukka)" rather
        # than borrowing the authentic Pukka labels — meta.character
        # becomes "Lunatic" and meta.step_name becomes "Lunatic
        # (Pukka)".
        ({
            "character": "Lunatic",
            "step_name": "Lunatic (Pukka)",
            "step": "select_target",
            "is_demon_attack": True,
        }, players["Bob"].id),
        # 7) Real Pukka's before_nightly_ability info card.
        ({
            "step_kind": "lunatic_picks_for_demon",
            "character": "Pukka",
        }, None),
        # 8) Real Pukka picks target — Cara.
        ({
            "character": "Pukka",
            "step": "select_target",
            "is_demon_attack": True,
        }, cara.id),
    ])

    # ---- Verify the authentic demon-info burst included LUNATIC ----
    # The Lunatic's burst is now labeled ``character: "Lunatic"`` /
    # ``step_name: "Lunatic Info"`` while the authentic Demon's burst
    # keeps ``character: "Demon"``; both share the demon-info token
    # shape. ``lunatic_player_id`` is only set on the authentic-side
    # burst, so we use it to pick the Demon's burst out unambiguously.
    pukka_info = next(
        s for s in seen
        if (s.meta or {}).get("step_kind") == "demon_info"
        and (s.meta or {}).get("stage") == "info"
        and (s.meta or {}).get("lunatic_player_id") is not None
    )
    tokens = pukka_info.meta["render"]["tokens"]
    labels = [t["label"] for t in tokens]
    assert "THIS PLAYER IS THE LUNATIC" in labels, (
        f"Demon's first-night info burst should end with the LUNATIC "
        f"reveal; got tokens={labels}"
    )
    # And it should be the LAST token (per the user's spec).
    assert labels[-1] == "THIS PLAYER IS THE LUNATIC"
    # Body of the LUNATIC token names Alice (the seated Lunatic).
    lunatic_token = next(
        t for t in tokens if t["label"] == "THIS PLAYER IS THE LUNATIC"
    )
    assert lunatic_token["body"] == "Alice"

    # ---- Verify the Lunatic-pick info card on the real Pukka ----
    lunatic_card = next(
        s for s in seen
        if (s.meta or {}).get("step_kind") == "lunatic_picks_for_demon"
    )
    card_tokens = lunatic_card.meta["render"]["tokens"]
    assert len(card_tokens) == 1
    assert card_tokens[0]["label"] == "THE LUNATIC PICKED"
    assert card_tokens[0]["body"] == "Bob"

    # Wait for the night to wrap up (auto-advances to day).
    deadline = time.time() + 3.0
    while e.phase is Phase.FIRST_NIGHT and time.time() < deadline:
        time.sleep(0.01)
    assert e.phase is Phase.DAY, f"engine still in {e.phase}"

    # ---- Verify the Lunatic's perceived Pukka pick produced no real
    #      poison: Bob is alive and not poisoned. ----
    bob = e.get_player(players["Bob"].id)
    assert bob.alive
    assert not bob.poisoned
    # The real Pukka's pick (Cara) IS now poisoned.
    cara_player = e.get_player(cara.id)
    assert cara_player.poisoned


# ---------------------------------------------------------------------------
# compute_reminder_tokens / lunatic_chosen.
# ---------------------------------------------------------------------------

def test_lunatic_chosen_token_placed_on_pick() -> None:
    """The Lunatic's chair-side reminder lands on the picked player."""
    e, players = _make_lunatic_game(demon="Imp")  # Imp doesn't act on N1
    lunatic = e.get_player(players["Alice"].id).character

    # Simulate a recorded pick.
    e._lunatic_picks_tonight = [players["Bob"].id]
    contributions = lunatic.compute_reminder_tokens(e)
    assert contributions == {"lunatic_chosen": [players["Bob"].id]}

    # No picks → no token.
    e._lunatic_picks_tonight = []
    assert lunatic.compute_reminder_tokens(e) == {}


# ---------------------------------------------------------------------------
# Drunk-branch / no-Lunatic interactions.
# ---------------------------------------------------------------------------

def test_authentic_demon_skips_lunatic_card_when_no_lunatic() -> None:
    """No Lunatic seated → ``before_nightly_ability`` is a no-op."""
    e = Engine()
    a = e.add_seat("Alice")
    b = e.add_seat("Bob")
    c = e.add_seat("Cara")
    d = e.add_seat("Dan")
    f = e.add_seat("Eve")
    e.assign_character(a.id, "Soldier")
    e.assign_character(b.id, "Mayor")
    e.assign_character(c.id, "Saint")
    e.assign_character(d.id, "Poisoner")
    e.assign_character(f.id, "Pukka")
    e.start_game()
    pukka = e.get_player(f.id).character
    # before_nightly_ability is a no-op when no Lunatic is seated.
    # Calling it directly should not raise and should not emit prompts.
    assert pukka.is_authentic
    pukka.before_nightly_ability(e, 1)
    # Pending prompt is None (no info card emitted).
    assert e.pending_prompt() is None


def test_lunatic_pick_recorder_filters_decline_sentinel() -> None:
    """Po's decline_id is stripped from the recorded picks."""
    from engine.prompt import SelectPlayerPrompt

    e, players = _make_lunatic_game(demon="Po")
    # Simulate the Po-uncharged decline prompt resolving on the
    # Lunatic seat.
    decline_id = 0
    prompt = SelectPlayerPrompt(
        text="Po picks a player (or shakes head no)",
        eligible_player_ids=[1, 2, decline_id],
        count=1,
        target_player_id=players["Alice"].id,
        meta={
            "character": "Po",
            "step": "select_target_or_skip",
            "stage": "player",
            "is_demon_attack": True,
            "decline_id": decline_id,
            "lunatic_filter_id": decline_id,
        },
    )
    e._maybe_record_lunatic_pick(prompt, decline_id)
    # The decline sentinel is filtered → empty picks → "DID NOT PICK".
    assert e._lunatic_picks_tonight == []


def test_lunatic_pick_recorder_ignores_non_lunatic_seats() -> None:
    """``is_demon_attack`` tagged prompt on a real Demon seat: no-op."""
    from engine.prompt import SelectPlayerPrompt

    e, players = _make_lunatic_game(demon="Pukka")
    real_pukka_id = players["Eve"].id
    # Same shape as the Pukka attack prompt, but targeting the real
    # Pukka's seat.
    prompt = SelectPlayerPrompt(
        text="Pukka poisons a player",
        eligible_player_ids=[players["Alice"].id, players["Bob"].id],
        count=1,
        target_player_id=real_pukka_id,
        meta={
            "character": "Pukka",
            "step": "select_target",
            "stage": "player",
            "is_demon_attack": True,
        },
    )
    e._lunatic_picks_tonight = [-1]  # canary — should NOT be touched
    e._maybe_record_lunatic_pick(prompt, players["Bob"].id)
    # Recorder gates on Lunatic seat → no write.
    assert e._lunatic_picks_tonight == [-1]


# ---------------------------------------------------------------------------
# Droison interlude.
# ---------------------------------------------------------------------------

def test_droisoned_lunatic_interlude_overrides_picks() -> None:
    """Droisoned Lunatic → ST wrong-info → tokens land on wrong picks.

    Drives the interlude method directly with a fake step so we can
    assert the prompt-and-override semantics without spinning up a
    full preset night.
    """
    from engine import preset as preset_module
    e, players = _make_lunatic_game(demon="Pukka")
    e.start_game()
    e._phase = Phase.NIGHT
    e._night_number = 2

    lunatic_player = e.get_player(players["Alice"].id)
    # Simulate the Lunatic-shadow Pukka having recorded a real pick.
    e._lunatic_picks_tonight = [players["Bob"].id]
    # Manually mark the Lunatic as poisoned (the engine's resolver
    # would normally handle this via an effect; we shortcut for the
    # test).
    lunatic_player.poisoned = True

    # Build a perceived-Pukka shadow on the Lunatic's chair the same
    # way ``acting_perceived_character`` does at runtime.
    perceived = lunatic_player.character.acting_perceived_character()
    assert perceived is not None
    fake_step = preset_module.NightStep(
        name="Pukka", description="(test fake)"
    )

    # Run the interlude in a worker thread so we can answer the
    # prompt from the main thread.
    import threading
    done = threading.Event()
    def _drive() -> None:
        e._maybe_run_lunatic_droison_interlude(perceived, fake_step)
        done.set()
    t = threading.Thread(target=_drive, daemon=True)
    t.start()

    # Wait for the prompt to appear.
    deadline = time.time() + 2.0
    while time.time() < deadline:
        p = e.pending_prompt()
        if p is not None:
            break
        time.sleep(0.005)
    p = e.pending_prompt()
    assert p is not None, "expected a wrong-info SelectPlayerPrompt"
    assert (p.meta or {}).get("step_kind") == "lunatic_droison"
    # Engine pre-fills a wrong default — a single player not equal to
    # the actual pick (Bob).
    default = (p.meta or {}).get("default")
    assert isinstance(default, list) and len(default) == 1
    assert default[0] != players["Bob"].id

    # ST overrides with Cara (a different "wrong" player than the
    # engine's default, to prove the ST's choice is what wins).
    _respond_and_settle(e, p.id, [players["Cara"].id])
    done.wait(timeout=2.0)
    t.join(timeout=2.0)

    # Picks have been replaced with Cara — the demon's info card and
    # the lunatic_chosen reminder will both follow this list.
    assert e._lunatic_picks_tonight == [players["Cara"].id]
    # Token contribution from compute_reminder_tokens follows suit.
    contrib = lunatic_player.character.compute_reminder_tokens(e)
    assert contrib == {"lunatic_chosen": [players["Cara"].id]}


def test_sober_lunatic_interlude_is_noop() -> None:
    """Sober Lunatic → interlude does nothing."""
    from engine import preset as preset_module
    e, players = _make_lunatic_game(demon="Pukka")
    e.start_game()
    e._phase = Phase.NIGHT
    e._night_number = 2

    lunatic_player = e.get_player(players["Alice"].id)
    perceived = lunatic_player.character.acting_perceived_character()
    assert perceived is not None
    e._lunatic_picks_tonight = [players["Bob"].id]
    fake_step = preset_module.NightStep(name="Pukka", description="")

    e._maybe_run_lunatic_droison_interlude(perceived, fake_step)
    # No override; picks unchanged.
    assert e._lunatic_picks_tonight == [players["Bob"].id]


# ---------------------------------------------------------------------------
# Po-Lunatic charge clock.
# ---------------------------------------------------------------------------

def test_lunatic_as_po_charge_clock_is_independent_and_silent() -> None:
    """Lunatic-as-Po has its own charge state; no 3-ATTACKS token."""
    e, players = _make_lunatic_game(demon="Po")
    e.start_game()

    lunatic = e.get_player(players["Alice"].id).character
    perceived_po = lunatic.acting_perceived_character()
    assert perceived_po is not None
    assert perceived_po.name == "Po"
    assert perceived_po._charged is False

    # Simulate the Lunatic-as-Po declining → charges internally.
    perceived_po._set_charged(e, True)
    assert perceived_po._charged is True
    # No Po3AttacksEffect emitted (gated on is_authentic).
    from engine.characters.po import Po3AttacksEffect
    effects = [
        eff for eff in e.effects_sourced_by(perceived_po)
        if isinstance(eff, Po3AttacksEffect)
    ]
    assert effects == [], (
        "Lunatic-as-Po charge state must stay silent — no 3 ATTACKS "
        "reminder on the Lunatic's chair."
    )

    # Real Po still emits the reminder when charged.
    real_po = e.get_player(players["Eve"].id).character
    assert real_po.is_authentic
    real_po._set_charged(e, True)
    real_effects = [
        eff for eff in e.effects_sourced_by(real_po)
        if isinstance(eff, Po3AttacksEffect)
    ]
    assert len(real_effects) == 1


if __name__ == "__main__":
    test_lunatic_auto_derives_perceived_demon_from_in_play()
    test_lunatic_perceived_demon_updates_when_demon_changes()
    test_lunatic_falls_back_to_imp_when_no_demon_seated()
    test_lunatic_setup_picks_by_role_surfaces_perceived_demon()
    test_lunatic_chair_snapshot_has_perceived_character()
    test_perceived_demon_on_lunatic_seat_is_not_authentic()
    test_lunatic_chosen_token_placed_on_pick()
    test_authentic_demon_skips_lunatic_card_when_no_lunatic()
    test_lunatic_pick_recorder_filters_decline_sentinel()
    test_lunatic_pick_recorder_ignores_non_lunatic_seats()
    test_sober_lunatic_interlude_is_noop()
    test_lunatic_as_po_charge_clock_is_independent_and_silent()
    test_droisoned_lunatic_interlude_overrides_picks()
    test_lunatic_as_pukka_first_night_full_flow()
    print("All Lunatic tests passed.")
