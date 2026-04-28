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
from typing import TYPE_CHECKING, List, Optional, Sequence

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
        }),
        CharType.OUTSIDER: frozenset({
            "ft_red_herring", "librarian_outsider",
            "washerwoman_wrong", "librarian_wrong", "investigator_wrong",
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
        """Order at which this character acts on a given night."""
        if night_number == 1:
            return self.first_night_order
        return self.other_night_order

    def acts_on_night(self, night_number: int) -> bool:
        return self.night_order(night_number) > 0

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
