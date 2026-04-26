"""Tests for the Clocktower state machine."""

from __future__ import annotations

import pytest

from clocktower import (
    Alignment,
    DeathCause,
    Game,
    InvalidActionError,
    InvalidPhaseError,
    Phase,
    RuleViolationError,
)


# ---------------------------------------------------------------------------
# Test fixtures.
# ---------------------------------------------------------------------------


# A canonical 8-player Trouble Brewing setup (Chef/Empath/Fortune Teller/
# Undertaker/Virgin/Drunk(thinks Investigator)/Scarlet Woman/Imp).
DEFAULT_SETUP = [
    ("Alice",  "Chef"),
    ("Bob",    "Empath"),
    ("Carol",  "Fortune Teller"),
    ("Dave",   "Undertaker"),
    ("Eve",    "Virgin"),
    ("Frank",  "Drunk"),          # Thinks Investigator.
    ("Grace",  "Scarlet Woman"),
    ("Heidi",  "Imp"),
]


def make_game(setup=DEFAULT_SETUP) -> tuple[Game, dict[str, int]]:
    """Build a game seated and character-assigned, ready to ``start_game``."""
    g = Game()
    ids = {}
    for name, char in setup:
        pid = g.add_player(name)
        g.assign_character(pid, char)
        ids[name] = pid
    # Mark the Drunk as believing they're the Investigator.
    if "Frank" in ids:
        g.set_perceived_character(ids["Frank"], "Investigator")
    return g, ids


def started_game() -> tuple[Game, dict[str, int]]:
    g, ids = make_game()
    g.start_game()
    return g, ids


# ---------------------------------------------------------------------------
# Setup and start.
# ---------------------------------------------------------------------------


def test_add_and_remove_players_preserves_seating():
    g = Game()
    a = g.add_player("A")
    b = g.add_player("B")
    c = g.add_player("C")
    assert [p.seat for p in g.players] == [0, 1, 2]
    g.remove_player(b)
    assert [p.name for p in g.players] == ["A", "C"]
    assert [p.seat for p in g.players] == [0, 1]


def test_start_game_requires_at_least_5_players():
    g = Game()
    for i in range(4):
        pid = g.add_player(f"P{i}")
        g.assign_character(pid, "Chef")
    with pytest.raises(RuleViolationError):
        g.start_game()


def test_start_game_requires_exactly_one_demon():
    # All 5 townsfolk — no Demon.
    g = Game()
    for i, char in enumerate(["Chef", "Empath", "Fortune Teller", "Virgin", "Mayor"]):
        pid = g.add_player(f"P{i}")
        g.assign_character(pid, char)
    with pytest.raises(RuleViolationError):
        g.start_game()


def test_drunk_is_marked_drunk_on_assignment():
    g, ids = make_game()
    assert g.get_player(ids["Frank"]).drunk is True
    assert g.get_player(ids["Frank"]).has_ability is False


def test_start_game_enters_first_night():
    g, _ = started_game()
    assert g.phase is Phase.FIRST_NIGHT
    assert g.night_number == 1


# ---------------------------------------------------------------------------
# Phase transitions.
# ---------------------------------------------------------------------------


def test_cannot_nominate_at_night():
    g, ids = started_game()
    with pytest.raises(InvalidPhaseError):
        g.nominate(ids["Alice"], ids["Bob"])


def test_cannot_wake_during_day():
    g, ids = started_game()
    g.advance_to_day()
    with pytest.raises(InvalidPhaseError):
        g.wake_player(ids["Alice"])


def test_day_night_cycle():
    g, ids = started_game()
    assert g.phase is Phase.FIRST_NIGHT
    g.advance_to_day()
    assert g.phase is Phase.DAY
    assert g.day_number == 1
    g.skip_execution()
    g.advance_to_night()
    assert g.phase is Phase.NIGHT
    assert g.night_number == 2


# ---------------------------------------------------------------------------
# Night: demon kill, Monk protection, Soldier safety.
# ---------------------------------------------------------------------------


def test_demon_kill_and_dawn_announcement():
    g, ids = started_game()
    g.kill(ids["Alice"], DeathCause.DEMON_KILL)
    # At night, the death is pending — not announced yet.
    assert ids["Alice"] in [p.id for p in g.pending_night_deaths]
    deaths = g.advance_to_day()
    assert [p.id for p in deaths] == [ids["Alice"]]
    assert not g.get_player(ids["Alice"]).alive


def test_monk_protects_from_demon_kill():
    g, ids = started_game()
    g.protect_from_demon(ids["Alice"])
    g.kill(ids["Alice"], DeathCause.DEMON_KILL)
    assert g.get_player(ids["Alice"]).alive


def test_soldier_immune_to_demon():
    g = Game()
    ids = {}
    for name, char in [
        ("A", "Soldier"),
        ("B", "Empath"),
        ("C", "Chef"),
        ("D", "Virgin"),
        ("E", "Mayor"),
        ("F", "Poisoner"),
        ("G", "Imp"),
    ]:
        pid = g.add_player(name)
        g.assign_character(pid, char)
        ids[name] = pid
    g.start_game()
    g.kill(ids["A"], DeathCause.DEMON_KILL)
    assert g.get_player(ids["A"]).alive


