# Hierarchical TOML Sort Implementation Plan (Archived)

> **Status:** Completed in v0.1.0 and retained as a historical implementation record. For the current contract, see the [design specification](../specs/2026-07-16-hierarchical-toml-sort-design.md) and the repository README.

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build a Python CLI that recursively sorts direct TOML keys and sibling explicit table declarations while preserving hierarchy and `tomlkit` formatting metadata.

**Architecture:** `sorter.py` owns ordering and the isolated `tomlkit` parsed-body adaptation. `cli.py` owns file I/O, command options, and exit codes. Tests exercise the public sorter result and the CLI surface using real TOML fixtures.

**Tech Stack:** Python 3.13, uv, tomlkit, Typer, pytest, Ruff, basedpyright.

## Global Constraints

- Use `tomlkit` to parse and serialize the same document object.
- Compare parsed logical key strings, never the quoted source spelling.
- Default to case-insensitive natural order, with an explicit alpha mode.
- Preserve parent-child hierarchy and array element order while sorting sibling explicit table declarations.
- Keep comments, blank-line trivia, inline comments, and key quoting attached to their original items.
- Restrict private `tomlkit` container access to `sorter.py`.
- Do not add dependencies beyond `tomlkit`, Typer, and development tooling.
- Run `uv lock` after changing `pyproject.toml`.

---

### Task 1: Convert the starter into a distributable CLI project

**Files:**

- Modify: `pyproject.toml`
- Modify: `README.md`
- Create: `src/toml_tidy/__init__.py`
- Create: `src/toml_tidy/cli.py`
- Delete: `main.py`
- Create: `tests/test_cli.py`
- Modify: `uv.lock`

**Interfaces:**

- Produces: `toml-tidy` console script bound to `toml_hierarchical_sort.cli:app`.
- Produces: `app: typer.Typer` for CLI integration tests.

- [x] **Step 1: Write the failing CLI help test.**

```python
from typer.testing import CliRunner

from toml_hierarchical_sort.cli import app


def test_help_when_called_without_arguments() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Sort TOML keys while preserving table hierarchy." in result.output
```

- [x] **Step 2: Run the test to verify it fails because the package is absent.**

Run: `uv run pytest tests/test_cli.py::test_help_when_called_without_arguments -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'toml_hierarchical_sort'`.

- [x] **Step 3: Replace the starter metadata and script with the minimal Typer application.**

```toml
[project]
name = "toml-tidy"
version = "0.1.0"
description = "Sort TOML keys without changing table hierarchy"
readme = "README.md"
requires-python = ">=3.13"
dependencies = [
  "tomlkit>=0.15,<0.16",
  "typer>=0.16,<1",
]

[project.scripts]
toml-tidy = "toml_hierarchical_sort.cli:app"
```

```python
import typer

app = typer.Typer(help="Sort TOML keys while preserving table hierarchy.")
```

Add the strict Ruff, basedpyright, and pytest sections from the project conventions. Add `pytest>=8`, `ruff>=0.11`, and `basedpyright>=1.29` to the `dev` dependency group. Then run `uv lock`.

Delete the unused starter `main.py` after the console-script entry point is configured.

- [x] **Step 4: Run the focused test and quality checks.**

Run: `uv run pytest tests/test_cli.py::test_help_when_called_without_arguments -v && uv run ruff check . && uv run basedpyright`

Expected: PASS with no lint or type diagnostics.

### Task 2: Implement logical key comparison

**Files:**

- Create: `src/toml_tidy/sorter.py`
- Create: `tests/test_sorter.py`

**Interfaces:**

- Produces: `OrderMode`, a string enum with `NATURAL = "natural"` and `ALPHA = "alpha"`.
- Produces: `sort_toml(source: str, order: OrderMode = OrderMode.NATURAL) -> str`.

- [x] **Step 1: Write failing ordering tests.**

```python
from toml_hierarchical_sort.sorter import OrderMode, sort_toml


def test_sort_toml_when_natural_order_is_selected() -> None:
    source = "item10 = 10\nitem2 = 2\nitem1 = 1\n"

    result = sort_toml(source)

    assert result == "item1 = 1\nitem2 = 2\nitem10 = 10\n"


def test_sort_toml_when_quoted_key_has_logical_order() -> None:
    source = "[plugins]\n\"omo-kit\" = 1\nomo = 2\n"

    result = sort_toml(source, order=OrderMode.ALPHA)

    assert result == "[plugins]\nomo = 2\n\"omo-kit\" = 1\n"
```

- [x] **Step 2: Run the focused tests to verify they fail because the sorter is absent.**

Run: `uv run pytest tests/test_sorter.py -v`

Expected: FAIL with `ImportError` for `OrderMode` and `sort_toml`.

- [x] **Step 3: Implement typed comparison helpers.**

```python
class OrderMode(StrEnum):
    NATURAL = "natural"
    ALPHA = "alpha"


def sort_toml(source: str, order: OrderMode = OrderMode.NATURAL) -> str:
    document = tomlkit.parse(source)
    _sort_container(document, order)
    return tomlkit.dumps(document)
```

Implement `_sort_key(key_text: str, order: OrderMode) -> tuple[str | int, ...]` from the parsed `tomlkit` key's string value. Split natural-order digit runs with a compiled regular expression, case-fold text runs, and retain the original logical text as the final tie-breaker. Keep `OrderMode` branching exhaustive with `match` and `assert_never`.

- [x] **Step 4: Run the focused tests.**

Run: `uv run pytest tests/test_sorter.py -v`

Expected: PASS.

### Task 3: Reorder parsed container bodies while sorting sibling tables

**Files:**

