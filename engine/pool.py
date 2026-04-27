"""Character pool / setup picks.

Owned by the engine post-refactor. The pool tracks:

  * The set of character names in play (the "bag").
  * The Drunk's fake-Townsfolk role (off-bag).
  * The Fortune Teller's red-herring role (in-bag).
  * The Washerwoman's seen-Townsfolk role (in-bag).
  * The Washerwoman's WRONG role (in-bag).

Auto-fill rules ensure that whenever the FT / WW are in the pool their
dependent slots are non-None as long as a valid candidate exists. The
slots are cleared when the relevant owner role leaves the pool.

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

    def _autofill_ft_red_herring(self) -> None:
        if "Fortune Teller" not in self._names:
            self._ft_red_herring = None
            return
        if self._ft_red_herring in self._names:
            return
        good = self._good_in_pool()
        non_self = [n for n in good if n != "Fortune Teller"]
        candidates = non_self or good
        self._ft_red_herring = random.choice(candidates) if candidates else None

    def _autofill_washerwoman_townsfolk(self) -> None:
        if "Washerwoman" not in self._names:
            self._washerwoman_townsfolk = None
            return
        if self._washerwoman_townsfolk in self._names:
            return
        townsfolk = self._townsfolk_in_pool()
        non_self = [n for n in townsfolk if n != "Washerwoman"]
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
            return True

    def clear(self) -> None:
        with self._lock:
            self._names.clear()
            self._drunk_fake = None
            self._ft_red_herring = None
            self._washerwoman_townsfolk = None
            self._washerwoman_wrong = None

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
            self._autofill_ft_red_herring()
            self._autofill_washerwoman_townsfolk()
            if (
                self._washerwoman_wrong is not None
                and self._washerwoman_wrong == self._washerwoman_townsfolk
            ):
                self._washerwoman_wrong = None
            self._autofill_washerwoman_wrong()
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
