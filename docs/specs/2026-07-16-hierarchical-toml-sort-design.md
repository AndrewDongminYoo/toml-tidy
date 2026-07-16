# Hierarchical TOML Sort Design

## Goal

Provide a command-line tool that alphabetically or naturally sorts keys inside each TOML table while preserving the document hierarchy, comments, and existing formatting as far as `tomlkit` permits.

## Scope

The tool accepts one TOML file and supports previewing required changes or rewriting that file in place.

It preserves parent-child table hierarchy while sorting sibling explicit table declarations at every level.

It sorts direct key-value entries that belong to the current table, including scalar, array, inline-table, and dotted key-values.

It recursively sorts explicit child tables.

Array-of-tables declarations sort by name among sibling table declarations.

It preserves array-of-tables element order and only recursively processes eligible nested tables inside each element.

## Command-Line Contract

`toml-hierarchical-sort PATH` writes the sorted document to standard output.

`toml-hierarchical-sort PATH --in-place` rewrites `PATH` only when sorting changes it.

`toml-hierarchical-sort PATH --check` writes no file, returns zero when `PATH` is already sorted, and returns one when sorting would change it.

`--order alpha` uses a case-insensitive lexical key order.

`--order natural` is the default and compares consecutive decimal digits numerically while otherwise using case-insensitive text.

Both order modes compare a key's parsed logical value, not its source spelling.

For example, `[plugins."omo-kit"]` and `[plugins.omo]` compare as `omo-kit` and `omo` while retaining their original quoted or bare syntax in output.

## Representation Strategy

The implementation parses source with `tomlkit` and serializes the same document object after reordering its parsed container body.

Each sortable key is moved together with its `tomlkit` key and item objects so quoted spelling, inline comments, value formatting, and item-level trivia remain attached.

Standalone comments are assigned to the following sortable key and move with that key, while blank lines between entries remain after the preceding entry.

Comments directly before a table declaration move with that declaration when sibling tables are reordered.

Blank lines at the end of a key or table segment stay at that segment boundary.

The implementation uses the parsed container body because `tomlkit`'s public mapping mutation API preserves insertion positions instead of exposing an ordering operation.

This dependency on a private representation is isolated in one module and protected by regression tests against the pinned `tomlkit` version range.

## Error Handling

Invalid TOML reports the parser error with the input path and leaves the file unchanged.

`--in-place` and `--check` are mutually exclusive.

The command rejects directories and missing paths before parsing.

## Tests

Unit tests cover alpha and natural ordering, case-insensitive tie behavior, recursive nested-table handling, parsed ordering for quoted and dotted keys including table headers, comments, inline comments, blank lines, and arrays of tables.

CLI tests cover standard output, check-mode exit codes, in-place rewriting, and invalid TOML without mutation.

The test fixtures assert exact serialized output for preservation-sensitive cases and parse the output again to confirm valid TOML.

## Non-Goals

The first release does not reorder array elements or keys inside inline tables.

It does not claim byte-for-byte preservation for every TOML construct beyond the covered `tomlkit` behavior.
