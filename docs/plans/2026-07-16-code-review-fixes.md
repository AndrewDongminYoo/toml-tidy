# Code Review Fixes — Implementation Plan

Fix all 13 confirmed findings from the 2026-07-16 high-effort code review of the initial implementation.
Findings are ordered by severity; Tasks 1–2 are data-corruption bugs and go first.

## Global Constraints

These bind every task.

- **Semantic preservation:** for every input, `tomlkit.parse(sort_toml(source))` must represent the same data as `tomlkit.parse(source)` — same keys, same values, same hierarchy. Sorting must never re-parent a key or table.
- **Idempotence:** `sort_toml(sort_toml(source)) == sort_toml(source)` for every input. An already-sorted document must round-trip unchanged (`sort_toml(sorted_source) == sorted_source`).
- **Exit-code contract (CLI):** `0` = success or `--check` clean; `1` = `--check` found changes needed, and nothing else; `2` = any error (parse error, encoding error, filesystem error), reported as `"{path}: {message}"` on stderr with no traceback.
- **Attachment rules (authoritative, per README):** standalone comments move with the following key or table declaration; whitespace between entries remains after the preceding entry; trailing whitespace remains at its table boundary. Where the spec (`docs/specs/2026-07-16-hierarchical-toml-sort-design.md`) contradicts the README, the README is correct and the spec must be fixed (Task 3).
- **TDD:** every behavior fix starts with a failing test that reproduces the finding (RED), then the fix (GREEN). Include the finding's exact repro string as a test case.
- **Gates before every commit:** `uv run pytest` (all green, `filterwarnings = ["error"]` means zero warnings), `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run basedpyright` (zero errors, `typeCheckingMode = all`).
- **Commits:** conventional commits, English, no Co-Author lines, no `Claude-Session` trailer. One commit per task is fine; split only if concerns genuinely differ.
- **Manifest changes:** any `pyproject.toml` dependency edit must regenerate `uv.lock` (`uv lock`) in the same commit.
- **Docs:** when a task changes user-visible behavior, update README.md and the spec in the same task. Markdown uses sentence-level line breaks and a language id on every fenced code block.
- **Scope:** surgical diffs. Do not refactor beyond what the task requires. Public API (`sort_toml(source, order)`, CLI flags) must not change shape.

## Task 1: Dotted keys — sort as direct keys, never re-parent

**Finding (CONFIRMED, most severe):** `sorter.py:61`.
Dotted keys (`m.x = 1`) parse as `tomlkit` `Table` body entries, so `_sort_segments` classifies them into the table segment.
Two consequences:

1. They are never sorted with sibling scalar keys — `sort_toml('b.z = 1\nb.a = 2\na = 3\n')` returns its input unchanged.
2. When a real `[a]` header sorts above a top-level dotted key, the dotted key is re-parented under `[a]` on reparse: `sort_toml('m.x = 1\n[a]\nv = 1\n')` → `'[a]\nv = 1\nm.x = 1\n'`, which reparses as `{'a': {'v': 1, 'm': {'x': 1}}}` — silent hierarchy corruption. Nested variant: `'[t]\nz.x = 1\n[t.a]\nv = 1\n'` re-parents `z.x` under `[t.a]`.

**Fix direction:** in `_sort_segments`, classify dotted-key entries as members of the **key segment**, not the table segment.
Investigate how tomlkit represents them (the body entry's key for `m.x = 1` — check `tomlkit.items.DottedKey` vs `SingleKey`, and/or the `Table` item's properties) and find a reliable discriminator between a dotted-key entry and an explicit `[table]`/super-table entry.
Keeping dotted keys in the key segment fixes both symptoms at once: they sort among sibling direct keys, and they can never cross a following `[table]` header (segment boundaries are preserved), so re-parenting becomes impossible.

**Ordering for dotted keys:** compare the parsed logical segment sequence — `a` < `b.a` < `b.z` (compare per segment with the active order mode; a shorter prefix sorts before its extensions).
Do not compare raw source spelling.

**Required tests (write RED first):**

- `sort_toml('b.z = 1\nb.a = 2\na = 3\n')` → `'a = 3\nb.a = 2\nb.z = 1\n'`.
- `sort_toml('m.x = 1\n[a]\nv = 1\n')` → `'m.x = 1\n[a]\nv = 1\n'` (unchanged; dotted key must stay above the header) and reparse-equality with the input.
- Nested: `sort_toml('[t]\nz.x = 1\n[t.a]\nv = 1\n')` keeps `z.x` inside `[t]` (reparse equality).
- Mixed: `'[t]\nz = 1\na.b = 2\n'` → `'[t]\na.b = 2\nz = 1\n'`.
- A reparse-equality assertion (`tomlkit.parse(out) == tomlkit.parse(src)` on `.unwrap()` or equivalent plain-dict comparison) for every new case.

**Docs:** add a README sentence documenting that dotted keys sort with sibling direct keys by their parsed dotted path.

## Task 2: Missing trailing newline must not produce invalid TOML