def test_poisoned_soldier_can_die_to_demon():
    g = Game()
    ids = {}
    for name, char in [
        ("A", "Soldier"),
        ("B", "Empath"),
        ("C", "Chef"),
        ("D", "Virgin"),
        ("E", "Mayor"),
        ("F", "Poisoner"),
        ("G", "Imp"),
    ]:
        pid = g.add_player(name)
        g.assign_character(pid, char)
        ids[name] = pid
    g.start_game()
    g.poison(ids["A"])
    g.kill(ids["A"], DeathCause.DEMON_KILL)
    assert not g.get_player(ids["A"]).alive


# ---------------------------------------------------------------------------
# Nomination rules.
# ---------------------------------------------------------------------------


def test_cannot_nominate_before_open():
    g, ids = started_game()
    g.advance_to_day()
    with pytest.raises(InvalidActionError):
        g.nominate(ids["Alice"], ids["Bob"])


def test_each_player_may_nominate_once_per_day():
    g, ids = started_game()
    g.advance_to_day()
    g.open_nominations()
    g.nominate(ids["Alice"], ids["Bob"])
    g.record_vote(ids["Alice"], True)
    g.tally_nomination()
    with pytest.raises(RuleViolationError):
        g.nominate(ids["Alice"], ids["Carol"])


def test_each_player_may_be_nominated_once_per_day():
    g, ids = started_game()
    g.advance_to_day()
    g.open_nominations()
    g.nominate(ids["Alice"], ids["Bob"])
    g.tally_nomination()
    with pytest.raises(RuleViolationError):
        g.nominate(ids["Carol"], ids["Bob"])


def test_dead_player_cannot_nominate():
    g, ids = started_game()
    g.kill(ids["Alice"], DeathCause.DEMON_KILL)
    g.advance_to_day()
    g.open_nominations()
    with pytest.raises(RuleViolationError):
        g.nominate(ids["Alice"], ids["Bob"])


# ---------------------------------------------------------------------------
# Voting: majority, plurality, ties, dead vote tokens.
# ---------------------------------------------------------------------------


def test_vote_majority_and_plurality_makes_about_to_die():
    g, ids = started_game()
    g.advance_to_day()
    g.open_nominations()
    g.nominate(ids["Alice"], ids["Heidi"])
    # 8 alive players, need >= 4 YES votes.
    for name in ("Alice", "Bob", "Carol", "Dave"):
        g.record_vote(ids[name], True)
    nom = g.tally_nomination()
    assert nom.succeeded
    assert g.about_to_die.id == ids["Heidi"]


def test_tie_on_second_nomination_clears_about_to_die():
    g, ids = started_game()
    g.advance_to_day()
    g.open_nominations()
    # First nomination: 4 YES votes -> about to die.
    g.nominate(ids["Alice"], ids["Heidi"])
    for name in ("Alice", "Bob", "Carol", "Dave"):
        g.record_vote(ids[name], True)
    g.tally_nomination()
    assert g.about_to_die.id == ids["Heidi"]

    # Second nomination: also 4 YES votes (tie) -> neither about to die.
    g.nominate(ids["Eve"], ids["Grace"])
    for name in ("Alice", "Bob", "Carol", "Dave"):
        g.record_vote(ids[name], True)
    g.tally_nomination()
    assert g.about_to_die is None


def test_second_nomination_with_more_votes_replaces_about_to_die():
    g, ids = started_game()
    g.advance_to_day()
    g.open_nominations()
    g.nominate(ids["Alice"], ids["Heidi"])
    for name in ("Alice", "Bob", "Carol", "Dave"):
        g.record_vote(ids[name], True)
    g.tally_nomination()

    g.nominate(ids["Eve"], ids["Grace"])
    for name in ("Alice", "Bob", "Carol", "Dave", "Eve"):
        g.record_vote(ids[name], True)
    g.tally_nomination()
    assert g.about_to_die.id == ids["Grace"]


def test_dead_player_vote_token_spent_only_on_yes():
    g, ids = started_game()
    g.kill(ids["Alice"], DeathCause.DEMON_KILL)
    g.advance_to_day()
    # Alice is dead but has her vote token.
    assert g.get_player(ids["Alice"]).has_vote_token
    g.open_nominations()
    g.nominate(ids["Bob"], ids["Heidi"])
    g.record_vote(ids["Alice"], False)  # No — token not spent.
    g.tally_nomination()
    assert g.get_player(ids["Alice"]).has_vote_token

    g.nominate(ids["Carol"], ids["Grace"])
    g.record_vote(ids["Alice"], True)  # Yes — token spent.
    g.tally_nomination()
    assert not g.get_player(ids["Alice"]).has_vote_token


