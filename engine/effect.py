"""Effect — ability output emitted onto a target seat with a token.

An :class:`Effect` is created by a Character's ability resolution and
stored in the Engine's effect registry. The registry is the single
source of truth for derived state like ``Player.drunk`` /
``Player.poisoned``, for the grimoire's per-seat reminder tokens, and
for the first phase of event resolution (active effects get to cancel
or redirect events before character reactions run).

See ``engine/engine.py`` for the registry + resolver implementation.
See ``engine/characters/*.py`` for concrete subclasses (each character
file defines its own Effect subclasses alongside the Character class).

Lifecycle
---------
Created when:
  * a Character's ability resolution calls ``engine.add_effect(...)``,
    and at that moment the source's ``has_ability`` is True.

Active iff (in the registry resolver's evaluation):
  * source is alive,
  * source is not droisoned by some *other* active effect targeting it
    (the self-source exemption: an effect's own contribution to its
    source's drunk/poison status is excluded when deciding the
    effect's own active state, so self-target effects don't trigger
    a self-deactivation cycle).
  * the effect's class declares it deactivates on source droisoned
    (``deactivate_on_source_droisoned = True`` is the default; the
    Drunk character's self-effect overrides to ``False`` so the Drunk
    is permanently drunk).

Deactivated when:
  * source becomes droisoned by another active effect. Re-activates if
    source sobers (and the effect hasn't been purged for some other
    reason in the meantime).

Purged from registry when:
  * source dies (default; subclasses override ``purge_on_source_death
    = False`` for markers that persist post-mortem — once-per-game
    NO ABILITY tokens, the Drunk's drunkness, …).
  * source's character changes (default; subclasses override
    ``purge_on_source_character_change = False`` rarely).
  * the effect's ``on_phase_boundary`` decides to expire and calls
    ``engine.purge_effect(self)``.
  * an explicit ``engine.purge_effect(...)`` call from any character.

Hooks (override as needed)
--------------------------
* :meth:`on_phase_boundary` — called for every effect (active and
  inactive) on every dusk/dawn transition. Default no-op. Override
  to expire phase-bounded effects.
* :meth:`on_source_death` — called when source's ``alive`` flips to
  False. Default purges per ``purge_on_source_death``.
* :meth:`on_source_character_change` — called when source's Character
  class is swapped. Default purges per
  ``purge_on_source_character_change``.
* :meth:`on_target_death` — called when a target of this *active*
  effect dies. Default no-op. Override e.g. Grandmother to grieve.
* :meth:`resolve_event` — phase-1 event resolution for active effects
  whose targets intersect the event's targets. Returns
  :class:`EventOutcome.CANCEL` to cancel, :class:`EventOutcome.REDIRECT`
  to retarget (with mutated ``event.targets``), or ``None`` to abstain.
  Default abstains.
* :meth:`token_kind_for_target` — token kind to render on a single
  target seat. Default returns ``self.kind``. Return ``None`` to
  suppress rendering on a particular target.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from engine.character import Character
    from engine.engine import Engine
    from engine.event import Event, EventOutcome


class Effect:
    """Ability output stored in the engine registry.

    Subclasses override class-level policy attributes and instance hooks.
    The base class is rarely instantiated directly — concrete effects
    live alongside their character files.
    """

    # ---- Identity / declaration (class-level; subclasses override) ----
    kind: str = "base"
    """Registry key and default token-kind. Subclasses set this to a
    short stable string (``"sailor_drunk"``, ``"monk_safe"``, ``"ft_red_herring"``,
    …). Used by ``effects_by_kind`` lookups and by the UI to map to
    icon assets. Multiple effects can share a kind across different
    characters (e.g. several poison-emitting characters may all use
    ``contributes_to_state="poisoned"`` while emitting distinct kinds
    like ``"poisoner_poisoned"`` / ``"pukka_poisoned"``)."""

    contributes_to_state: Optional[str] = None
    """Which derived ``Player.<state>`` this effect contributes to when
    active. Currently supported: ``"drunk"``, ``"poisoned"``, ``None``
    (no state contribution — info / protection / event-resolving only).
    The engine's ``resolve_droison_state`` ORs all active effects with
    matching ``contributes_to_state`` to compute the player's flag."""

    # ---- Lifecycle policies (class-level; subclasses override) -------
    purge_on_source_death: bool = True
    """If True, the effect is removed from the registry when the source
    player's ``alive`` flips to False. Override to ``False`` for markers
    that survive post-mortem (e.g. ``slayer_no_ability``)."""

    purge_on_source_character_change: bool = True
    """If True, the effect is removed when the source's Character class
    is swapped (Scarlet Woman → Imp). Rarely overridden."""

    deactivate_on_source_droisoned: bool = True
    """If True, the effect deactivates when the source becomes
    droisoned by another effect. Override to ``False`` for the
    Drunk's permanent self-drunk and for visual-only self-markers
    whose meaning doesn't toggle with the source's ability state."""

    def __init__(self, source: "Character", targets: List[int]) -> None:
        self.source: "Character" = source
        self.targets: List[int] = list(targets)
        # Set by ``Engine.add_effect``:
        self.id: int = 0
        # Set by the resolver. Initialized True so an effect added in
        # the middle of a frame is visible until the next resolve pass.
        self.is_active: bool = True

    # ---- Hooks (override as needed) ---------------------------------

    def on_phase_boundary(self, engine: "Engine", phase: str) -> None:
        """Called for every effect (active and inactive) on every
        dusk/dawn transition. ``phase`` is ``"dawn"`` or ``"dusk"``.
        Default no-op. Override to expire phase-bounded effects."""
        return None

    def on_source_death(self, engine: "Engine") -> None:
        """Called when the source player's ``alive`` flips to False.
        Default purges self per ``purge_on_source_death``."""
        if self.purge_on_source_death:
            engine.purge_effect(self)

    def on_source_character_change(
        self, engine: "Engine", new_character: str
    ) -> None:
        """Called when the source's Character class changes. Default
        purges self per ``purge_on_source_character_change``."""
        if self.purge_on_source_character_change:
            engine.purge_effect(self)

    def on_target_death(
        self, engine: "Engine", dead_target_id: int
    ) -> None:
        """Called when a target of this *active* effect dies. Default
        no-op. Override e.g. ``GrandmotherGrandchildEffect`` to fire
        the grief death."""
        return None

    def resolve_event(
        self, engine: "Engine", event: "Event"
    ) -> Optional["EventOutcome"]:
        """Phase-1 event resolution. Engine calls this for every
        *active* effect whose targets list intersects the event's
        targets. Return :class:`EventOutcome.CANCEL` to cancel the
        event, :class:`EventOutcome.REDIRECT` to retarget it (mutating
        ``event.targets`` first), or ``None`` to abstain. Default
        abstains."""
        return None

    def token_kind_for_target(
        self, target_id: int, engine: "Engine"
    ) -> Optional[str]:
        """Token kind to render on a single target seat. Default returns
        :attr:`kind`. Subclasses may vary by ticks remaining (Courtier
        rotates DRUNK 1/2/3) or return ``None`` to suppress rendering
        on a specific target (Moonchild's pending-kill is internal-only
        and shouldn't display)."""
        return self.kind

    # ---- Convenience -------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover (debug only)
        src = self.source.name if self.source is not None else "—"
        state = "active" if self.is_active else "inactive"
        return (
            f"<Effect#{self.id} {self.kind} src={src} "
            f"targets={self.targets} {state}>"
        )