**Finding (CONFIRMED):** `sorter.py:105`.
The last entry of a file without a trailing newline has no newline trivia; when sorting moves it earlier, the following key is glued onto the same line: `sort_toml('b = 1\na = 2')` → `'a = 2b = 1\n'`, which is unparseable (`Invalid number`).
`--in-place` would overwrite the user's file with broken TOML.

**Fix direction:** normalize in `sort_toml` after parsing: when the source does not end with a newline, ensure the final meaningful body item's trail gains a `"\n"` before segment sorting runs (or equivalently guarantee every reordered entry carries a trailing newline).
It is acceptable — and expected formatter behavior — that output gains a final newline; keep it deterministic and idempotent.

**Required tests (RED first):**

- `sort_toml('b = 1\na = 2')` → `'a = 2\nb = 1\n'` and the output reparses cleanly.
- Already-sorted no-newline input: `sort_toml('a = 1\nb = 2')` → `'a = 1\nb = 2\n'` (only the newline added) — and running `sort_toml` again on that output returns it unchanged (idempotence).
- Table variant: `sort_toml('[b]\nx = 1\n[a]\ny = 2')` reparses cleanly with correct hierarchy.

## Task 3: Comment and whitespace attachment

Three related CONFIRMED findings in `_sort_segment` / `_sort_container`, plus a spec correction.

**3a — table-header comments (`sorter.py:47`):** a standalone comment directly above a `[table]` header lexically lives at the tail of the PREVIOUS table's container body, so reordering sibling tables strands it under the wrong table.
Repro: `sort_toml('# about zebra\n[zebra]\nk = 1\n# about apple\n[apple]\nk = 2\n')` leaves `'# about apple'` at EOF under `[zebra]`.
Fix direction: when sorting a container's table segment, hoist the trailing comment block (comments not followed by any key within that table's body) out of each table's container and treat it as the leading-comment group of the NEXT sibling entry, so it moves with the following declaration.
Take care: only hoist a trailing run of comments (and their interleaved whitespace) that immediately precedes the next sibling declaration; a comment that is genuinely the last content of the last table stays put.

**3b — comment/blank-line inversion (`sorter.py:92`):** whitespace arriving after pending comments is flushed above them, so an already-sorted file is rewritten: `sort_toml('# header comment\n\na = 1\nb = 2\n')` → `'\n# header comment\na = 1\nb = 2\n'`.
Fix direction: preserve the original interleaved order of a leading trivia run (comments and whitespace) when it is attached to a following key or carried as leading/trailing trivia — never reorder trivia relative to each other.

**3c — spec correction:** `docs/specs/2026-07-16-hierarchical-toml-sort-design.md` line 41 claims leading blank lines move with the following key; the code and README both implement "whitespace stays after the preceding entry."
Fix the spec sentence to match the README (the README rule is authoritative per Global Constraints).

**Required tests (RED first):**

- The 3a repro: both comments must sit directly above their own tables after sorting.
- The 3b repro: already-sorted input returns byte-identical output.
- Idempotence for both repros.
- A case combining both: commented tables that also need key sorting inside.

## Task 4: Idempotence for out-of-order sibling subtables

**Finding (CONFIRMED):** `sorter.py:104`.
`[a.y] … [b] … [a.x]` produces two body entries whose sort key is `'a'`; the stable sort leaves them in original relative order, but the reparsed output merges them into one super-table whose children then sort, so pass 1 ≠ pass 2: `--in-place` followed by `--check` exits 1.

**Fix direction:** derive a table entry's sort key from its full effective header path (e.g. `a.y` and `a.x`, not both `a`), so the first pass already yields the order a reparse-and-resort would produce.
Investigate how to recover the concrete nested path from a super-table body entry (walk single-child super tables down to the named table).
This must compose with Task 1's dotted-path comparison (shorter prefix before extensions, per-segment comparison with the active order mode).

**Required tests (RED first):**

- `src = '[a.y]\nk = 1\n[b]\nk = 2\n[a.x]\nk = 3\n'`: assert `sort_toml(sort_toml(src)) == sort_toml(src)`, and that pass 1 already orders `[a.x]` before `[a.y]`.
- Reparse equality (no data change).
- A property-style loop over a handful of hand-written shuffled documents asserting idempotence (plain pytest, no new dependencies).

## Task 5: Array-of-tables must not freeze sibling table sorting

**Finding (CONFIRMED):** `sorter.py:56`.
Any `[[aot]]` entry hard-flushes the table segment, so sibling tables on opposite sides never sort: `sort_toml('[z]\nk = 1\n[[items]]\nk = 2\n[a]\nk = 3\n')` returns input unchanged while `--check` exits 0.

**Design decision (assumption stated in plan, apply it):** treat an AoT body entry as a sortable sibling declaration keyed by its name — `[[items]]` sorts among `[a]`/`[z]` by `items` — while the elements WITHIN the AoT keep their order (they are one body entry, so this is automatic).
This matches the spec's "sorting sibling explicit table declarations at every level" and keeps the spec's "array-of-tables element order remains unchanged" intact.
Safety note verified during review: a `[items.sub]` header following `[[items]]` is stored inside the AoT's last element's container, not as a root body sibling, so moving the AoT as a unit cannot detach it.
Verify this with a test rather than trusting the note.

