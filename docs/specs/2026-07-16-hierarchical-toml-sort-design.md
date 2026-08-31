# Hierarchical TOML Sort Design

## Goal

Provide a command-line tool that alphabetically or naturally sorts keys inside each TOML table while preserving the document hierarchy, comments, and existing formatting as far as `tomlkit` permits.

## Scope

The tool accepts one or more TOML files and supports previewing required changes or rewriting each file in place.

It preserves parent-child table hierarchy while sorting sibling explicit table declarations at every level.

It sorts direct key-value entries that belong to the current table, including scalar, array, inline-table, and dotted key-values.

It recursively sorts explicit child tables.

Array-of-tables declarations sort by name among sibling table declarations.

It preserves array-of-tables element order and only recursively processes eligible nested tables inside each element.

## Command-Line Contract

`toml-tidy PATH` writes the sorted document to standard output; stdout mode takes exactly one path, and multiple paths require `--in-place` or `--check` so separate documents are never concatenated.

`toml-tidy PATH... --in-place` rewrites each path only when sorting changes it.

`toml-tidy PATH... --check` writes no file, returns zero when every path is already sorted, and returns one when sorting would change any of them.

With multiple paths the command processes every file and returns the worst exit code: two for any error, else one for any check difference, else zero.

`--order alpha` uses a case-insensitive lexical key order.

`--order natural` is the default and compares consecutive decimal digits numerically while otherwise using case-insensitive text.

Both order modes compare a key's parsed logical value, not its source spelling.

For example, `[plugins."omo-kit"]` and `[plugins.omo]` compare as `omo-kit` and `omo` while retaining their original quoted or bare syntax in output.

`--scope all` is the default and sorts both segment kinds; `--scope tables` sorts only sibling table declarations, and `--scope keys` sorts only direct key entries, each leaving the other segment kind in source order.

## Configuration

Per-file defaults come from the `[tool.toml-tidy]` table of the nearest `pyproject.toml` found walking up from each target file; the first `pyproject.toml` found wins whether or not it contains the table.

Supported keys are `order`, `scope`, `first`, and `blank-lines`; CLI flags override `order`, `scope`, and `blank-lines`, while `first` is configuration-only.

`first` lists top-level entry names that are pinned ahead of their sorted siblings in the listed order, matched against the leading segment of the parsed key path, and never applies inside nested tables.

When a segment kind is excluded by `scope`, `first` cannot move entries of that kind because the segment is not reordered at all.

An unreadable configuration file or an invalid configuration value reports `{pyproject path}: {message}` on stderr and exits with code two.

Unknown keys in `[tool.toml-tidy]` are invalid and report the same configuration error rather than being silently ignored.

## Representation Strategy

The implementation parses source with `tomlkit` and serializes the same document object after reordering its parsed container body.

Each sortable key is moved together with its `tomlkit` key and item objects so quoted spelling, inline comments, and item-level trivia remain attached.

Every non-empty single-line array uses one space after `[` and before `]`, one space after each separating comma, and none before it.
A trailing comma retains its place at the end of the array.
This rule applies recursively to nested arrays.
Empty and multi-line arrays retain their source layout.

The opt-in `line-width` setting expands a single-line array when the line it renders on, including indentation and the key, exceeds the configured column count.
A comment trailing the array is excluded from that measurement, because expanding the array relocates the comment without shortening it.
Only an array that is the direct value of a key is measured, since only that array occupies a line of its own; arrays nested inside another array or an inline table share their parent's line.
Expansion never runs in reverse, and an array already spanning several lines is never measured, so a second run over expanded output changes nothing.
An empty array is never expanded because doing so cannot shorten its line.

Standalone comments are assigned to the following sortable key and move with that key, while blank lines between entries remain after the preceding entry.

Comments directly before a table declaration move with that declaration when sibling tables are reordered.

Blank lines at the end of a key or table segment stay at that segment boundary.

These blank-line placements hold unless the opt-in `blank-lines` setting is enabled, which rewrites blank lines after sorting to exactly one before every table header and none elsewhere; comments, values, and their attachment are still preserved.

