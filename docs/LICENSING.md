# Licensing

This project is **GPL-3.0** (`LICENSE`). What follows is about the project it forks, because
that project's own statements about its licence disagree with each other and this repository
used to repeat only one of them.

## What upstream says

`plugin/companion.py` is a hardened fork of `pwnios.py` from
[`BraedenP232/PwnIOS`](https://github.com/BraedenP232/PwnIOS), the backend of the paid iOS app
"Pwnagotchi Companion". Measured on that repository's default branch at commit `20a3aca0d39c`
(2026-05-15):

- `pwnios.py` declares, in the plugin class: `__license__ = "GPL3"`, `__author__ = "PellTech"`,
  `__version__ = "1.0.3.1"`.
- The repository's `LICENSE` file is the **MIT License**, `Copyright (c) 2025 Bready2Crumble`.

The file and the repository disagree, and a third name appears in the account that hosts
them. This is recorded as a dated observation (SPEC.md F34) so that a change upstream is
visible as a change rather than silently resolving the question.

## What this project does about it

The conservative reading satisfies both at once:

- **This project stays GPL-3.0.** MIT-licensed code may be incorporated into a GPL work, and
  GPL code obviously may, so the choice is safe under either reading.
- **The MIT notice is retained verbatim** in `NOTICE`, naming the copyright holder as
  upstream's `LICENSE` does. Under the MIT reading that retention is an obligation, and
  attribution in prose is not the same thing as the notice the licence asks for.
- **Attribution to the project and its author is kept** everywhere it was, because it is owed
  under both readings and because that project published the hard part.
- **Every place this repository describes upstream's licence says both things**, rather than
  the one that suits us: `README.md`, `SPEC.md` D1, `CLAUDE.md`, the header of
  `plugin/companion.py`.

Upstream should be asked to reconcile the two, since one line on their tracker would settle it
for everyone downstream. That request is the owner's to make and has not been made at the time
of writing; issue #25 here is where its link belongs once it exists.

**This is not legal advice and nobody here is a lawyer.** The above is the cautious option
that is defensible under either reading. If the ambiguity ever matters commercially, that is a
question for someone qualified.
