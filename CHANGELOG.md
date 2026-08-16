# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- `--in-place` dropped a macOS extended ACL from its target, which v0.4.0 introduced by replacing the file with a new inode that `shutil.copystat` cannot carry an ACL onto. A target holding one now takes the same rewrite-in-place path as a hard-linked target, so its entries survive the write; files without an ACL are still replaced atomically. A POSIX platform that is neither Linux nor macOS keeps the inode unconditionally rather than assume an ACL it cannot inspect would survive.
- `--in-place` cleared the set-user-ID and set-group-ID bits of any target it rewrote in place rather than replaced — since v0.4.0 for a hard-linked target, and on Windows. Rewriting now restores them, so a file cannot lose one kind of security metadata on a path taken to preserve another. A target whose bits could not be restored — one the caller may write but does not own, or whose group the caller does not belong to, for which `chmod` reports success while clearing set-group-ID — is refused with exit `2` before its content or its mode is touched, rather than sorted and quietly stripped.

## [v0.4.0] — 2026-08-16

### Changed

- Unknown keys in `[tool.toml-tidy]` are now a configuration error (exit `2`) instead of being ignored, so a typo such as `blank_lines` no longer leaves the intended setting silently at its default.
- `--in-place` no longer carries a POSIX or macOS access-control list across the write. The atomic replacement this release introduces produces a new inode, and the standard library copies the permission mode, timestamps, and flags onto it but no ACL, so a target whose access depends on an ACL entry rather than on its mode loses that entry; hard-linked and Windows targets, which are rewritten in place, keep theirs. Tracked as issue 11.

### Fixed

- `--in-place` truncated the target before writing, so an interrupted or failing write destroyed the original file. Output now goes to a temporary file in the destination directory and replaces the target only once the complete content is flushed and synchronized, carrying over the target's ownership, permission mode, and other metadata. A target with no writable mode bit, or one the process cannot open for writing, is rejected before any work begins. Hard-linked targets and Windows targets are rewritten in place instead, so links stay joined and the file keeps its owner and DACL, at the cost of atomicity on those paths.

## [v0.3.1] — 2026-07-30

### Fixed

- Blank-line normalization inserted an LF separator into a CRLF document whenever the boundary had no trivia of its own to copy an ending from: an empty table body (`sort_toml("[x]\r\n[y]\r\n", blank_lines=True)`), or a dotted key-value, whose `Table` wrapper reports a bare newline while the CRLF belongs to the value nested inside it. The ending now comes from the declaring header or from the value that actually renders. Files with uniform line endings processed through the CLI were unaffected, since it normalizes and restores them around the sort.

## [v0.3.0] — 2026-07-30

### Added

- `--blank-lines` / `--no-blank-lines` and the `blank-lines` configuration key (off by default) to normalize blank lines after sorting: exactly one before every table and array-of-tables header — above that header's attached comment run — and none between key entries, inside comment runs, at the end of the file, or above the document's first line. Blank lines inside multi-line string values are untouched, and the result is idempotent, so `--check` reports a file clean once `--in-place` has fixed it.
- `args: []` in `.pre-commit-hooks.yaml`, so hook consumers can set flags such as `--blank-lines` from `.pre-commit-config.yaml`; `--in-place` stays in the hook entry where an override cannot drop it.

## [v0.2.0] — 2026-07-17

### Added

- Multiple file paths per invocation; every file is processed and the worst exit code wins, so the CLI works directly as a pre-commit or trunk formatter target. Stdout mode takes exactly one path — multiple paths require `--in-place` or `--check`.
- `--scope all|tables|keys` to sort only sibling table declarations (`tables`) or only direct key entries (`keys`).
- `[tool.toml-tidy]` configuration in the nearest `pyproject.toml` (keys: `order`, `scope`, `first`); CLI flags override, invalid values exit `2`.
- `first` configuration key pinning top-level entries ahead of their sorted siblings in listed order (e.g. keep `[project]` on top of `pyproject.toml`).
- `.pre-commit-hooks.yaml` so the repository can be used as a pre-commit hook repo.

## [v0.1.0] — 2026-07-16

Initial release.

### Added

- `toml-tidy` CLI that sorts TOML keys recursively while preserving table hierarchy, comments, and source formatting via `tomlkit`.
- `natural` (default) and `alpha` ordering modes comparing parsed logical keys — digit runs compare numerically, quoted spelling is preserved in output.
- Dotted key-values sort with sibling direct keys by full dotted path; array-of-tables declarations sort by name while element order is preserved.
- `--check` mode (exit `1` when sorting is needed) and `--in-place` mode (writes only on change); sorted output goes to stdout by default.
- Line-ending preservation: uniform CRLF is restored on write and stdout, mixed-ending files pass through byte-faithfully per line.
- Guaranteed idempotence and semantic preservation: sorting never re-parents a key or table, and already-sorted documents round-trip unchanged.
- Robust error contract: parse, encoding, filesystem, and recursion errors report `{path}: {message}` on stderr with exit code `2` and no traceback.
- Python 3.12+ support, MIT license.

[v0.4.0]: https://github.com/AndrewDongminYoo/toml-tidy/compare/v0.3.1...v0.4.0
[v0.3.1]: https://github.com/AndrewDongminYoo/toml-tidy/compare/v0.3.0...v0.3.1
[v0.3.0]: https://github.com/AndrewDongminYoo/toml-tidy/compare/v0.2.0...v0.3.0
[v0.2.0]: https://github.com/AndrewDongminYoo/toml-tidy/compare/v0.1.0...v0.2.0
[v0.1.0]: https://github.com/AndrewDongminYoo/toml-tidy/releases/tag/v0.1.0