def test_dead_player_without_vote_token_cannot_vote():
    g, ids = started_game()
    g.kill(ids["Alice"], DeathCause.DEMON_KILL)
    g.advance_to_day()
    g.open_nominations()
    g.nominate(ids["Bob"], ids["Heidi"])
    g.record_vote(ids["Alice"], True)
    g.tally_nomination()  # spends Alice's token
    g.nominate(ids["Carol"], ids["Grace"])
    with pytest.raises(RuleViolationError):
        g.record_vote(ids["Alice"], True)


# ---------------------------------------------------------------------------
# Execution.
# ---------------------------------------------------------------------------


def test_execute_player_kills_about_to_die():
    g, ids = started_game()
    g.advance_to_day()
    g.open_nominations()
    g.nominate(ids["Alice"], ids["Heidi"])
    for name in ("Alice", "Bob", "Carol", "Dave"):
        g.record_vote(ids[name], True)
    g.tally_nomination()
    executed = g.execute_player()
    assert executed.id == ids["Heidi"]
    assert not g.get_player(ids["Heidi"]).alive


def test_only_one_execution_per_day():
    g, ids = started_game()
    g.advance_to_day()
    g.open_nominations()
    g.nominate(ids["Alice"], ids["Bob"])
    for name in ("Alice", "Bob", "Carol", "Dave"):
        g.record_vote(ids[name], True)
    g.tally_nomination()
    g.execute_player()
    with pytest.raises(RuleViolationError):
        g.execute_player(ids["Carol"])


# ---------------------------------------------------------------------------
# Win conditions.
# ---------------------------------------------------------------------------


def test_executing_demon_ends_game_good_wins():
    g, ids = started_game()
    g.advance_to_day()
    g.open_nominations()
    g.nominate(ids["Alice"], ids["Heidi"])
    for name in ("Alice", "Bob", "Carol", "Dave"):
        g.record_vote(ids[name], True)
    g.tally_nomination()
    # Scarlet Woman takeover needs 5+ alive; here we still have 8 alive,
    # so killing the Imp by execution WILL promote the Scarlet Woman.
    # To isolate "demon dies -> good wins", kill down to <5 first.
    # Instead, directly test the case when SW is dead.
    g.get_player(ids["Grace"]).kill(DeathCause.ABILITY)
    g.execute_player()
    assert g.phase is Phase.FINISHED
    assert g.winner is Alignment.GOOD


def test_scarlet_woman_becomes_imp_if_5_or_more_alive():
    g, ids = started_game()
    g.advance_to_day()
    g.open_nominations()
    g.nominate(ids["Alice"], ids["Heidi"])
    for name in ("Alice", "Bob", "Carol", "Dave"):
        g.record_vote(ids[name], True)
    g.tally_nomination()
    g.execute_player()
    # 7 alive players remain, SW takes over; game continues.
    assert g.phase is not Phase.FINISHED
    assert g.get_player(ids["Grace"]).character.name == "Imp"


def test_saint_executed_evil_wins():
    g = Game()
    ids = {}
    for name, char in [
        ("A", "Saint"),
        ("B", "Empath"),
        ("C", "Chef"),
        ("D", "Virgin"),
        ("E", "Mayor"),
        ("F", "Poisoner"),
        ("G", "Imp"),
    ]:
        pid = g.add_player(name)
        g.assign_character(pid, char)
        ids[name] = pid
    g.start_game()
    g.advance_to_day()
    g.open_nominations()
    g.nominate(ids["B"], ids["A"])
    # 7 alive, need 4 yes.
    for name in ("A", "B", "C", "D"):
        g.record_vote(ids[name], True)
    g.tally_nomination()
    g.execute_player()
    assert g.phase is Phase.FINISHED
    assert g.winner is Alignment.EVIL


def test_two_players_left_evil_wins():
    g, ids = started_game()
    # Kill everyone except the Imp and one good player.
    for name in ("Alice", "Bob", "Carol", "Dave", "Eve", "Grace"):
        g.kill(ids[name], DeathCause.DEMON_KILL)
    # Two alive (Frank = Drunk good, Heidi = Imp evil) -> evil wins.
    assert g.phase is Phase.FINISHED
    assert g.winner is Alignment.EVIL


# ---------------------------------------------------------------------------
# Player-view / snapshot sanity.
# ---------------------------------------------------------------------------


def test_drunk_player_view_shows_perceived_character():
    g, ids = started_game()
    view = g.player_view(ids["Frank"])
    # Frank is the Drunk but believes he's the Investigator.
    assert view["me"]["character"] == "Investigator"


def test_snapshot_is_json_serializable():
    import json

    g, ids = started_game()
    g.advance_to_day()
    g.open_nominations()
    g.nominate(ids["Alice"], ids["Bob"])
    snap = g.snapshot()
    # Should not raise.
    json.dumps(snap)
    assert snap["phase"] == "day"
    assert snap["active_nomination"]["nominator_id"] == ids["Alice"]
