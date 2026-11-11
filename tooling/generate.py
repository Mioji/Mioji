# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""Entry point of the profile generator.

Usage, from any working directory::

    python tooling/generate.py            # render from the snapshot
    python tooling/generate.py --fetch    # refresh the snapshot first
    python tooling/generate.py --check    # verify generated files

Running this file puts ``tooling/`` on ``sys.path``, which makes the
``profilegen`` package importable without installation.
"""

from profilegen.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