**Required tests (RED first):**

- The repro sorts to `[a]`, `[[items]]`, `[z]` with reparse equality.
- `'[[b]]\nk = 1\n[[a]]\nk = 2\n'` → `[[a]]` before `[[b]]`.
- Multi-element AoT keeps element order.
- `'[[items]]\nk = 1\n[items.sub]\ns = 1\n[a]\nk = 2\n'`: `[items.sub]` stays attached to its element after sorting (reparse equality).
- Idempotence for all cases.

**Docs:** update README/spec to state that array-of-tables declarations sort by name among sibling tables while element order is preserved.

## Task 6: CLI — exit-code contract and newline preservation

Two CONFIRMED findings in `cli.py`.

**6a — unhandled errors (`cli.py:26`):** only `TOMLKitError` is caught.
Non-UTF-8 input (`UnicodeDecodeError`), `--in-place` write failures (`PermissionError`/`OSError`), and `RecursionError` from deeply nested headers inside `tomlkit.parse` all escape as tracebacks with exit code 1 — colliding with `--check`'s documented "needs sorting" exit 1.
Fix: wrap the read, sort, and write steps so that `UnicodeDecodeError`, `OSError`, and `RecursionError` are each reported as `"{path}: {message}"` on stderr with exit code 2, matching the existing `TOMLKitError` handling.
No traceback may reach the user for these cases.

**6b — CRLF normalization (`cli.py:26`, `cli.py:40`):** `read_text`/`write_text` use universal-newline translation, so `--in-place` on a CRLF file that needs sorting silently rewrites every line ending to LF (verified byte-level).
Fix: preserve the source file's dominant line ending — follow the approach of `tomlkit.toml_file.TOMLFile.read()/write()` (detect the linesep on read, restore it on write); apply the same restoration to stdout output so piping matches the file mode.
Using `TOMLFile` directly for the `--in-place` path is acceptable if it fits; otherwise replicate its `newline=''` discipline.

**Required tests (RED first, use `typer.testing.CliRunner` and `tmp_path`):**

- Non-UTF-8 file → exit 2, stderr contains the path, no traceback in output.
- `--in-place` on a read-only file (chmod 444) → exit 2, file unchanged.
- Deeply nested header (`'[' + 'a.' * 1500 + 'a]'`) → exit 2, no traceback.
- CRLF file needing sorting: after `--in-place`, bytes contain `\r\n` line endings and no bare-`\n` lines; `--check` on an already-sorted CRLF file exits 0 and leaves bytes untouched.

## Task 7: Stale Container map and tomlkit version pin

Two CONFIRMED findings.

**7a — stale `Container._map` (`sorter.py:34`):** `container.body[:] = ...` reorders the private `_body` list without updating the parallel key→index map, so after `_sort_container` runs, key lookups on the live document return wrong values (`doc['a'] == 1` for `'b = 1\na = 2\nc = 3'`).
Text output is correct today only because `dumps()` walks the body directly.
Fix direction: after reordering a container's body, restore map consistency — investigate tomlkit's internals for the cheapest safe way (rebuild the map to match the new body order, or reorder via a supported pathway).
The fix must not change dump output for any existing test.

**7b — unbounded tomlkit range (`pyproject.toml:7`):** the design doc line 49 claims the private-API risk is "protected by regression tests against the pinned tomlkit version range", but the manifest floats `tomlkit>=0.15.0` with no ceiling.
Fix: constrain to the tested minor range (`tomlkit>=0.15.0,<0.16`), regenerate `uv.lock` in the same commit, and leave the spec sentence now-true.

**Required tests (RED first):**

- After sorting, the same parsed document object answers key lookups correctly: parse `'b = 1\na = 2\nc = 3\n'`, run the sorter on the document, assert `doc['a'] == 2`, `doc['b'] == 1`, `doc['c'] == 3`.
- Same assertion one level deep inside a `[table]`.

## Task 8: Remove duplicate formatters from trunk config

Two CONFIRMED config findings in `.trunk/trunk.yaml`.

- **black@26.5.1** (line 21) fights `[tool.ruff.format]` in pyproject.toml — an empirically shown one-way disagreement on docstring code formatting. Remove `black` from `lint.enabled`; ruff format is the single formatter.
- **isort@8.0.1** (line 24) duplicates ruff's `I` rules and, with only `profile=black` in `.trunk/configs/.isort.cfg`, misclassifies `toml_hierarchical_sort` as third-party — an empirically reproduced fmt/check loop against ruff I001. Remove `isort` from `lint.enabled` and delete `.trunk/configs/.isort.cfg`.

**Verification:** `trunk check --no-progress src/toml_hierarchical_sort/sorter.py` (or the full repo if fast) runs without configuration errors, and `uv run ruff check src tests` still passes.
No behavior tests needed — config-only change.