The implementation uses the parsed container body because `tomlkit`'s public mapping mutation API preserves insertion positions instead of exposing an ordering operation.

This dependency on a private representation is isolated in one module and protected by regression tests against the pinned `tomlkit` version range.

## Error Handling

Invalid TOML reports the parser error with the input path and leaves the file unchanged.

In-place output rejects targets without a writable mode bit or effective write access, then normally writes to a temporary file in the destination directory, synchronizes the complete output, and atomically replaces the resolved target; failures before replacement leave the original file unchanged.

When hard links, directory permissions, or ownership prevent a safe replacement but the existing file is writable, in-place output rewrites the existing inode so linked or group-writable files remain consistent and retain their metadata.

On Windows, in-place output rewrites the existing file so its owner and DACL remain attached; this platform-specific path is not atomic.

On platforms that expose file ownership, atomic replacement preserves the target's user and group ownership and uses the standard library to copy its permission mode and other supported metadata.

A target carrying an access-control list the replacement cannot reproduce joins hard-linked targets on the rewrite-in-place path, so access granted or denied by an ACL rather than by the permission mode survives the write. On macOS that means any extended ACL: the standard library's metadata copy does not carry one and offers no way to read one, so the check is bound to the platform's own `acl_get_file`. A lookup that fails for any reason other than the file having no ACL counts as carrying one, since a check that could not run is not an answer. On Linux the same metadata copy already carries a POSIX ACL across as an extended attribute, so nothing is detoured and the write stays atomic there — but the replacement's ACL is compared against the target's before it is put in place, and any difference keeps the inode instead. Both directions matter: the standard library swallows a refused attribute write, so it can report success having carried nothing, and the temporary file is created in the target's own directory, so an inheritable entry there lands on a replacement whose target had none — handing the file an access rule it never carried. A comparison that cannot read an attribute counts as a difference unless the error says the attribute is absent, on the same reasoning that governs the macOS lookup. Where no attribute API exists at all, only macOS may still replace, and only after its own lookup finds the replacement free of an ACL. Any other POSIX platform keeps the inode unconditionally, trading atomicity it cannot confirm is safe for metadata it would otherwise drop.

A set-user-ID or set-group-ID target is refused on the rewrite-in-place path rather than rewritten. Writing clears those bits from its first byte, and putting them back is not reliably available: it needs ownership the writer need not have, and for set-group-ID a `chmod` by a caller outside the file's group clears the bit while reporting success, so even asking whether a restore would work destroys what it asks about. The replacement path is unaffected — `shutil.copystat` carries the mode across intact — so an ordinary set-ID file is still sorted in place; only one that also carries a hard link or an ACL, or sits on Windows, is declined.

`--in-place` and `--check` are mutually exclusive.

A missing or unreadable path (including a directory) reports `{path}: {message}` with exit code two and does not stop the remaining paths.

## Tests

Unit tests cover alpha and natural ordering, case-insensitive tie behavior, recursive nested-table handling, parsed ordering for quoted and dotted keys including table headers, comments, inline comments, blank lines, arrays of tables, scope-limited sorting, and `first` pinning.

CLI tests cover standard output, check-mode exit codes, in-place rewriting, invalid TOML without mutation, multi-path aggregation, and `[tool.toml-tidy]` configuration resolution including CLI override and invalid values.

Array spacing and `line-width` expansion are additionally checked by a permutation sweep over block shapes, option combinations, and line endings, asserting idempotence, unchanged parsed data, and the spacing and width properties against the dumped text rather than the measuring code.
The sweep derives widths from each fixture's own rendered line lengths so every array line becomes a boundary case; fixed widths alone left an off-by-one in the measurement undetected.

The test fixtures assert exact serialized output for preservation-sensitive cases and parse the output again to confirm valid TOML.

## Non-Goals

The first release does not reorder array elements or keys inside inline tables.

It does not join a multi-line array back onto one line, and it does not normalize whitespace inside inline tables.

It does not claim byte-for-byte preservation for every TOML construct beyond the covered `tomlkit` behavior.