- Modify: `src/toml_tidy/sorter.py`
- Modify: `tests/test_sorter.py`

**Interfaces:**

- Consumes: `sort_toml(source: str, order: OrderMode = OrderMode.NATURAL) -> str`.
- Produces: recursive sorting for direct keys and sibling explicit child tables.

- [x] **Step 1: Write failing preservation and hierarchy tests.**

```python
def test_sort_toml_when_comments_and_child_table_exist() -> None:
    source = "# package settings\nz = 1 # trailing\n\n# keep with a\na = 2\n[tool]\ny = 1\nx = 2\n[tool.child]\nb = 1\na = 2\n"

    result = sort_toml(source)

    assert result == "# keep with a\na = 2\n\n# package settings\nz = 1 # trailing\n[tool]\nx = 2\ny = 1\n[tool.child]\na = 2\nb = 1\n"


def test_sort_toml_when_array_of_tables_exists() -> None:
    source = "[[items]]\nz = 1\na = 2\n\n[[items]]\ny = 3\nx = 4\n"

    result = sort_toml(source)

    assert result == "[[items]]\na = 2\nz = 1\n\n[[items]]\nx = 4\ny = 3\n"
```

- [x] **Step 2: Run the preservation tests to verify they fail because body reordering is absent.**

Run: `uv run pytest tests/test_sorter.py -k 'comments or array_of_tables' -v`

Expected: FAIL with output-order assertion failures.

- [x] **Step 3: Implement container body grouping and recursion.**

```python
def _sort_container(container: Container, order: OrderMode) -> None:
    groups = _groups_from_body(container)
    _sort_grouped_keys_in_place(groups, order)
    _replace_body_and_rebuild_map(container, groups)
    _sort_child_containers(container, order)
```

Build groups from `Container.body` so standalone comments immediately preceding a sortable key or explicit table move with that entry. Keep whitespace after its preceding entry and preserve trailing whitespace at the segment boundary. Sort sibling explicit tables by their parsed logical keys, keep array-of-table entries fixed, and recurse into `Table` and `AoT` child containers. Do not reconstruct TOML items or render strings manually.

- [x] **Step 4: Add parser-validity assertions and run the sorter suite.**

```python
def test_sort_toml_when_output_is_rendered() -> None:
    result = sort_toml("b = 1\na = 2\n")

    parsed = tomlkit.parse(result)

    assert parsed["a"] == 2
```

Run: `uv run pytest tests/test_sorter.py -v`

Expected: PASS.

### Task 4: Implement the file-oriented CLI contract

**Files:**

- Modify: `src/toml_tidy/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**

- Consumes: `sort_toml(source: str, order: OrderMode = OrderMode.NATURAL) -> str`.
- Produces: `toml-tidy PATH [--in-place | --check] [--order natural|alpha]`.

- [x] **Step 1: Write failing CLI behavior tests.**

```python
def test_check_when_input_requires_sorting(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("b = 1\na = 2\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(path), "--check"])

    assert result.exit_code == 1
    assert path.read_text(encoding="utf-8") == "b = 1\na = 2\n"


def test_in_place_when_input_requires_sorting(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("b = 1\na = 2\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(path), "--in-place"])

    assert result.exit_code == 0
    assert path.read_text(encoding="utf-8") == "a = 2\nb = 1\n"
```

- [x] **Step 2: Run the focused CLI tests to verify they fail.**

Run: `uv run pytest tests/test_cli.py -k 'check or in_place' -v`

Expected: FAIL because the command has no file argument or options.

- [x] **Step 3: Add the command implementation and typed errors.**

```python
@app.command()
def sort_file(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    in_place: Annotated[bool, typer.Option("--in-place")] = False,
    check: Annotated[bool, typer.Option("--check")] = False,
    order: Annotated[OrderMode, typer.Option("--order")] = OrderMode.NATURAL,
) -> None:
    ...
```

Reject simultaneous `--in-place` and `--check` with `typer.BadParameter`. Read and write UTF-8 text only. Write unmodified output to standard output by default. In check mode, exit with status one only when the transformed text differs. Surface parser and filesystem errors with the path and a nonzero exit status without writing a partial file.

- [x] **Step 4: Run all CLI tests.**

Run: `uv run pytest tests/test_cli.py -v`

Expected: PASS.

### Task 5: Document usage and run full verification

**Files:**

- Modify: `README.md`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_sorter.py`

**Interfaces:**

- Documents: installation with `uv tool install .`, standard-output mode, `--check`, `--in-place`, and both order modes.

- [x] **Step 1: Write failing end-to-end command tests for alpha mode and invalid TOML.**

```python
def test_command_when_invalid_toml_does_not_mutate_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.toml"
    source = "key = [\n"
    path.write_text(source, encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(path), "--in-place"])

    assert result.exit_code != 0
    assert path.read_text(encoding="utf-8") == source
```

- [x] **Step 2: Run the end-to-end tests to verify they fail.**

Run: `uv run pytest tests/test_cli.py::test_command_when_invalid_toml_does_not_mutate_file -v`

Expected: FAIL until parser errors are handled at the command boundary.

- [x] **Step 3: Complete boundary error reporting and README examples.**

```bash
uv tool install .
toml-tidy pyproject.toml --check
toml-tidy pyproject.toml --in-place --order natural
```

State that parent-child hierarchy and array element order remain unchanged while direct keys and sibling explicit tables are recursively sorted. State that parsed logical keys control comparison, so quoted spelling remains preserved.

- [x] **Step 4: Run the complete quality gate.**

Run: `uv run ruff format --check && uv run ruff check . && uv run basedpyright && uv run pytest`

Expected: every command exits zero.
