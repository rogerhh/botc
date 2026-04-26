# Project Rules

Rules to follow when working on this Blood on the Clocktower server.

## Drunk / Poisoned Information

When a character is drunk or poisoned and the ability shows them
information, the Storyteller is always given a chance to set the
shown answer. The engine pre-fills a *wrong* default so the
Storyteller can simply hit Next and the player gets bad info.

- **Binary info (yes/no, exactly two options):** the engine pre-fills
  the *flipped* (wrong) answer. The Storyteller sees the prompt with
  the wrong answer highlighted and may change it; either way it is
  sent on Next.

- **Range of options (3+ choices, e.g. Empath 0/1/2, Washerwoman
  picking a character):** the engine pre-fills a *random wrong*
  option. The Storyteller may change it before it goes to the player.

- **UI language:** never use the words "confirm" or "override"
  anywhere the Storyteller sees. The drunk/poisoned info prompt has
  the wrong answer pre-selected and is dispatched by hitting Next (or
  Yes/No for binary prompts, with the wrong answer highlighted as the
  default).

This applies to every info character (Empath, Fortune Teller,
Washerwoman, Librarian, Investigator, Chef, Undertaker, Ravenkeeper,
etc.) and to any future info ability added to the script.
