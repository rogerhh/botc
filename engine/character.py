"""Base Character class.

A Character is the *role* a Player is playing. It carries the static
data (name, type, night order, ability text) and implements two methods
the engine calls into:

    * ``ability(engine, night_number)`` — invoked once per relevant night
      (or by the storyteller during the day, for daytime abilities).
      The implementation drives the storyteller-facing prompt flow and
      dispatches the ability's events to the rest of the table.

    * ``reaction(event, engine)`` — called for every event in the game,
      so a character can update internal state (e.g. the Ravenkeeper
      arming itself when its player dies). The base implementation
      handles a small set of common state changes; subclasses override
      to add character-specific reactions.

Each character also exposes a class-level pair of orderings
(``first_night_order``, ``other_night_order``); ``0`` means "doesn't
act on this night". The engine uses these to walk action_order.
"""

from __future__ import annotations

import random as _rand
from typing import TYPE_CHECKING, Callable, Iterable, List, Optional, Sequence

from engine.enums import CharType, SetupMode
from engine.event import Event, EventType
from engine.prompt import InformationPrompt, SelectCharacterPrompt

if TYPE_CHECKING:
    from engine.engine import Engine
    from engine.player import Player
    from engine.check import Check


class Character:
    """Base Character class.

    Subclass this and override :meth:`ability` (and optionally
    :meth:`reaction`).

    The default ``ability`` is a no-op so you can subclass for
    characters with no nightly action (Soldier, Mayor, ...).
    """

    # --- Static metadata (override on subclass) ---
    name: str = "Character"
    char_type: CharType = CharType.TOWNSFOLK
    ability_text: str = ""

    # Night order. 0 means "doesn't act this night".
    first_night_order: int = 0
    other_night_order: int = 0

    # True for Slayer-style "once a game" abilities.
    once_per_game: bool = False

    # True for daytime abilities that remain usable after the seated
    # player has died. The canonical case is the Klutz, whose ability
    # ("when you learn that you died, publicly choose 1 alive player…")
    # only triggers post-death. The base ``use_daytime_ability`` path
    # in :class:`engine.engine.Engine` and the storyteller side panel
    # both gate the "Use ability" button on ``alive`` by default; this
    # flag relaxes that gate for roles that need to act after dying.
    daytime_ability_active_when_dead: bool = False

    # True for "any time" abilities the Storyteller can fire during
    # *night* as well as during day. The canonical case is the Tinker
    # ("you might die at any time") — the Storyteller may choose to
    # kill the Tinker at any moment, day or night. The base
    # ``use_daytime_ability`` path in :class:`engine.engine.Engine`
    # and the storyteller side panel both gate the "Use ability"
    # button on ``phase == DAY`` by default; this flag relaxes that
    # gate. Characters opting in MUST implement
    # :meth:`daytime_ability` synchronously — the engine runs at-night
    # invocations inline (no worker thread, no
    # ``send_prompt`` calls) so it does not collide with the running
    # night-order thread.
    daytime_ability_active_at_night: bool = False

    # Setup-time deltas (Baron: outsider +2, townsfolk -2).
    setup_outsider_delta: int = 0
    setup_townsfolk_delta: int = 0

    # Reminder tokens this character places on player seats. Each entry
    # is a dict ``{"name": "<DISPLAY>", "icon": "<file>.png"}`` where
    # ``icon`` is a filename in ``assets/tokens/`` (relative). The
    # storyteller-facing UI uses these to render the character's
    # available reminder tokens next to its seat (Butler -> MASTER,
    # Washerwoman -> TOWNSFOLK / WRONG, Fortune Teller -> RED HERRING,
    # ...). Override on subclasses; default is no reminder tokens.
    #
    # Token visibility is *purely* a function of state. The UI renders
    # a reminder token whenever the engine has a slot pointing at the
    # carrying chair (e.g. ``pool.washerwoman_townsfolk()`` is set);
    # the engine clears the slot when the ability that owns the token
    # resolves, so transient tokens (Washerwoman / Librarian /
    # Investigator "you start knowing") disappear automatically once
    # the ability ends. There are no "first-night-only" or other
    # phase-gated display flags — display always matches state.
    reminder_tokens: list = []

    # Setup-time token kinds a chair holding this character can host.
    # Defaults are derived from :meth:`registration_categories` via
    # :meth:`accepts_tokens`; subclasses with self-exclusion rules
    # (Washerwoman / Librarian / Investigator can't host their own
    # WRONG token) override that classmethod.
    _SETUP_TOKENS_BY_REGISTRATION = {
        CharType.TOWNSFOLK: frozenset({
            "washerwoman_townsfolk", "ft_red_herring",
            "washerwoman_wrong", "librarian_wrong", "investigator_wrong",
            "grandmother_grandchild",
        }),
        CharType.OUTSIDER: frozenset({
            "ft_red_herring", "librarian_outsider",
            "washerwoman_wrong", "librarian_wrong", "investigator_wrong",
            "grandmother_grandchild",
        }),
        CharType.MINION: frozenset({
            "investigator_minion",
            "washerwoman_wrong", "librarian_wrong", "investigator_wrong",
        }),
        CharType.DEMON: frozenset({
            "washerwoman_wrong", "librarian_wrong", "investigator_wrong",
        }),
    }

    @classmethod
    def accepts_tokens(cls) -> "frozenset[str]":
        """Setup-time reminder-token kinds this character's chair can host.

        Most kinds use :meth:`registration_categories` so misregistering
        roles (Spy as Townsfolk/Outsider, Recluse as Minion/Demon) get
        the union of every category they could register as. The Drunk's
        IS-THE-DRUNK token is the exception: it requires a *true*
        Townsfolk (no Spy), so it gates on ``char_type`` directly.

        Subclasses with self-exclusion rules (e.g. the Washerwoman
        can't host her own WRONG token) override.
        """
        out: set = set()
        for cat in cls.registration_categories():
            out |= cls._SETUP_TOKENS_BY_REGISTRATION.get(cat, frozenset())
        if cls.char_type is CharType.TOWNSFOLK:
            out.add("drunk")
        return frozenset(out)

    def __init__(self, player: Optional["Player"] = None) -> None:
        # The Character holds a back-pointer to its Player so it can
        # mutate the Player's states from inside ability() / reaction().
        self.player: Optional["Player"] = player
        # Other Characters this Character "owns" via setup-time picks.
        # The canonical case is the Drunk: when the storyteller picks
        # the Drunk's pretend Townsfolk, the chosen role is instantiated
        # and appended here, so the Drunk holds an actual Townsfolk
        # Character instance (with all its night order / ability text)
        # alongside its own state. Members do not have a Player set —
        # they are not seated, they are carried by the picking
        # Character.
        self.members: List["Character"] = []
        # First-night-ability tracking. True until the character has
        # actually fired their first-night ability slot. The engine's
        # night loop uses this — not the global ``night_number`` — to
        # decide which slot the character acts on. ``on_revive`` resets
        # this to True so a revived character's first-night ability is
        # available again on the next night.
        self._first_night_pending: bool = True

    # ------------------------------------------------------------------
    # Authenticity.
    # ------------------------------------------------------------------

    @property
    def is_authentic(self) -> bool:
        """Is this Character instance the seated player's *real* role?

        True for the real Empath / Pukka / Imp on their own seat. False
        for any perceived-role instance carried by an impersonator
        (Drunk, Lunatic) and run on the impersonator's chair via
        :meth:`acting_perceived_character`. The Drunk's perceived TF
        instance and the Lunatic's perceived Demon instance both fail
        this check because the seated ``player.character`` is the
        impersonator, not them.

        Used by demons (and any other ability with real-world effects)
        to gate the *resolution* path: a perceived Imp running on the
        Lunatic's chair still picks targets and goes through the
        motions, but their kill must not land. The Drunk's perceived
        ability flow is additionally gated by ``has_ability=False``
        from the self-drunk effect; the Lunatic is sober by design,
        so the authenticity check is the load-bearing gate there.
        """
        return (
            self.player is not None
            and self.player.character is self
        )

    @property
    def can_produce_real_effect(self) -> bool:
        """Combined gate for "should this ability's resolution apply?".

        Equivalent to ``self.is_authentic and self.player.has_ability``,
        with a defensive fallback if the player isn't wired. This is
        the single source of truth every demon ability (and any other
        role with persistent state-changing effects) checks before
        actually firing the kill / poison / charge / regurgitate. The
        ability still goes through the prompt-and-pick motions before
        this gate so the storyteller and player flow stay identical
        on every chair the role can run on (real Demon, Lunatic-shadow,
        droisoned, etc.).
        """
        if self.player is None:
            return False
        return self.is_authentic and self.player.has_ability

    # ------------------------------------------------------------------
    # Lifecycle hooks.
    # ------------------------------------------------------------------

    def acting_perceived_character(self) -> Optional["Character"]:
        """Return the role this player believes they are, ready to act.

        The default returns ``None`` — most characters are exactly the
        role they appear to be.

        Override on any role whose seated player believes they are a
        *different* role and should be woken / receive reactions as
        that other role:

          * **Drunk:** thinks they are some Townsfolk; the engine wakes
            them at that Townsfolk's night slot, runs that Townsfolk's
            setup_ability, and dispatches reactions to that Townsfolk.
            Because ``player.has_ability`` is ``False`` for a Drunk, the
            impersonated role's ability flows take their drunk/poisoned
            branch and produce wrong info / no real effect.

        Implementations must wire ``perceived.player`` to the same
        seated :class:`Player` that this character occupies, so the
        impersonated role's ``self.player.id``-based logic resolves to
        the seated chair. The engine only consults this hook for
        roles that genuinely act as the impersonated role; other uses
        of ``self.members`` (e.g. the Fortune Teller's red-herring
        role lookup) deliberately keep ``members[0].player = None`` and
        leave this hook returning ``None``.
        """
        return None

    # Setup-time token-slot declarations.
    #
    # Each entry describes one setup-time reminder-token kind whose
    # value (a role name) lives in :class:`engine.pool.CharacterPool`.
    # The engine's generic token-drag dispatch (Engine.apply_token) is
    # driven entirely by this declaration; adding a new role with a
    # new setup pick (e.g. Pixie's chosen Townsfolk, Snake Charmer's
    # initial target) is one entry here plus a class attribute on the
    # role.
    #
    # Each entry is a dict with keys:
    #   ``kind``        — token kind (matches snapshot tokens[].kind).
    #   ``slot``        — short logical name ("townsfolk", "wrong",
    #                     "fake", "red_herring", …). Used in logging.
    #   ``getter``      — name of the CharacterPool method that returns
    #                     the current value (e.g. "washerwoman_townsfolk").
    #   ``setter``      — name of the CharacterPool method that writes
    #                     the value (e.g. "set_washerwoman_townsfolk").
    #   ``autofill``    — name of the CharacterPool method that
    #                     re-rolls a sensible default (or None).
    #   ``mutex_with``  — list of *other* token kinds this slot pairs
    #                     with (drag onto a chair carrying a partner
    #                     swaps the two). Default empty.
    #   ``check``       — Check identifying which chair characters can
    #                     receive this token. ``None`` means any chair
    #                     (e.g. WRONG tokens — any character can carry
    #                     them as long as it's not self / not the seen
    #                     partner). ``"true_townsfolk"`` is a magic
    #                     value used by the Drunk's IS-THE-DRUNK
    #                     swap which requires a *true* Townsfolk
    #                     (not a misregistered Spy).
    #   ``forbid_self`` — True if the token can't sit on the owner's
    #                     own seat (the WRONG and TYPED tokens forbid
    #                     this).
    #   ``forbid_seen`` — True if this WRONG token must differ from
    #                     its mutex_with seen partner (Washerwoman /
    #                     Librarian / Investigator WRONG slots).
    #   ``triggers_seat_swap`` — True only for the Drunk's IS-THE-
    #                     DRUNK token, which moves the IS-THE-DRUNK
    #                     marker AND swaps the chair characters.
    setup_picks: "tuple[dict, ...]" = ()

    def check_win_condition(
        self, engine: "Engine", *, at_dusk: bool
    ) -> "Optional[tuple[object, str]]":
        """Per-character win check, called by the engine win-loop.

        Returns ``(Alignment, reason)`` if this character's seat
        triggers a win right now, or ``None`` otherwise. Called once
        per seated character (and once per impersonated perceived
        character) on every ``Engine._check_win_conditions`` pass.

        The engine fires the *first* registered win — including its
        builtin demon-dead and two-alive checks — so a character that
        wants priority should rely on event ordering (e.g. Saint's
        EXECUTION reaction fires before the post-execution win check).

        ``at_dusk`` is True only for the dusk pass (after DAY_END,
        before NIGHT_START). Mayor's 3-alive-no-execution win uses
        this gate.

        Default: no contribution. Override on Mayor (3 alive at dusk),
        and any future role with a passive win condition. Saint stays
        a reaction-based pending-win because it fires *during* the
        execution event.
        """
        return None

    def compute_reminder_tokens(
        self, engine: "Engine"
    ) -> "dict[str, list[int]]":
        """Return the runtime reminder tokens this seat contributes.

        Returns a dict mapping ``token_kind`` -> list of seat
        ``player_id``s the token currently sits on. The engine merges
        these contributions across every seated character (and every
        impersonated perceived character) into the per-seat token map
        consumed by ``chair_views``.

        This is the primary scalability lever for reminder tokens:
        each character class owns the rule for which token its
        ability places, when it is visible, and which seats it sits
        on. The engine has no character-name knowledge at all.

        Default: no tokens. Override on any character that places a
        reminder token tied to its current state (Poisoner's POISONED,
        Butler's MASTER, Monk's SAFE, Slayer's NO ABILITY, Virgin's
        NO ABILITY, Undertaker's DIED TODAY, Demon's DEAD reminder,
        Scarlet Woman's IS THE DEMON, …).
        """
        return {}

    def setup_blocker(
        self, engine: "Engine"
    ) -> "Optional[str]":
        """Per-character readiness check for the Start Game button.

        Returns a short human-readable reason why this character isn't
        ready to start the game (e.g. "Drunk fake unset",
        "Washerwoman wrong invalid"), or ``None`` when this seat is
        ready. Called during Setup phase; the engine collects the
        first non-None contribution across every seated character
        and surfaces it in the snapshot as
        ``setup_validation_blocker``.

        Replaces the UI-side ``_validateSetupTokens`` logic that
        duplicated engine rules. Adding a new role with new
        prerequisites is a one-method override.

        Default: walk this class' ``setup_picks`` and report any slot
        whose value is missing or not in the current pool. Subclasses
        with stricter rules (Drunk's strict-true Townsfolk; the
        Librarian's "0 Outsiders" reading where the slot is allowed
        to be empty) override.
        """
        if self.player is None:
            return None
        pool_names = set(engine.pool.list())
        for spec in getattr(self.__class__, "setup_picks", ()) or ():
            slot = spec.get("slot", "?")
            getter_name = spec.get("getter")
            if not getter_name:
                continue
            getter = getattr(engine.pool, getter_name, None)
            if getter is None:
                continue
            value = getter()
            if not value:
                return f"{self.name} {slot} unset."
            if value not in pool_names:
                # Check fakery — the Drunk's fake TF is the one slot
                # whose value is *outside* the pool by design.
                if not spec.get("triggers_seat_swap"):
                    return f"{self.name} {slot} invalid."
        return None

    def absorb_setup_data(
        self, engine: "Engine", data: "dict"
    ) -> None:
        """Absorb pre-game UI picks for this character.

        Called from :meth:`Engine.apply_setup_data` for every seated
        character. The default looks up each declared ``setup_picks``
        kind in ``data`` (keyed by token kind, e.g. ``"drunk_fake"``,
        ``"washerwoman_townsfolk"``) and writes the value to the
        matching pool slot via the registry's setter — so a generic
        UI bag-state replay works without per-character code on the
        engine.

        Override on roles whose absorption needs to mutate
        character-internal state too (Drunk's ``members[0]`` /
        ``perceived_character_name``, Fortune Teller's
        ``_red_herring`` / ``members[0]``, Washerwoman / Librarian /
        Investigator's ``_chosen_*``). Such overrides should call
        ``super().absorb_setup_data(engine, data)`` to keep the
        pool-slot writes.
        """
        if self.player is None:
            return
        for spec in getattr(self.__class__, "setup_picks", ()) or ():
            kind = spec.get("kind")
            value = data.get(kind)
            if value is None:
                continue
            setter_name = spec.get("setter")
            if not setter_name:
                continue
            setter = getattr(engine.pool, setter_name, None)
            if setter is None:
                continue
            try:
                setter(value)
            except ValueError:
                # Tolerate stale UI snapshots — the pool's invariants
                # raise when a value isn't valid for the current slot.
                pass

    def on_assign_to_seat(self, engine: "Engine") -> None:
        """Run when this character is assigned to a seat.

        Fires from :meth:`Engine.assign_character` after the
        :class:`Player` <-> :class:`Character` wiring is in place but
        before any :meth:`on_setup_ability` pass. This is the place to
        seed character-owned state that has to exist *before* the role
        is acted on:

          * Drunk: mark the seat drunk; default ``perceived_character_name``
            to ``"Townsfolk"`` so the player's phone doesn't reveal the
            Drunk before the storyteller picks the impersonated TF.

        With the registry-effects refactor (Layer 2 + pool-system
        Phase A), this is also the hook for emitting *setup-phase*
        registry effects via ``engine.add_effect(SetupEffect(...))``.
        For example, the Washerwoman emits its TOWNSFOLK and WRONG
        markers here; the Fortune Teller emits its red-herring
        marker; the Grandmother emits its grandchild marker. Each
        SetupEffect class owns its auto-fill defaults so this method
        usually just calls a single helper or ``add_effect`` line.

        Default: no-op. Override on any role with seat-bound side
        effects of being assigned. The engine has no character-name
        knowledge in :meth:`Engine.assign_character`; everything the
        new role needs to seed lives on this hook.
        """
        return None

    def on_unseated(self, engine: "Engine") -> None:
        """Run when this character is removed from a seat.

        Fires from :meth:`Engine.assign_character` (when re-assigning
        a seat to a different character — the OLD character on the
        seat gets ``on_unseated`` called before the new one gets
        ``on_assign_to_seat``) and from :meth:`Engine.remove_seat`.

        Default behavior: purge every effect sourced by this
        character from the registry. Most setup-phase markers
        (WW/Lib/Inv/FT/Grandmother) want this — when the role
        leaves the bag, its tokens go with it.

        Override only if the character has a more nuanced cleanup
        story (currently no roles do).
        """
        if engine is None:
            return
        try:
            sourced = list(engine.effects_sourced_by(self))
        except Exception:  # pragma: no cover (defensive)
            return
        for eff in sourced:
            try:
                engine.purge_effect(eff)
            except Exception:  # pragma: no cover (defensive)
                pass

    def setup_ability(self, engine: "Engine") -> None:
        """Run the character's setup-time ability, if any (legacy entry).

        This is the original prompt-the-storyteller hook used by
        :meth:`Engine._run_setup_actions`. As of the on_setup_ability
        refactor, the engine's preferred entry is
        :meth:`on_setup_ability`, which is mode-aware
        (:class:`SetupMode`). The default ``on_setup_ability`` calls
        through to this method for ``SetupMode.IN_GAME`` so existing
        characters keep working unchanged.

        Default: no-op. Subclasses may override either method.
        ``setup_ability`` is appropriate when the character only has
        one path (always prompt the ST, no UI-driven shortcut). For
        characters whose setup picks can also be set via the UI's
        token UI (Drunk, Fortune Teller, Washerwoman), override
        ``on_setup_ability`` instead so the SETUP_PHASE branch can
        absorb the UI state without prompting.
        """
        return None

    def on_setup_ability(
        self,
        engine: "Engine",
        mode: SetupMode = SetupMode.IN_GAME,
    ) -> None:
        """Run the character's on-setup ability under the given mode.

        See :class:`engine.enums.SetupMode` for the meaning of each mode.

        Default behaviour:
          * ``SetupMode.SETUP_PHASE``: no-op. The UI is still in
            control; characters with no setup picks have nothing to
            absorb.
          * ``SetupMode.IN_GAME``: delegate to the legacy
            :meth:`setup_ability` so existing characters keep working.

        Override on any character whose setup-time decision can also
        be set via the UI's pool / token state — branch on ``mode`` to
        either absorb that state silently (SETUP_PHASE) or emit
        Storyteller prompts (IN_GAME).
        """
        if mode is SetupMode.IN_GAME:
            self.setup_ability(engine)
        # SETUP_PHASE: default no-op.

    def before_nightly_ability(
        self, engine: "Engine", night_number: int
    ) -> None:
        """Pre-ability hook fired by the engine just before ``ability``.

        The default implementation handles Demon-side bookkeeping so
        every BMR demon (and any future demon) gets it for free
        without per-class duplication:

          * If this is an *authentic* Demon seat (``is_authentic`` is
            True and ``char_type is CharType.DEMON``), and a Lunatic
            is seated on the table, emit the
            ``THE LUNATIC PICKED <names> TONIGHT`` /
            ``THE LUNATIC DID NOT PICK ANYONE TONIGHT`` info card to
            this Demon's player. Reads ``engine._lunatic_picks_tonight``
            (which the Lunatic-shadow's prompt-resolution hook
            populated earlier in the same night).

        The hook is a no-op for any non-Demon, any non-authentic
        seat (the Lunatic-shadow Pukka itself doesn't get this card),
        and any game without a seated Lunatic.

        Override on a subclass to add additional pre-ability logic;
        if you do, call ``super().before_nightly_ability(engine,
        night_number)`` first to keep the default Demon-side
        handling.
        """
        if not self.is_authentic:
            return
        if self.player is None or self.player.dead:
            return
        if self.char_type is not CharType.DEMON:
            return
        # Gate on a seated Lunatic. Avoid emitting the info card to a
        # game with no Lunatic — pure noise on the Demon's panel
        # otherwise.
        from engine.characters.lunatic import Lunatic as _Lunatic
        lunatic = next(
            (
                p for p in engine.players
                if p.character is not None
                and isinstance(p.character, _Lunatic)
            ),
            None,
        )
        if lunatic is None:
            return

        picks = list(getattr(engine, "_lunatic_picks_tonight", []) or [])
        pick_players = []
        for pid in picks:
            try:
                pick_players.append(engine.get_player(int(pid)))
            except (KeyError, ValueError, TypeError):
                continue
        names = ", ".join(p.name for p in pick_players) if pick_players else ""

        if pick_players:
            text = f"THE LUNATIC PICKED {names} TONIGHT."
            label = "THE LUNATIC PICKED"
            body = names
        else:
            text = "THE LUNATIC DID NOT PICK ANYONE TONIGHT."
            label = "THE LUNATIC PICKED"
            body = "no one"

        engine.send_prompt(InformationPrompt(
            text=text,
            target_player_id=self.player.id,
            shown_to_player=True,
            highlight_player_ids=[p.id for p in pick_players],
            meta={
                "step_kind": "lunatic_picks_for_demon",
                "character": self.name,
                "target_player_name": self.player.name,
                "stage": "info",
                "lunatic_player_id": lunatic.id,
                "lunatic_player_name": lunatic.name,
                "picked_player_ids": [p.id for p in pick_players],
                "picked_player_names": [p.name for p in pick_players],
                "render": {
                    "tokens": [{"label": label, "body": body}],
                },
            },
        ))

    def ability(self, engine: "Engine", night_number: int) -> None:
        """Run the character's nightly ability.

        Default: no-op. Override for characters with a night action.
        Implementations interact with the storyteller via
        ``engine.send_prompt(...)`` and dispatch events with
        ``engine.dispatch(...)``. This method runs in the engine's
        worker thread; ``send_prompt`` blocks until the storyteller
        responds.

        Note: implementations should respect the player's state. A
        drunk or poisoned source still goes through the motions
        (storyteller wakes them, etc.), but receives false information
        and the resolution event must NOT update game state.
        """
        return None

    def daytime_ability(self, engine: "Engine") -> None:
        """For abilities triggered during the day (Slayer, Virgin)."""
        return None

    def day_ability_available(self, engine: "Engine") -> bool:
        """Whether this character's day-time ability should be exposed
        as an invocable button on the player's left-side panel right
        now.

        The UI calls this on every seated character whenever a refresh
        happens during the day; characters with day-active or
        on-learning-of-death abilities (Slayer, Artist, Moonchild,
        Klutz, Tinker, Gossip, Virgin's nominator-execute trigger) say
        Yes when their gate is satisfied. Default returns False — most
        characters don't have a day-button.

        This is the engine-generic hook for the day-button surface; the
        engine never hardcodes character names. Subclasses override
        with their own gating logic (typically: alive AND not yet used
        AND any pending-trigger latch).
        """
        return False

    def recheck_persistent_effects(
        self, engine: "Engine", phase: str
    ) -> None:
        """Re-evaluate any persistent effects this character has applied.

        Called by the engine on **every seated character** (alive or
        dead, sober or drunk/poisoned, including any
        ``acting_perceived_character``) at the start of each dawn and
        each dusk. Most BotC abilities have a stated duration ("tonight
        and tomorrow day", "until dawn", "tonight only"…) and the
        ability stops working once its source can no longer maintain it
        (the source dies, becomes drunk/poisoned, or simply reaches
        the natural end of the duration).

        Default: no-op. Override on any character that has applied a
        persistent state to another seat (or to itself) and that state
        needs cleaning up at a phase boundary even when the source
        won't act again — e.g. the Poisoner's POISONED token must
        clear at the next dusk regardless of whether the Poisoner is
        still alive to do it themselves.

        Parameters
        ----------
        engine:
            The :class:`Engine` instance.
        phase:
            ``"dawn"`` (transition NIGHT -> DAY, after DAY_START
            dispatch) or ``"dusk"`` (transition DAY -> NIGHT, after
            DAY_END dispatch).
        """
        return None

    # ------------------------------------------------------------------
    # Registration.
    # ------------------------------------------------------------------

    # Class-level signal: which character types could this character's
    # ``registers_as`` override plausibly fake at run time? The default
    # implementation returns ``self.name`` (the true role) so a default
    # character can fake exactly its own char_type. Override classes
    # (Spy, Recluse) declare the broader sets they can fake — used by
    # :class:`engine.check.Check.could_register_as_pass` for
    # setup-time eligibility checks (no Storyteller prompts).
    @classmethod
    def registration_categories(cls) -> "tuple[CharType, ...]":
        """The set of char_types this class' ``registers_as`` may emit.

        For a non-misregistering character this is just its own
        ``char_type``. Spy / Recluse override to widen the set.
        """
        return (cls.char_type,)

    def registers_as(
        self,
        engine: "Engine",
        the_check: "Check",
    ) -> str:
        """Return the character name this player registers as for a check.

        Detection-style abilities (Washerwoman, Librarian, Investigator,
        Chef, Empath, Fortune Teller, Undertaker, Ravenkeeper, Slayer,
        Virgin) never call this directly — they use
        :meth:`Character.check`, which dispatches into ``registers_as``
        with the right :class:`Check` context.

        Default behaviour: returns ``self.name`` (the player's true
        role) without prompting. Override on any character whose
        registration may differ from their true role (Spy, Recluse).
        Overrides may return a stub name (e.g. ``"Townsfolk"``,
        ``"Good"``) when the check only inspects ``alignment`` /
        ``char_type`` — keeping the ST prompt minimal.

        See ``engine/README.md`` "Registering As" for the full
        description of when each detector calls ``check()``, what
        attributes it declares, and how Spy / Recluse handle the
        override.
        """
        return self.name

    @classmethod
    def could_pass_check(cls, the_check: "Check") -> bool:
        """Setup-time eligibility test, without prompting.

        Returns True iff this class' :meth:`registration_categories`
        overlap with the categories the ``the_check`` accepts. Used by
        the setup UI to decide whether a token (e.g. the Washerwoman's
        TOWNSFOLK seen-token) can be applied to a given chair: any
        chair whose character class *could* register in a way that
        passes the check is an eligible drop target.

        This is a STATIC test based purely on class-level metadata —
        it does NOT call :meth:`registers_as` and never prompts the
        Storyteller. The actual registration choice happens later, at
        ability time during the game.
        """
        return the_check.could_register_as_pass(cls.registration_categories())

    def check(
        self,
        engine: "Engine",
        target: "Player",
        the_check: "Check",
    ) -> bool:
        """Run a :class:`Check` against ``target`` and return pass/fail.

        Calls ``target.character.registers_as(engine, the_check)`` —
        the registration override on the target's class may prompt
        the Storyteller for a misregistration choice. The returned
        registered character name is then resolved to the check's
        ``attribute`` (``name`` / ``char_type`` / ``alignment``) and
        compared against ``the_check.passes``.

        Targets without an assigned character return ``False``
        (the check vacuously fails — there is nothing to register).
        """
        if target is None or target.character is None:
            return False
        # Lazy import to avoid a circular: engine.check imports
        # CharType from engine.enums; engine.character imports CharType
        # too. The Check dataclass itself only reaches into engine in
        # the helper, not at module load.
        from engine.check import attribute_value

        registered = target.character.registers_as(engine, the_check)
        try:
            value = attribute_value(engine, registered, the_check.attribute)
        except (KeyError, ValueError):
            # Defensive: a malformed registers_as response shouldn't
            # crash the engine. Treat as a fail.
            return False
        return value in the_check.passes

    # ------------------------------------------------------------------
    # Setup helpers.
    # ------------------------------------------------------------------

    def pick_character_at_setup(
        self,
        engine: "Engine",
        *,
        eligible_characters: List[str],
        text: str,
        meta: Optional[dict] = None,
        default: Optional[str] = None,
    ) -> Optional["Character"]:
        """Send a :class:`SelectCharacterPrompt` and instantiate the pick.

        This is the canonical helper for any character whose setup
        ability chooses *another Character* (not a Player). It:

          * sends a :class:`SelectCharacterPrompt` to the storyteller,
          * receives a character name (string),
          * builds a fresh :class:`Character` instance for that name
            via :meth:`Engine.build_character`,
          * appends that instance to ``self.members`` so the picking
            character carries the chosen role's full metadata
            (night order, ability text, …) alongside its own,
          * dispatches a :class:`EventType.SETUP_PICK` event so other
            characters can react if they care.

        Returns the instantiated :class:`Character`, or ``None`` if the
        storyteller's response was unusable.

        Self-avoidance default: if ``default`` is not provided and the
        caller supplies an ``eligible_characters`` list that *includes*
        ``self.name``, this helper auto-fills the prompt's
        ``meta["default"]`` with a randomly-chosen *non-self* eligible
        candidate. The ST can still pick self if they want; we just
        avoid pre-selecting it. Callers that prefer a specific
        default may pass ``default`` explicitly.

        **Drunk-style impersonation.** If this character is running as
        the *perceived* role on a Drunk-style impersonator's chair
        (i.e. the seated player's actual character is something else
        — see :meth:`Character.acting_perceived_character`), the
        storyteller is *not* prompted: the impersonated role's setup
        pick has no real effect (the player has no ability), and the
        physical reminder token does not need to be placed. We fill
        the slot with a :class:`GoodStub` placeholder (the canonical
        "some good player" stand-in) and return it, still appended to
        ``self.members`` so any downstream logic on the perceived
        role keeps working. The ``eligible_characters`` argument is
        ignored in this case — the pick is purely a slot-filler.
        """
        # Detect impersonation: the seated player's actual character
        # is not this Character instance. The canonical case is the
        # Drunk: ``self`` is the perceived TF, ``self.player.character``
        # is the Drunk. In that case fill the slot with a GoodStub
        # placeholder (a "some good player" stand-in) and skip the
        # storyteller prompt.
        is_impersonation = (
            self.player is not None
            and self.player.character is not None
            and self.player.character is not self
        )

        if is_impersonation:
            # Lazy import to avoid a circular import at module load
            # time (engine.characters.* import from engine.character).
            from engine.characters.stubs import GoodStub

            chosen: Character = GoodStub()
            self.members.append(chosen)
            impersonator_name = (
                self.player.character.name if self.player.character else "?"
            )
            engine.log(
                f"{self.name} (impersonated by {impersonator_name} on "
                f"{self.player.name}): filled setup pick with GoodStub "
                f"(no ST prompt; reminder token not placed)."
            )
            engine.dispatch(
                Event(
                    EventType.SETUP_PICK,
                    source=self,
                    targets=[self.player],
                    data={
                        "picked_character": chosen.name,
                        "picked_instance": chosen,
                        "auto_picked": True,
                    },
                )
            )
            return chosen

        prompt_meta = {"character": self.name, "step": "setup_select_character"}
        if meta:
            prompt_meta.update(meta)
        # Default selection. Honor a caller-provided ``default`` when
        # given; otherwise auto-pick a non-self eligible candidate
        # whenever ``self.name`` is in the eligible list (FT, WW). The
        # ST can still drag the token onto self if they want.
        if default is not None and default in eligible_characters:
            prompt_meta.setdefault("default", default)
        elif (
            "default" not in prompt_meta
            and eligible_characters
            and self.name in eligible_characters
        ):
            non_self = [c for c in eligible_characters if c != self.name]
            if non_self:
                prompt_meta["default"] = _rand.choice(non_self)
        prompt = SelectCharacterPrompt(
            text=text,
            eligible_characters=eligible_characters,
            target_player_id=self.player.id if self.player else None,
            meta=prompt_meta,
        )
        chosen_name = engine.send_prompt(prompt)
        if not isinstance(chosen_name, str) or not chosen_name:
            return None

        try:
            chosen = engine.build_character(chosen_name)
        except KeyError:
            engine.log(
                f"{self.name}: storyteller picked unknown character "
                f"{chosen_name!r}; ignoring."
            )
            return None

        # The picked character is *carried by* this character, not
        # seated. It has no Player.
        self.members.append(chosen)

        engine.dispatch(
            Event(
                EventType.SETUP_PICK,
                source=self,
                targets=[self.player] if self.player else [],
                data={
                    "picked_character": chosen.name,
                    "picked_instance": chosen,
                },
            )
        )
        return chosen

    # ------------------------------------------------------------------
    # Reaction.
    # ------------------------------------------------------------------

    def reaction(self, event: Event, engine: "Engine") -> None:
        """React to an event.

        The base implementation handles a small set of generic state
        updates. Subclasses should override for special reactions and
        delegate back to ``super().reaction(event, engine)`` for the
        cases they don't care about.

        If the SOURCE player is drunk or poisoned, the player's states
        may not be updated by their own ability's resolution events.
        Other characters' reactions are still allowed to fire — the
        game is still happening, the source just doesn't do what they
        think they're doing.
        """
        if self.player is None:
            return

        # Block resolution events from a drunk/poisoned source from
        # affecting *targets*. (Reactions on the source itself, like a
        # death, can still fire — that's already handled by the engine.)
        if event.type is EventType.RESOLUTION:
            src = event.source
            if src and src.player and not src.player.has_ability:
                return

        # Default behavior: nothing else to do here. Most state changes
        # are made directly by the source's ability via Engine helpers
        # (engine.kill, engine.poison, ...) which already do the right
        # thing.
        return None

    # ------------------------------------------------------------------
    # Helpers.
    # ------------------------------------------------------------------

    def acts_first_night(self) -> bool:
        return self.first_night_order > 0

    def acts_other_nights(self) -> bool:
        return self.other_night_order > 0

    def night_order(self, night_number: int) -> int:
        """Order at which this character acts on the upcoming night.

        The decision is now keyed off the per-character
        ``_first_night_pending`` flag rather than the global
        ``night_number``. A character whose first-night ability hasn't
        fired yet (a freshly-seated role on night 1, or a role that
        was revived) sorts at its first-night order; once the slot
        has been spent, subsequent nights use ``other_night_order``.
        ``night_number`` is kept on the signature for back-compat with
        callers who still pass it, but it is no longer consulted.
        """
        if self._first_night_pending and self.first_night_order > 0:
            return self.first_night_order
        return self.other_night_order

    def acts_on_night(self, night_number: int) -> bool:
        return self.night_order(night_number) > 0

    @property
    def is_first_night_pending(self) -> bool:
        """Has the first-night ability slot been spent yet?

        True for newly-seated characters (game start) and for any
        character who has been revived since last firing their
        first-night ability. Characters whose ``ability`` branches on
        "first night vs other night" should consult this rather than
        the engine-supplied ``night_number`` argument so their
        first-night branch fires again after revive.
        """
        return self._first_night_pending

    def mark_first_night_fired(self) -> None:
        """Mark the first-night ability slot as spent.

        Called by the engine after the character's ability has run on
        a night where it acted at its first-night order. After this,
        the character moves to its ``other_night_order`` slot until
        :meth:`on_revive` resets the flag.
        """
        self._first_night_pending = False

    def on_revive(self, engine: "Engine") -> None:
        """Refresh per-character ability state on revive.

        Called by :meth:`engine.engine.Engine.revive` whenever this
        character's seated player comes back to life. The default
        refreshes the first-night ability slot — a revived character
        will act at their ``first_night_order`` again on their next
        night — and clears any per-character once-per-game flag the
        subclass tracks via the ``_used`` / ``_triggered`` convention.

        Subclasses that store once-per-game state under a different
        attribute name should override this method, call
        ``super().on_revive(engine)`` first, and reset their own
        attribute. Drunk-style impersonators delegate the reset to
        the perceived character automatically — the engine walks
        ``acting_perceived_character()`` after invoking this hook.
        """
        # Refresh the first-night slot.
        self._first_night_pending = True
        # Clear the two conventional once-per-game flags so subclasses
        # that follow the Slayer / Virgin / Artist / Klutz pattern get
        # their slot back automatically.
        if hasattr(self, "_used"):
            try:
                self._used = False
            except Exception:  # pragma: no cover (defensive)
                pass
        if hasattr(self, "_triggered"):
            try:
                self._triggered = False
            except Exception:  # pragma: no cover (defensive)
                pass

    def would_act_tonight(self, engine: "Engine", night_number: int) -> bool:
        """Will this character actually do something tonight?

        Used by the engine's preset-driven night loop to decide whether
        to emit a storyteller-facing announcement for this step. The
        default checks two basic preconditions:

          * The character has a non-zero night order tonight.
          * The seated player is alive.

        Characters whose action is gated on additional conditions
        described in the rulebook (Undertaker on a no-execution day,
        Ravenkeeper while still alive, Scarlet Woman with no demon
        death today, …) override this method to apply the additional
        check, so the storyteller doesn't see a wake-up prompt for a
        character whose action won't fire.
        """
        if not self.acts_on_night(night_number):
            return False
        if self.player is None:
            return False
        if self.player.dead:
            return False
        return True

    # ------------------------------------------------------------------
    # Multi-target processing helpers.
    # ------------------------------------------------------------------

    def process_targets_with_goon_break(
        self,
        engine: "Engine",
        targets: "Iterable[Player]",
        action_fn: "Callable[[Player], None]",
    ) -> None:
        """Iterate ``targets`` in order, applying ``action_fn`` per seat.

        The canonical "do something to each picked seat, but stop if a
        Goon's drunkening interrupts mid-loop" pattern.

        **Order of operations per iteration**, consciously notify-FIRST:

          1. Pre-iteration ``has_ability`` guard. If the source has
             already lost their ability (e.g. an earlier target was
             the Goon and drunkened us), break.
          2. :meth:`engine.engine.Engine.notify_goon_chosen` — if the
             current target is the Goon's seat, fire the Goon's
             retort *before* this iteration's ``action_fn`` runs.
             The retort drunkens the source synchronously via the
             registry, so step 3 sees ``has_ability=False`` and skips.
          3. Post-notify ``has_ability`` guard. Break if the source
             just lost ability (the Goon was this target).
          4. ``action_fn(target)``. The source is guaranteed to have
             ability at this point.

        The notify-first ordering matches the rules: when a player
        picks the Goon, the Goon makes them drunk **immediately**,
        before the player's own ability resolves on the Goon. This
        makes user scenarios 2 and 5 fall out:

          * Shabaloth picks ``[Goon, A]`` → notify drunkens Shabaloth
            on Goon, action_fn never runs, neither A nor Goon dies.
          * Innkeeper picks ``[Goon, Other]`` → notify drunkens
            Innkeeper on Goon, action_fn never runs, neither target
            gets SAFE or DRUNK.

        And scenarios 3 and 6:

          * Shabaloth picks ``[A, Goon]`` → notify(A) is no-op,
            action_fn(A) kills A, notify(Goon) drunkens Shabaloth,
            loop breaks, Goon doesn't die.
          * Innkeeper picks ``[Other, Goon]`` → notify(Other) no-op,
            action_fn(Other) emits SAFE+DRUNK on Other, notify(Goon)
            drunkens Innkeeper, loop breaks, Goon gets nothing.

        The pick order comes from the player's multi-select prompt
        response, which already preserves click order (see
        ``ui/static/index.html``'s ``_selectedPlayerIds`` push/shift
        and the storyteller mirror in ``ui/static/storyteller.html``).
        Callers pass the order through unchanged.

        ``action_fn`` should not need its own drunk/poisoned guard —
        the helper guarantees the source has ability when action_fn
        is called. (Belt-and-braces guards inside action_fn are
        harmless but redundant.)

        Default implementation suits Shabaloth, Po, and any future
        "do thing to N targets" ability. Override on characters whose
        per-target action shape differs (Innkeeper applies SAFE to
        all picks plus DRUNK to one; the override passes its own
        ``action_fn`` to this base implementation rather than
        re-rolling the loop).
        """
        if self.player is None:
            return
        for t in targets or ():
            if t is None:
                continue
            # Step 1: pre-iteration gate. ``can_produce_real_effect``
            # combines authenticity (this is the seated role, not a
            # Lunatic / Drunk shadow) with ``has_ability`` (alive,
            # sober, healthy). Either failing → no real action and no
            # Goon retort fires; the loop bails out so the picks past
            # this point don't trigger anything either.
            if not self.can_produce_real_effect:
                break
            # Step 2: notify the Goon FIRST. If t is the Goon, the
            # retort drunkens the source synchronously; no-op otherwise.
            engine.notify_goon_chosen(self, t)
            # Step 3: post-notify guard. Skip action_fn for this
            # iteration AND break the loop — the source has lost their
            # ability and every remaining target is now moot.
            if not self.can_produce_real_effect:
                break
            # Step 4: source has ability, target is not the Goon, run
            # the per-target action.
            action_fn(t)

    def __repr__(self) -> str:  # pragma: no cover  (debug)
        pname = self.player.name if self.player else "—"
        return f"<{self.__class__.__name__} player={pname}>"


class StubCharacter(Character):
    """Generic placeholder for characters whose ability isn't yet coded.

    For any night the character is supposed to act on, we emit a single
    "(unimplemented) wake/sleep" prompt so the storyteller can still
    walk through the action by hand without the engine crashing.

    Concrete blank subclasses can inherit from this so they retain
    that helper prompt for free.
    """

    def ability(self, engine: "Engine", night_number: int) -> None:
        if self.player is None or self.player.dead:
            return
        if not self.acts_on_night(night_number):
            return
        engine.send_prompt(
            InformationPrompt(
                text=(
                    f"(Unimplemented) Walk through the {self.name}'s ability "
                    f"for {self.player.name}, then click Next."
                ),
                target_player_id=self.player.id,
                meta={"character": self.name, "step": "stub"},
            )
        )
