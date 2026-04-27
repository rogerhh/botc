"""Character pool / setup picks.

Owned by the engine post-refactor. The pool tracks:

  * The set of character names in play (the "bag").
  * The Drunk's fake-Townsfolk role (off-bag).
  * The Fortune Teller's red-herring role (in-bag).
  * The Washerwoman's seen-Townsfolk role (in-bag).
  * The Washerwoman's WRONG role (in-bag).
  * The Librarian's seen-Outsider role (in-bag).
  * The Investigator's seen-Minion role (in-bag).

Auto-fill rules ensure that whenever the FT / WW / Librarian /
Investigator are in the pool their dependent slots are non-None as long
as a valid candidate exists. The slots are cleared when the relevant
owner role leaves the pool.

The class is thread-safe; the UI's HTTP handlers and the engine's
character setup paths both read from and write to it.
"""

from __future__ import annotations

import random
import threading
from typing import List, Optional

from engine import script as script_data
from engine.enums import CharType


class CharacterPool:
    """The set of characters chosen by the storyteller for the game.

    Each entry is a character name. Order is preserved (storyteller
    insertion order). The same character is never present twice.

    See module docstring for the four auxiliary slots (Drunk fake,
    FT red herring, WW seen-Townsfolk, WW wrong).
    """

    def __init__(self) -> None:
        self._names: List[str] = []
        self._drunk_fake: Optional[str] = None
        self._ft_red_herring: Optional[str] = None
        self._washerwoman_townsfolk: Optional[str] = None
        self._washerwoman_wrong: Optional[str] = None
        self._librarian_outsider: Optional[str] = None
        self._librarian_wrong: Optional[str] = None
        self._investigator_minion: Optional[str] = None
        self._investigator_wrong: Optional[str] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Accessors.
    # ------------------------------------------------------------------

    def list(self) -> List[str]:
        with self._lock:
            return list(self._names)

    def drunk_fake(self) -> Optional[str]:
        with self._lock:
            return self._drunk_fake

    def ft_red_herring(self) -> Optional[str]:
        with self._lock:
            return self._ft_red_herring

    def washerwoman_townsfolk(self) -> Optional[str]:
        with self._lock:
            return self._washerwoman_townsfolk

    def washerwoman_wrong(self) -> Optional[str]:
        with self._lock:
            return self._washerwoman_wrong

    def librarian_outsider(self) -> Optional[str]:
        with self._lock:
            return self._librarian_outsider

    def librarian_wrong(self) -> Optional[str]:
        with self._lock:
            return self._librarian_wrong

    def investigator_minion(self) -> Optional[str]:
        with self._lock:
            return self._investigator_minion

    def investigator_wrong(self) -> Optional[str]:
        with self._lock:
            return self._investigator_wrong

    # ------------------------------------------------------------------
    # Internal helpers (assume self._lock is held).
    # ------------------------------------------------------------------

    def _good_in_pool(self) -> List[str]:
        good: List[str] = []
        for n in self._names:
            spec = script_data.SCRIPT_BY_NAME.get(n)
            if spec is None:
                continue
            if spec.char_type in (CharType.TOWNSFOLK, CharType.OUTSIDER):
                good.append(n)
        return good

    def _townsfolk_in_pool(self) -> List[str]:
        return [
            n for n in self._names
            if (script_data.SCRIPT_BY_NAME.get(n)
                and script_data.SCRIPT_BY_NAME[n].char_type
                is CharType.TOWNSFOLK)
        ]

    def _outsiders_in_pool(self) -> List[str]:
        return [
            n for n in self._names
            if (script_data.SCRIPT_BY_NAME.get(n)
                and script_data.SCRIPT_BY_NAME[n].char_type
                is CharType.OUTSIDER)
        ]

    def _minions_in_pool(self) -> List[str]:
        return [
            n for n in self._names
            if (script_data.SCRIPT_BY_NAME.get(n)
                and script_data.SCRIPT_BY_NAME[n].char_type
                is CharType.MINION)
        ]

    def _autofill_ft_red_herring(self) -> None:
        """Pick a red herring for the FT.

        Self-avoidance: if the slot is currently sitting on
        ``"Fortune Teller"`` (a degenerate auto-pick from a moment when
        the FT was the only Good role in the pool) and a non-self Good
        candidate has since become available, switch to a non-self
        candidate immediately. The rules permit the FT to be its own
        red herring, but it makes for a degenerate game — the
        storyteller can still drag the token back to the FT manually.
        """
        if "Fortune Teller" not in self._names:
            self._ft_red_herring = None
            return
        good = self._good_in_pool()
        non_self = [n for n in good if n != "Fortune Teller"]
        if self._ft_red_herring == "Fortune Teller" and non_self:
            self._ft_red_herring = random.choice(non_self)
            return
        if self._ft_red_herring in self._names:
            return
        candidates = non_self or good
        self._ft_red_herring = random.choice(candidates) if candidates else None

    def _autofill_washerwoman_townsfolk(self) -> None:
        """Pick the seen-Townsfolk for the WW.

        Self-avoidance: if the slot is currently sitting on
        ``"Washerwoman"`` (a degenerate auto-pick from a moment when
        the WW was the only Townsfolk in the pool) and another
        Townsfolk has since been added, switch to it immediately. The
        rules don't forbid the WW from being her own seen-Townsfolk,
        but it makes for a degenerate game — the storyteller can drag
        the token back to the WW manually if they really want it.
        """
        if "Washerwoman" not in self._names:
            self._washerwoman_townsfolk = None
            return
        townsfolk = self._townsfolk_in_pool()
        non_self = [n for n in townsfolk if n != "Washerwoman"]
        if self._washerwoman_townsfolk == "Washerwoman" and non_self:
            self._washerwoman_townsfolk = random.choice(non_self)
            return
        if self._washerwoman_townsfolk in self._names:
            return
        candidates = non_self or townsfolk
        self._washerwoman_townsfolk = (
            random.choice(candidates) if candidates else None
        )

    def _autofill_washerwoman_wrong(self) -> None:
        if "Washerwoman" not in self._names:
            self._washerwoman_wrong = None
            return
        if (
            self._washerwoman_wrong in self._names
            and self._washerwoman_wrong != "Washerwoman"
            and self._washerwoman_wrong != self._washerwoman_townsfolk
        ):
            return
        candidates = [
            n for n in self._names
            if n != "Washerwoman" and n != self._washerwoman_townsfolk
        ]
        self._washerwoman_wrong = (
            random.choice(candidates) if candidates else None
        )

    def _autofill_librarian_outsider(self) -> None:
        """If the Librarian is in the pool but no seen-Outsider is set,
        pick one uniformly at random from the Outsiders in the pool.
        Caller must already hold self._lock.

        If there are no Outsiders in the pool the slot stays ``None``.
        That's deliberate — the Librarian's first-night ability shows
        the "0 Outsiders" reading when no Outsider role is selected,
        which is exactly the rules-correct outcome when no Outsiders
        are in play.
        """
        if "Librarian" not in self._names:
            self._librarian_outsider = None
            return
        if self._librarian_outsider in self._names:
            spec = script_data.SCRIPT_BY_NAME.get(self._librarian_outsider)
            if spec is not None and spec.char_type is CharType.OUTSIDER:
                return
        outsiders = self._outsiders_in_pool()
        self._librarian_outsider = (
            random.choice(outsiders) if outsiders else None
        )

    def _autofill_investigator_minion(self) -> None:
        """If the Investigator is in the pool but no seen-Minion is
        set, pick one uniformly at random from the Minions in the
        pool. Caller must already hold self._lock.
        """
        if "Investigator" not in self._names:
            self._investigator_minion = None
            return
        if self._investigator_minion in self._names:
            spec = script_data.SCRIPT_BY_NAME.get(self._investigator_minion)
            if spec is not None and spec.char_type is CharType.MINION:
                return
        minions = self._minions_in_pool()
        self._investigator_minion = (
            random.choice(minions) if minions else None
        )

    def _autofill_librarian_wrong(self) -> None:
        """If the Librarian is in the pool but no WRONG-role is set,
        pick one uniformly at random from the in-pool roles that are
        *neither* the Librarian herself *nor* the currently-set
        seen-Outsider role. Caller must already hold self._lock.

        If the Librarian's seen-Outsider slot is empty (no Outsiders
        in play — the "0 Outsiders" reading), the WRONG slot stays
        empty too: there's no pair of players to point at, so no
        token to place.
        """
        if "Librarian" not in self._names:
            self._librarian_wrong = None
            return
        # No seen-Outsider → no WRONG either (the "0 Outsiders"
        # reading skips both reminder tokens).
        if not self._librarian_outsider:
            self._librarian_wrong = None
            return
        if (
            self._librarian_wrong in self._names
            and self._librarian_wrong != "Librarian"
            and self._librarian_wrong != self._librarian_outsider
        ):
            return
        candidates = [
            n for n in self._names
            if n != "Librarian" and n != self._librarian_outsider
        ]
        self._librarian_wrong = (
            random.choice(candidates) if candidates else None
        )

    def _autofill_investigator_wrong(self) -> None:
        """If the Investigator is in the pool but no WRONG-role is
        set, pick one uniformly at random from the in-pool roles that
        are *neither* the Investigator herself *nor* the
        currently-set seen-Minion role. Caller must already hold
        self._lock.
        """
        if "Investigator" not in self._names:
            self._investigator_wrong = None
            return
        if not self._investigator_minion:
            self._investigator_wrong = None
            return
        if (
            self._investigator_wrong in self._names
            and self._investigator_wrong != "Investigator"
            and self._investigator_wrong != self._investigator_minion
        ):
            return
        candidates = [
            n for n in self._names
            if n != "Investigator" and n != self._investigator_minion
        ]
        self._investigator_wrong = (
            random.choice(candidates) if candidates else None
        )

    # ------------------------------------------------------------------
    # Mutators.
    # ------------------------------------------------------------------

    def add(self, name: str) -> bool:
        with self._lock:
            if name in self._names:
                return False
            self._names.append(name)
            if self._drunk_fake == name:
                self._drunk_fake = None
            self._autofill_ft_red_herring()
            self._autofill_washerwoman_townsfolk()
            self._autofill_washerwoman_wrong()
            self._autofill_librarian_outsider()
            self._autofill_librarian_wrong()
            self._autofill_investigator_minion()
            self._autofill_investigator_wrong()
            return True

    def remove(self, name: str) -> bool:
        with self._lock:
            if name not in self._names:
                return False
            self._names.remove(name)
            if name == "Drunk":
                self._drunk_fake = None
            if name == "Fortune Teller":
                self._ft_red_herring = None
            if name == "Washerwoman":
                self._washerwoman_townsfolk = None
                self._washerwoman_wrong = None
            if name == "Librarian":
                self._librarian_outsider = None
                self._librarian_wrong = None
            if name == "Investigator":
                self._investigator_minion = None
                self._investigator_wrong = None
            if name == self._ft_red_herring:
                self._ft_red_herring = None
                self._autofill_ft_red_herring()
            if name == self._washerwoman_townsfolk:
                self._washerwoman_townsfolk = None
                self._autofill_washerwoman_townsfolk()
                self._autofill_washerwoman_wrong()
            if name == self._washerwoman_wrong:
                self._washerwoman_wrong = None
                self._autofill_washerwoman_wrong()
            if name == self._librarian_outsider:
                self._librarian_outsider = None
                self._autofill_librarian_outsider()
                self._autofill_librarian_wrong()
            if name == self._librarian_wrong:
                self._librarian_wrong = None
                self._autofill_librarian_wrong()
            if name == self._investigator_minion:
                self._investigator_minion = None
                self._autofill_investigator_minion()
                self._autofill_investigator_wrong()
            if name == self._investigator_wrong:
                self._investigator_wrong = None
                self._autofill_investigator_wrong()
            return True

    def clear(self) -> None:
        with self._lock:
            self._names.clear()
            self._drunk_fake = None
            self._ft_red_herring = None
            self._washerwoman_townsfolk = None
            self._washerwoman_wrong = None
            self._librarian_outsider = None
            self._librarian_wrong = None
            self._investigator_minion = None
            self._investigator_wrong = None

    def set_many(self, names: List[str]) -> List[str]:
        seen: set = set()
        deduped: List[str] = []
        for n in names:
            if isinstance(n, str) and n and n not in seen:
                seen.add(n)
                deduped.append(n)
        with self._lock:
            self._names = deduped
            if "Drunk" not in deduped or (self._drunk_fake in deduped):
                self._drunk_fake = None
            if (
                "Fortune Teller" not in deduped
                or self._ft_red_herring not in deduped
            ):
                self._ft_red_herring = None
            if (
                "Washerwoman" not in deduped
                or self._washerwoman_townsfolk not in deduped
            ):
                self._washerwoman_townsfolk = None
            if (
                "Washerwoman" not in deduped
                or self._washerwoman_wrong not in deduped
                or self._washerwoman_wrong == "Washerwoman"
            ):
                self._washerwoman_wrong = None
            if (
                "Librarian" not in deduped
                or self._librarian_outsider not in deduped
            ):
                self._librarian_outsider = None
            if (
                "Librarian" not in deduped
                or self._librarian_wrong not in deduped
                or self._librarian_wrong == "Librarian"
            ):
                self._librarian_wrong = None
            if (
                "Investigator" not in deduped
                or self._investigator_minion not in deduped
            ):
                self._investigator_minion = None
            if (
                "Investigator" not in deduped
                or self._investigator_wrong not in deduped
                or self._investigator_wrong == "Investigator"
            ):
                self._investigator_wrong = None
            self._autofill_ft_red_herring()
            self._autofill_washerwoman_townsfolk()
            if (
                self._washerwoman_wrong is not None
                and self._washerwoman_wrong == self._washerwoman_townsfolk
            ):
                self._washerwoman_wrong = None
            self._autofill_washerwoman_wrong()
            self._autofill_librarian_outsider()
            if (
                self._librarian_wrong is not None
                and self._librarian_wrong == self._librarian_outsider
            ):
                self._librarian_wrong = None
            self._autofill_librarian_wrong()
            self._autofill_investigator_minion()
            if (
                self._investigator_wrong is not None
                and self._investigator_wrong == self._investigator_minion
            ):
                self._investigator_wrong = None
            self._autofill_investigator_wrong()
            return list(self._names)

    def set_drunk_fake(self, name: Optional[str]) -> Optional[str]:
        with self._lock:
            if name is None:
                self._drunk_fake = None
                return None
            if "Drunk" not in self._names:
                raise ValueError("Drunk is not in the pool")
            if name in self._names:
                raise ValueError(
                    "Drunk's pretend role can't be a real role in play")
            self._drunk_fake = name
            return self._drunk_fake

    def set_ft_red_herring(self, name: Optional[str]) -> Optional[str]:
        with self._lock:
            if name is None:
                self._ft_red_herring = None
                return None
            if "Fortune Teller" not in self._names:
                raise ValueError("Fortune Teller is not in the pool")
            if name not in self._names:
                raise ValueError(
                    "FT's red-herring role must already be in the pool")
            spec = script_data.SCRIPT_BY_NAME.get(name)
            if spec is None:
                raise ValueError(f"unknown character {name!r}")
            if spec.char_type not in (CharType.TOWNSFOLK, CharType.OUTSIDER):
                raise ValueError(
                    "FT's red-herring role must be a Townsfolk or Outsider")
            self._ft_red_herring = name
            return self._ft_red_herring

    def set_washerwoman_townsfolk(self, name: Optional[str]) -> Optional[str]:
        with self._lock:
            if name is None:
                self._washerwoman_townsfolk = None
                if self._washerwoman_wrong == name:
                    self._washerwoman_wrong = None
                self._autofill_washerwoman_wrong()
                return None
            if "Washerwoman" not in self._names:
                raise ValueError("Washerwoman is not in the pool")
            if name not in self._names:
                raise ValueError(
                    "WW's seen Townsfolk must already be in the pool")
            spec = script_data.SCRIPT_BY_NAME.get(name)
            if spec is None:
                raise ValueError(f"unknown character {name!r}")
            if spec.char_type is not CharType.TOWNSFOLK:
                raise ValueError(
                    "WW's seen role must be a Townsfolk")
            self._washerwoman_townsfolk = name
            if self._washerwoman_wrong == name:
                self._washerwoman_wrong = None
            self._autofill_washerwoman_wrong()
            return self._washerwoman_townsfolk

    def set_washerwoman_wrong(self, name: Optional[str]) -> Optional[str]:
        with self._lock:
            if name is None:
                self._washerwoman_wrong = None
                return None
            if "Washerwoman" not in self._names:
                raise ValueError("Washerwoman is not in the pool")
            if name not in self._names:
                raise ValueError(
                    "WW's WRONG role must already be in the pool")
            if name == "Washerwoman":
                raise ValueError(
                    "WW's WRONG token can't sit on the Washerwoman herself")
            if name == self._washerwoman_townsfolk:
                raise ValueError(
                    "WW's WRONG role must differ from the seen Townsfolk")
            spec = script_data.SCRIPT_BY_NAME.get(name)
            if spec is None:
                raise ValueError(f"unknown character {name!r}")
            self._washerwoman_wrong = name
            return self._washerwoman_wrong

    def set_librarian_outsider(self, name: Optional[str]) -> Optional[str]:
        """Set the Librarian's seen Outsider role, or pass None to
        clear it.

        Raises ValueError if the pool doesn't contain the Librarian,
        if ``name`` isn't already in the pool, or if ``name`` isn't
        an Outsider.
        """
        with self._lock:
            if name is None:
                self._librarian_outsider = None
                # No seen-Outsider → no WRONG either.
                self._librarian_wrong = None
                return None
            if "Librarian" not in self._names:
                raise ValueError("Librarian is not in the pool")
            if name not in self._names:
                raise ValueError(
                    "Librarian's seen Outsider must already be in the pool")
            spec = script_data.SCRIPT_BY_NAME.get(name)
            if spec is None:
                raise ValueError(f"unknown character {name!r}")
            if spec.char_type is not CharType.OUTSIDER:
                raise ValueError(
                    "Librarian's seen role must be an Outsider")
            self._librarian_outsider = name
            # Re-roll WRONG if it now collides with the new seen-TF.
            if self._librarian_wrong == name:
                self._librarian_wrong = None
            self._autofill_librarian_wrong()
            return self._librarian_outsider

    def set_librarian_wrong(self, name: Optional[str]) -> Optional[str]:
        """Set the Librarian's WRONG role, or pass None to clear it.

        Raises ValueError if the pool doesn't contain the Librarian,
        if ``name`` isn't already in the pool, if ``name`` is the
        Librarian herself, or if ``name`` is the same as the seen
        Outsider slot (the two tokens point at *different* players).
        """
        with self._lock:
            if name is None:
                self._librarian_wrong = None
                return None
            if "Librarian" not in self._names:
                raise ValueError("Librarian is not in the pool")
            if name not in self._names:
                raise ValueError(
                    "Librarian's WRONG role must already be in the pool")
            if name == "Librarian":
                raise ValueError(
                    "Librarian's WRONG token can't sit on the Librarian "
                    "herself")
            if name == self._librarian_outsider:
                raise ValueError(
                    "Librarian's WRONG role must differ from the seen "
                    "Outsider")
            spec = script_data.SCRIPT_BY_NAME.get(name)
            if spec is None:
                raise ValueError(f"unknown character {name!r}")
            self._librarian_wrong = name
            return self._librarian_wrong

    def set_investigator_minion(self, name: Optional[str]) -> Optional[str]:
        """Set the Investigator's seen Minion role, or pass None to
        clear it.

        Raises ValueError if the pool doesn't contain the Investigator,
        if ``name`` isn't already in the pool, or if ``name`` isn't a
        Minion.
        """
        with self._lock:
            if name is None:
                self._investigator_minion = None
                self._investigator_wrong = None
                return None
            if "Investigator" not in self._names:
                raise ValueError("Investigator is not in the pool")
            if name not in self._names:
                raise ValueError(
                    "Investigator's seen Minion must already be in the pool")
            spec = script_data.SCRIPT_BY_NAME.get(name)
            if spec is None:
                raise ValueError(f"unknown character {name!r}")
            if spec.char_type is not CharType.MINION:
                raise ValueError(
                    "Investigator's seen role must be a Minion")
            self._investigator_minion = name
            if self._investigator_wrong == name:
                self._investigator_wrong = None
            self._autofill_investigator_wrong()
            return self._investigator_minion

    def set_investigator_wrong(self, name: Optional[str]) -> Optional[str]:
        """Set the Investigator's WRONG role, or pass None to clear
        it.

        Raises ValueError if the pool doesn't contain the
        Investigator, if ``name`` isn't already in the pool, if
        ``name`` is the Investigator herself, or if ``name`` is the
        same as the seen-Minion slot.
        """
        with self._lock:
            if name is None:
                self._investigator_wrong = None
                return None
            if "Investigator" not in self._names:
                raise ValueError("Investigator is not in the pool")
            if name not in self._names:
                raise ValueError(
                    "Investigator's WRONG role must already be in the pool")
            if name == "Investigator":
                raise ValueError(
                    "Investigator's WRONG token can't sit on the "
                    "Investigator herself")
            if name == self._investigator_minion:
                raise ValueError(
                    "Investigator's WRONG role must differ from the seen "
                    "Minion")
            spec = script_data.SCRIPT_BY_NAME.get(name)
            if spec is None:
                raise ValueError(f"unknown character {name!r}")
            self._investigator_wrong = name
            return self._investigator_wrong
