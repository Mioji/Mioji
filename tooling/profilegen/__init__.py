# SPDX-FileCopyrightText: 2026 Alexander (Mioji) <mioji.net@outlook.com>
# SPDX-License-Identifier: MIT

"""Generator for the multilingual GitHub profile README of this repository.

The package turns three kinds of committed input into a deterministic
set of generated files:

* declarative configuration under ``configs/`` and ``locales/readme/``;
* a validated snapshot of public GitHub data in
  ``configs/data/snapshot.json``;
* WebP artwork and its generation prompts under ``assets/profile/``.

Rendering never touches the network. Only ``--fetch`` talks to the
GitHub REST API, and it replaces the snapshot before rendering, so every
generated file can be reproduced byte for byte from the repository
alone. Continuous integration enforces that property with ``--check``.

Module layout, in dependency order:

* :mod:`profilegen.errors` and :mod:`profilegen.validation` provide the
  exception hierarchy and strict JSON readers used everywhere else;
* :mod:`profilegen.models`, :mod:`profilegen.config`,
  :mod:`profilegen.theme` and :mod:`profilegen.locales` parse inputs into
  immutable, fully validated objects;
* :mod:`profilegen.client` and :mod:`profilegen.collect` fetch a new
  snapshot from GitHub;
* :mod:`profilegen.svg`, :mod:`profilegen.panels` and
  :mod:`profilegen.render` produce the output files in memory;
* :mod:`profilegen.security` validates generated SVG and embedded WebP;
* :mod:`profilegen.publish` writes outputs transactionally;
* :mod:`profilegen.cli` wires the stages together.
"""
