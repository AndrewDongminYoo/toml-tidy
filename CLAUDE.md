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

- `cli.py` — Typer app, one command taking one or more paths. Exit codes: `0` sorted/clean, `1` `--check` found changes needed, `2` any error (TOML parse, encoding, filesystem, recursion, invalid config), reported as `{path}: {message}` on stderr with no traceback. With multiple paths every file is processed and the worst code wins. `--in-place` and `--check` are mutually exclusive; `--in-place` writes only when content changes. Per-file defaults come from `[tool.toml-tidy]` in the nearest `pyproject.toml` walking up from each target (keys: `order`, `scope`, `first`, `blank-lines`, `line-width`); `--order`/`--scope`/`--blank-lines`/`--line-width` flags override, `first` is config-only. `line-width` must be a positive integer; `bool` is an `int` subclass in Python, so the parser rejects it explicitly.
- `sorter.py` — all sorting logic. `sort_toml(source, order, scope, first, *, blank_lines, line_width)` is the only public entry point.

### How sorting works (sorter.py)

The sorter mutates `tomlkit`'s parsed `Container.body` directly (a list of `(Key | None, Item)` tuples), because tomlkit's public mapping API preserves insertion order and offers no reorder operation. This private-representation dependency is deliberately isolated to this one module and pinned via the `tomlkit>=0.15.0,<0.16` range — regression tests guard it.

Key invariants (documented in `docs/specs/2026-07-16-hierarchical-toml-sort-design.md`):

- Sorting happens per **segment**: a run of direct keys, or a run of sibling explicit `Table`/`AoT` declarations. Segments never mix — a table run and a key run are sorted independently. An `AoT` (array of tables) sorts by name among its sibling tables like any other segment member; only the order of elements **within** an AoT is preserved.
- Sort keys compare the **parsed logical key** (`Key.key`), not source spelling — `[plugins."omo-kit"]` compares as `omo-kit` but keeps its quotes in output.
- `natural` order (default) compares digit runs numerically (`item2` < `item10`); `alpha` is case-insensitive lexical. Both append the raw key as a tiebreaker.
- Standalone comments attach to the **following** key/table and move with it; whitespace stays after the **preceding** entry; trailing whitespace stays at the segment boundary. This attachment/hoisting logic spans `_sort_segments`, `_sort_segment`, `_hoist_header_comments`, `_pop_trailing_comment_run`, and `_restore_comment_attachment`.
- A dotted key-value (e.g. `a.b = 1`) parses as a `Table` entry but sorts together with direct keys in the same key segment, compared by its full dotted path; explicit `[a.b]` headers stay in the table segment.
- Recursion descends into `Table` values and each `AoT` element, but inline-table keys are never reordered.
- `Scope` limits which segment kinds sort: `tables` passes key segments through untouched, `keys` passes table segments through; skipped segments bypass `_sort_segment` entirely (no merge, no comment hoisting), preserving tomlkit's own parse round-trip form. That form is the fidelity floor, not raw source bytes — tomlkit itself coalesces split AoT declarations (`[[a]] … [b] … [[a]]`) at parse time.
- `first` pins top-level entries (matched on the leading segment of the parsed key path) ahead of sorted siblings in listed order; recursion passes an empty `first` so it never applies inside nested tables.
- `blank_lines` (opt-in, off by default) runs `_normalize_blank_lines` between the sort and the map rebuild — it adds and deletes body entries, so it must precede `_restore_maps`. It rewrites `Whitespace` entries only: exactly one blank line before each table/AoT header (above that header's attached comment run), none between keys, inside comment runs, at EOF, or above the document's first rendered line. Every boundary has exactly one owner, or it doubles: a separator before a header belongs to the body holding it only when the preceding entry there is a key, otherwise to the preceding declaration's deepest body tail (`followed`), which is where tomlkit parses it. `separate_first` is the only boundary a body's first entry can own — the header this container renders directly above it — so a super table (no header of its own) and the document root (nothing above line 1) both pass `separate_first=False`. Blank lines inside multi-line strings are part of the value, never trivia, so a regex over the dumped output would corrupt them.
- `_normalize_array_spacing` rewrites every non-empty single-line array's whitespace run: one edge space each side, `", "` for each separator, and a trailing comma kept at the end. The run is rebuilt rather than edited because the commas live inside `Whitespace` items, not between them. Single-line is what makes this safe against comments: a `#` runs to end of line, so a comment inside an array always forces the closing bracket onto another line, and the `"\n" in as_string()` guard therefore excludes every array that could hold one.
- `line_width` (opt-in, unset by default) runs `_expand_wide_arrays` after spacing normalization, so the measured width is the width the normalized array actually renders at. It expands through `_expand_array`, which builds the whitespace run by hand rather than calling `array.multiline(True)`: tomlkit's multiline renderer hard-codes `\n`, so that call splices bare LF into a CRLF document, and the CLI's mixed-ending path hands such a document straight through. The line ending comes from the array's own trivia, never an enclosing wrapper — a dotted key-value's wrapper carries a bare newline while the value carries the CRLF. There is no collapse direction, because `tomlkit`'s `multiline(False)` renders the stored items verbatim rather than rejoining them, and hand-stripping the newlines would splice array comments into the value line. Expansion-only is also what makes the pass idempotent: an expanded array contains newlines and is skipped on the next run.
- Width is `indent + prefix + key + sep + array`, excluding a trailing comment (expanding moves a comment but cannot shorten it). A dotted key-value parses as a `Table` wrapper rendering no header, so `a.b = [...]` arrives as key `b` one level down; `prefix` carries the leading segments or the measurement runs short by exactly those columns. The indent sits on the innermost item even for a dotted key. Empty arrays are skipped — expanding one cannot shorten its line.
- After the whole tree is sorted, `_sort_document` runs a separate `_restore_maps` pass that rebuilds each container's key-to-index map; the reorder mutates `body` directly and bypasses tomlkit's own map bookkeeping, so lookups like `document["a"]` would otherwise resolve stale indexes.

## Conventions

- Ruff runs with `select = ["ALL"]` and a short ignore list in `pyproject.toml`; docstrings follow Google convention. Tests get relaxed per-file ignores.
- basedpyright runs in `all` mode — new code must be fully typed (uses PEP 695 `type` aliases, `StrEnum`, `match` statements).
- Design/plan docs live in `docs/specs/` and `docs/superpowers/plans/`; behavior changes should stay consistent with the spec or update it.
