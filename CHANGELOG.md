# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[v0.1.0]: https://github.com/AndrewDongminYoo/toml-tidy/releases/tag/v0.1.0
