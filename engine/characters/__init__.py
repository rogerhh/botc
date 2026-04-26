"""Concrete character implementations.

Each character lives in its own module so the engine can import them
lazily and the character roster stays open to extension. The
:data:`CHARACTER_REGISTRY` dict maps character name -> class so the
engine can build a fresh instance from a name string.
"""

from __future__ import annotations

from typing import Dict, Type

from engine.character import Character

# --- Townsfolk -------------------------------------------------------
from engine.characters.washerwoman import Washerwoman
from engine.characters.librarian import Librarian
from engine.characters.investigator import Investigator
from engine.characters.chef import Chef
from engine.characters.empath import Empath
from engine.characters.fortune_teller import FortuneTeller
from engine.characters.undertaker import Undertaker
from engine.characters.monk import Monk
from engine.characters.ravenkeeper import Ravenkeeper
from engine.characters.virgin import Virgin
from engine.characters.slayer import Slayer
from engine.characters.soldier import Soldier
from engine.characters.mayor import Mayor

# --- Outsiders -------------------------------------------------------
from engine.characters.butler import Butler
from engine.characters.drunk import Drunk
from engine.characters.recluse import Recluse
from engine.characters.saint import Saint

# --- Minions ---------------------------------------------------------
from engine.characters.poisoner import Poisoner
from engine.characters.spy import Spy
from engine.characters.scarlet_woman import ScarletWoman
from engine.characters.baron import Baron

# --- Demons ----------------------------------------------------------
from engine.characters.imp import Imp


# Add new implementations here as they're written.
_IMPLEMENTED: tuple = (
    # Townsfolk
    Washerwoman,
    Librarian,
    Investigator,
    Chef,
    Empath,
    FortuneTeller,
    Undertaker,
    Monk,
    Ravenkeeper,
    Virgin,
    Slayer,
    Soldier,
    Mayor,
    # Outsiders
    Butler,
    Drunk,
    Recluse,
    Saint,
    # Minions
    Poisoner,
    Spy,
    ScarletWoman,
    Baron,
    # Demons
    Imp,
)


CHARACTER_REGISTRY: Dict[str, Type[Character]] = {
    cls.name: cls for cls in _IMPLEMENTED
}


def get_character_class(name: str) -> Type[Character]:
    """Return the Character subclass for a given character name."""
    if name not in CHARACTER_REGISTRY:
        raise KeyError(
            f"No implementation registered for character {name!r}. "
            f"Known: {sorted(CHARACTER_REGISTRY)}"
        )
    return CHARACTER_REGISTRY[name]


def list_implemented_names() -> list[str]:
    """Names of all characters with concrete implementations."""
    return sorted(CHARACTER_REGISTRY)


__all__ = [
    "CHARACTER_REGISTRY",
    "get_character_class",
    "list_implemented_names",
    # Townsfolk
    "Washerwoman",
    "Librarian",
    "Investigator",
    "Chef",
    "Empath",
    "FortuneTeller",
    "Undertaker",
    "Monk",
    "Ravenkeeper",
    "Virgin",
    "Slayer",
    "Soldier",
    "Mayor",
    # Outsiders
    "Butler",
    "Drunk",
    "Recluse",
    "Saint",
    # Minions
    "Poisoner",
    "Spy",
    "ScarletWoman",
    "Baron",
    # Demons
    "Imp",
]
