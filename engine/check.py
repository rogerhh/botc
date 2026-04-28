"""Detection checks.

A :class:`Check` describes a question one character asks of another at
ability time: "what alignment are you?", "are you the Demon?", "are
you the Slayer?". Detection-style characters (Washerwoman, Librarian,
Investigator, Chef, Empath, Fortune Teller, Undertaker, Ravenkeeper,
Slayer, Virgin, …) construct a Check and pass it to
:meth:`Character.check`, which:

  1. invokes the target's :meth:`Character.registers_as` with the
     check's context (so the override on Spy / Recluse knows what
     attribute is being asked about and can offer a *reduced* eligible
     list when only ``alignment`` or ``char_type`` matters), and

  2. compares the registered result's ``attribute`` against the
     check's ``passes`` set.

Three attributes are supported:

  * ``"name"`` — the registered character's :attr:`Character.name`.
    Used by the WW / Lib / Inv (1-of-2 readings) and by the
    Undertaker / Ravenkeeper (which show a specific role on the
    player's phone).
  * ``"char_type"`` — the registered character's :attr:`char_type`.
    Used by the Slayer (Demon-only kill), Virgin (TF-only execute), and
    the alignment-style detectors below when alignment derives from
    type.
  * ``"alignment"`` — the registered character's effective alignment.
    Used by the Empath / Chef.

When a Check's ``attribute`` is alignment or char_type, the override
on Spy / Recluse may return one of the inert *stubs*
(:mod:`engine.characters.stubs`) — e.g. ``GoodStub`` to mean "some
good role" without committing to a specific name. The check then
inspects the requested attribute on the stub, which carries the
necessary metadata. This keeps the ST prompt small (yes/no on
alignment, a few options for char_type) instead of forcing a full
character pick.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Sequence, Tuple

from engine.enums import Alignment, CharType

if TYPE_CHECKING:
    from engine.character import Character


@dataclass(frozen=True)
class Check:
    """Specification of a detection-side check.

    Attributes:
        attribute: Which attribute of the registered Character we're
            inspecting. One of ``"name"``, ``"char_type"``,
            ``"alignment"``.
        passes: The values that, if the inspected attribute equals one
            of them, count as a *pass* of the check. For
            ``attribute == "name"`` this is a tuple of role names
            (strings). For ``"char_type"`` it is a tuple of
            :class:`CharType` values. For ``"alignment"`` it is a
            tuple of :class:`Alignment` values.
        detector_name: Name of the detector character running the
            check (for ST prompt text and audit metadata).
        detector_player_id: Seat id of the detector, when known. Used
            by the override's ST prompt to line up with the detector's
            UI panel.
        extra_meta: Extra metadata merged into the override's prompt
            when one fires. Useful for the detector to attach a
            "step_for" tag the UI can render.
    """

    attribute: str
    passes: Tuple = ()
    detector_name: str = ""
    detector_player_id: int = -1
    extra_meta: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Convenience inspectors used by Spy / Recluse overrides to decide
    # whether they could plausibly fake something the check would
    # accept.
    # ------------------------------------------------------------------

    def passes_value(self, value) -> bool:
        """True iff ``value`` is in this check's accepted passes."""
        return value in self.passes

    def char_types_in_passes(self) -> Sequence[CharType]:
        """Char types relevant to this check's passes.

        For ``attribute == "char_type"`` returns ``self.passes`` directly.
        For ``attribute == "alignment"`` returns the char_types whose
        default alignment is in ``self.passes`` (e.g. alignment GOOD
        → TF + Outsider).
        For ``attribute == "name"`` returns the empty tuple (the
        attribute lookup walks character names, not types).
        """
        if self.attribute == "char_type":
            return tuple(self.passes)
        if self.attribute == "alignment":
            out = []
            for ct in (
                CharType.TOWNSFOLK,
                CharType.OUTSIDER,
                CharType.MINION,
                CharType.DEMON,
            ):
                if ct.default_alignment in self.passes:
                    out.append(ct)
            return tuple(out)
        return ()

    def could_register_as_pass(
        self, registration_categories: Sequence[CharType]
    ) -> bool:
        """Could a registers_as override that can fake any of
        ``registration_categories`` plausibly produce a passing answer?

        Used by setup-time eligibility ("can this token apply to this
        chair?"): we want to know whether a player might *eventually*
        register as something the check accepts.
        """
        cat_set = set(registration_categories)
        if self.attribute == "char_type":
            return any(ct in cat_set for ct in self.passes)
        if self.attribute == "alignment":
            return any(
                ct in cat_set
                for ct in (
                    CharType.TOWNSFOLK,
                    CharType.OUTSIDER,
                    CharType.MINION,
                    CharType.DEMON,
                )
                if ct.default_alignment in self.passes
            )
        # attribute == "name": override could in principle pick any of
        # the names in passes; we don't know without looking up types
        # per name. Conservative answer: True.
        return True

    def registration_matters(
        self, registration_categories: Sequence[CharType]
    ) -> bool:
        """Could the override's registration choice *change* the result?

        Returns True when the override's possible registrations are
        a mix of pass and fail for this check — meaning the
        Storyteller's pick will affect whether the check passes.

        Returns False when every category the override could fake
        produces the same answer (all pass or all fail). In that case
        the override should NOT fire — the default registration (true
        name) gives the correct answer.

        Used by Spy / Recluse to decide whether to prompt the ST.
        """
        if not registration_categories:
            return False
        if self.attribute == "name":
            # Without specific names we can't be sure; conservative
            # answer is "yes, prompt the ST" so they can pick.
            return True
        results = set()
        for ct in registration_categories:
            if self.attribute == "char_type":
                results.add(ct in self.passes)
            elif self.attribute == "alignment":
                results.add(ct.default_alignment in self.passes)
            if len(results) > 1:
                return True
        return False


# ---------------------------------------------------------------------------
# Helpers for resolving the registered "name" → its char_type / alignment.
# ---------------------------------------------------------------------------


def attribute_value(
    engine: "Engine",  # type: ignore[name-defined]
    registered_name: str,
    attribute: str,
):
    """Look up the requested attribute on a registered character name.

    Stubs (TownsfolkStub, …) have their own char_type / stub_alignment;
    real script characters look up via the engine's helpers. ``name``
    attribute is returned as-is.
    """
    if attribute == "name":
        return registered_name
    # Build a fresh instance to read its attributes. Cheap (Character
    # subclasses are dataclass-y) and avoids hardcoding stub names here.
    inst = engine.build_character(registered_name)
    if attribute == "char_type":
        return inst.char_type
    if attribute == "alignment":
        # Stubs carry an explicit ``stub_alignment`` field; real
        # characters have no per-class alignment (alignment lives on
        # the Player). Default alignment for a real character is the
        # default of its char_type (TF/Outsider → GOOD, Minion/Demon →
        # EVIL).
        stub_align = getattr(inst, "stub_alignment", None)
        if stub_align is not None and getattr(inst, "is_stub", False):
            return stub_align
        return inst.char_type.default_alignment
    raise ValueError(f"Unknown attribute {attribute!r}")
