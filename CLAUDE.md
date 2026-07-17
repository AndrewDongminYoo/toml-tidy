# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

CLI tool that sorts TOML keys within each table while preserving table hierarchy, comments, and source formatting via `tomlkit`.
Python 3.13, managed with `uv`, packaged with `uv_build`.

## Commands

```bash
uv sync                                # install deps (dev group included)
uv run pytest                          # run all tests
uv run pytest tests/test_sorter.py -k <name>   # run a single test
uv run ruff check src tests           # lint (select = ALL)
uv run ruff format src tests          # format
uv run basedpyright                   # type check (typeCheckingMode = all)
trunk check                           # full lint suite (pre-push hook runs this)
uv run toml-tidy <file>  # run the CLI
```

`pytest` runs with `filterwarnings = ["error"]` — any warning fails the test.

## Architecture

Two modules under `src/toml_tidy/`:

- `cli.py` — Typer app, one command taking one or more paths. Exit codes: `0` sorted/clean, `1` `--check` found changes needed, `2` any error (TOML parse, encoding, filesystem, recursion, invalid config), reported as `{path}: {message}` on stderr with no traceback. With multiple paths every file is processed and the worst code wins. `--in-place` and `--check` are mutually exclusive; `--in-place` writes only when content changes. Per-file defaults come from `[tool.toml-tidy]` in the nearest `pyproject.toml` walking up from each target (keys: `order`, `scope`, `first`); `--order`/`--scope` flags override, `first` is config-only.
- `sorter.py` — all sorting logic. `sort_toml(source, order, scope, first)` is the only public entry point.

### How sorting works (sorter.py)

The sorter mutates `tomlkit`'s parsed `Container.body` directly (a list of `(Key | None, Item)` tuples), because tomlkit's public mapping API preserves insertion order and offers no reorder operation. This private-representation dependency is deliberately isolated to this one module and pinned via the `tomlkit>=0.15.0,<0.16` range — regression tests guard it.

Key invariants (documented in `docs/specs/2026-07-16-hierarchical-toml-sort-design.md`):

- Sorting happens per **segment**: a run of direct keys, or a run of sibling explicit `Table`/`AoT` declarations. Segments never mix — a table run and a key run are sorted independently. An `AoT` (array of tables) sorts by name among its sibling tables like any other segment member; only the order of elements **within** an AoT is preserved.
- Sort keys compare the **parsed logical key** (`Key.key`), not source spelling — `[plugins."omo-kit"]` compares as `omo-kit` but keeps its quotes in output.
- `natural` order (default) compares digit runs numerically (`item2` < `item10`); `alpha` is case-insensitive lexical. Both append the raw key as a tiebreaker.
- Standalone comments attach to the **following** key/table and move with it; whitespace stays after the **preceding** entry; trailing whitespace stays at the segment boundary. This attachment/hoisting logic spans `_sort_segments`, `_sort_segment`, `_hoist_header_comments`, `_pop_trailing_comment_run`, and `_restore_comment_attachment`.
- A dotted key-value (e.g. `a.b = 1`) parses as a `Table` entry but sorts together with direct keys in the same key segment, compared by its full dotted path; explicit `[a.b]` headers stay in the table segment.
- Recursion descends into `Table` values and each `AoT` element, but inline-table keys are never reordered.
- `Scope` limits which segment kinds sort: `tables` passes key segments through untouched, `keys` passes table segments through; skipped segments bypass `_sort_segment` entirely (no merge, no comment hoisting), which keeps them byte-identical.
- `first` pins top-level entries (matched on the leading segment of the parsed key path) ahead of sorted siblings in listed order; recursion passes an empty `first` so it never applies inside nested tables.
- After the whole tree is sorted, `_sort_document` runs a separate `_restore_maps` pass that rebuilds each container's key-to-index map; the reorder mutates `body` directly and bypasses tomlkit's own map bookkeeping, so lookups like `document["a"]` would otherwise resolve stale indexes.

## Conventions

- Ruff runs with `select = ["ALL"]` and a short ignore list in `pyproject.toml`; docstrings follow Google convention. Tests get relaxed per-file ignores.
- basedpyright runs in `all` mode — new code must be fully typed (uses PEP 695 `type` aliases, `StrEnum`, `match` statements).
- Design/plan docs live in `docs/specs/` and `docs/superpowers/plans/`; behavior changes should stay consistent with the spec or update it.
