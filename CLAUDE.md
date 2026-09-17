# Claude Code Instructions

## Git & Authorship

- All commits MUST be authored as the project owner. Never include `Co-Authored-By: Claude` or any trace of Claude in commits, code, comments, or documentation.
- Author: `Jcapehart2 <jcapehart2@hotmail.com>`
- Never mention Claude, AI assistance, or Anthropic anywhere in the repository.

## Project

- RuneKit Reforged: Alt1-compatible toolkit for RuneScape 3 on Linux
- Python 3.11+, PySide6/Qt6
- macOS support is experimental/untested — Linux is the primary target

## Dependencies

- Never add, remove, or upgrade a dependency (in `pyproject.toml`,
  `poetry.lock`, system package install steps in `README.md`/`RuneKit.sh`,
  or CI/packaging files) without first checking with the project owner
  (Jcapehart2, the maintainer/user directing this work).
  Propose the dependency and the reason it's needed, and wait for approval
  before making the change.