_SETUP_EFFECT_REGISTRY: dict = {}
"""Class-level lookup of all :class:`SetupEffect` subclasses by their
``kind``. Populated automatically via ``__init_subclass__`` — each
subclass's declaration registers it. The engine consults this registry
to dispatch ``Engine.move_setup_token(kind, dest_chair_id)`` calls and
to drive ``Engine.refresh_setup_effects`` auto-fill iteration.

Lookup: ``_SETUP_EFFECT_REGISTRY["ft_red_herring"] →
FortuneTellerRedHerringEffect``. ``None`` returned if no class
registered for that kind."""


def get_setup_effect_class(kind: str) -> "type[SetupEffect] | None":
    """Look up a :class:`SetupEffect` subclass by its ``kind``.

    Returns ``None`` if no class registered. Used by the engine's
    drag-and-drop dispatch and auto-fill orchestrator."""
    return _SETUP_EFFECT_REGISTRY.get(kind)


def all_setup_effect_classes() -> "list[type[SetupEffect]]":
    """All :class:`SetupEffect` subclasses currently registered, in
    definition order. The auto-fill orchestrator iterates these
    sorted by ``autofill_priority`` (descending)."""
    return list(_SETUP_EFFECT_REGISTRY.values())


class SetupEffect(Effect):
    """Effect created during the pre-game setup phase.

    Setup effects live in the same registry as in-game effects but
    participate in setup-time concerns the in-game effects don't:

    * **Auto-fill** — when the source character is seated (or the
      bag composition changes), the engine asks each SetupEffect
      subclass to instantiate a default if no equivalent effect
      currently exists. Subclasses override
      :meth:`autofill_default_target` to pick a sensible default.
    * **Mutex** — declares which other effect kinds it conflicts
      with on the same target. Adding a new mutex-conflicting
      effect purges the conflicting one first. Replaces the pool's
      hand-rolled WW seen-vs-wrong / Lib seen-vs-wrong /
      Investigator seen-vs-wrong swap logic.
    * **Validation** — :meth:`can_target` is the predicate
      ``Engine.move_setup_token`` consults before re-targeting an
      effect via drag-and-drop. Replaces the per-pool-setter
      type/role validation.
    * **Setup-only lifecycle** — ``setup_only = True`` means the
      effect should be purged at the SETUP_END boundary (before
      night 1 runs). The Washerwoman / Librarian / Investigator
      first-night-only markers do this — by SETUP_END their info
      has been delivered. Effects that persist into play (FT red
      herring, Grandmother grandchild) leave it ``False``.

    The class is a thin extension of :class:`Effect` — all the
    ordinary lifecycle hooks (``on_phase_boundary``,
    ``on_source_death``, ``on_target_death``, ``resolve_event``,
    ``token_kind_for_target``) work identically.
    """

    # Mutex declarations: when this effect is added on a target,
    # any active effect of these kinds on the same target is purged
    # first. Used by ``Engine.move_setup_token`` for the drag-and-drop
    # token-swap UX.
    mutex_kinds: tuple = ()

    # Auto-fill priority — higher runs earlier when the bag changes.
    # Used by :meth:`Engine.refresh_setup_effects` to deterministically
    # order auto-fill of dependent effects (e.g. WW seen has a higher
    # priority than WW wrong because wrong needs to know what seen
    # picked).
    autofill_priority: int = 0

    # If True, the engine purges this effect at the SETUP_END
    # boundary (i.e. just before night 1 runs). The marker is purely
    # for the storyteller's setup-phase bookkeeping; it disappears
    # once the game proper begins.
    setup_only: bool = False

    @classmethod
    def can_target(cls, engine: "Engine", chair_id: int) -> bool:
        """Validation: may this effect's target be the given chair?

        Default: True (any chair). Override for type-restricted
        markers (WW seen → must be on a Townsfolk, FT red herring
        → must be on a Townsfolk/Outsider, etc.). Used by
        :meth:`Engine.move_setup_token` to validate drags."""
        return True

    @classmethod
    def autofill_default_target(
        cls,
        source: "Character",
        engine: "Engine",
    ) -> Optional[int]:
        """Compute a default target chair when this effect is auto-
        emitted on bag changes.

        Default: ``None`` — caller must pick. Override to pick a
        sensible default at auto-fill time. The chosen chair must
        satisfy ``can_target``."""
        return None

    def __init_subclass__(cls, **kwargs):
        """Auto-register every concrete :class:`SetupEffect` subclass
        in the module-level registry by its ``kind``. This is what
        makes ``Engine.move_setup_token(kind, dest_chair_id)`` /
        ``Engine.refresh_setup_effects()`` engine-side dispatchers
        work without any character-name hardcoding: the engine asks
        the registry "what class declares this kind?" and the
        right :class:`SetupEffect` subclass answers.

        Subclasses are skipped if their ``kind`` is still the
        inherited ``"base"`` placeholder (so abstract intermediate
        classes don't pollute the registry)."""
        super().__init_subclass__(**kwargs)
        kind = getattr(cls, "kind", "base")
        if kind and kind != "base":
            _SETUP_EFFECT_REGISTRY[kind] = cls
