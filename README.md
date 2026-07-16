# toml-hierarchical-sort

Sort TOML keys while preserving table hierarchy and source formatting where `tomlkit` supports it.

## Install

```bash
uv tool install .
```

## Usage

```bash
toml-hierarchical-sort pyproject.toml
toml-hierarchical-sort pyproject.toml --check
toml-hierarchical-sort pyproject.toml --in-place --order natural
```

Without `--in-place`, sorted TOML is written to standard output.

`--check` writes nothing and exits with status `1` when the file requires sorting.

`--in-place` rewrites the file only when sorting changes it.

## Ordering

`natural` is the default and compares digit runs numerically, so `item2` precedes `item10`.

`alpha` uses case-insensitive lexical order.

Both modes compare TOML's parsed logical key, not source quoting.

Dotted keys such as `b.a = 2` sort with their sibling direct keys by their parsed dotted path, segment by segment, so `a` precedes `b.a`, which precedes `b.z`.

For example, `[plugins.omo]` precedes `[plugins."omo-kit"]`, while the quoted spelling remains unchanged in output.

## Preservation

Direct keys and sibling explicit table declarations are sorted recursively within their parent table.

Array-of-tables declarations such as `[[items]]` sort by name among their sibling tables, while the element order inside each array of tables remains unchanged.

Parent-child hierarchy remains unchanged.

Standalone comments move with the following key or table declaration.

Whitespace between entries remains after the preceding entry, and trailing whitespace remains at its table boundary.

Inline comments, value formatting, and key quoting remain attached to their parsed `tomlkit` items.

Keys inside inline tables are not reordered.
