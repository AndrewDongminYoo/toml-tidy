"""Time toml-tidy on synthetic inputs.

Run it with ``uv run python scripts/benchmark.py``. It is not a test and not
a CI gate: it writes generated inputs to a temporary directory, times each
case several times, and prints the best and median wall-clock seconds so two
revisions can be compared on one machine.
"""

import importlib.metadata
import platform
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from typer.testing import CliRunner

import toml_tidy
from toml_tidy.cli import app
from toml_tidy.sorter import OrderMode, Scope, sort_toml

if TYPE_CHECKING:
    from collections.abc import Callable

SEED = 41
REPEATS = 5
TABLES = 2_000
DOTTED_GROUPS = 800
KEYS_PER_GROUP = 10
WIDE_ARRAYS = 1_000
ARRAY_ITEMS = 20
LINE_WIDTH = 88
CLI_DIRECTORIES = 4
CLI_FILES_PER_DIRECTORY = 100


def _bracketed_tables(rng: random.Random) -> str:
    """Return ``TABLES`` bracketed tables of ten keys each, both shuffled."""
    tables = [f"table{index}" for index in range(TABLES)]
    rng.shuffle(tables)
    lines: list[str] = []
    for table in tables:
        keys = [f"key{index}" for index in range(KEYS_PER_GROUP)]
        rng.shuffle(keys)
        lines.append(f"[{table}]")
        lines.extend(f'{key} = "{table}-{key}"' for key in keys)
        lines.append("")
    return "\n".join(lines)


def _dotted_keys(rng: random.Random) -> str:
    """Return top-level dotted keys, the shape tomlkit parses slowest."""
    keys = [
        f"group{group}.key{index}"
        for group in range(DOTTED_GROUPS)
        for index in range(KEYS_PER_GROUP)
    ]
    rng.shuffle(keys)
    return "".join(f"{key} = {position}\n" for position, key in enumerate(keys))


def _wide_arrays(rng: random.Random) -> str:
    """Return single-line string arrays wider than ``LINE_WIDTH``."""
    keys = [f"array{index}" for index in range(WIDE_ARRAYS)]
    rng.shuffle(keys)
    lines: list[str] = []
    for key in keys:
        items = ", ".join(f'"{key}-item{index}"' for index in range(ARRAY_ITEMS))
        lines.append(f"{key} = [{items}]")
    return "\n".join(lines) + "\n"


def _cli_tree(root: Path) -> list[Path]:
    """Write already sorted targets in a few directories under shared configs.

    A table-less ``pyproject.toml`` partway up and the configured one at the
    top make every uncached lookup walk several directories and parse both.
    """
    _ = (root / "pyproject.toml").write_text(
        '[tool.toml-tidy]\norder = "natural"\n', encoding="utf-8"
    )
    project = root / "project"
    project.mkdir()
    _ = (project / "pyproject.toml").write_text(
        '[project]\nname = "bench"\n', encoding="utf-8"
    )
    targets: list[Path] = []
    for directory_index in range(CLI_DIRECTORIES):
        directory = project / "packages" / f"package{directory_index}" / "config"
        directory.mkdir(parents=True)
        for file_index in range(CLI_FILES_PER_DIRECTORY):
            target = directory / f"file{file_index}.toml"
            _ = target.write_text('a = 1\nb = 2\n\n[table]\nc = "x"\n', "utf-8")
            targets.append(target)
    return targets


def _time(action: "Callable[[], object]") -> list[float]:
    """Return the wall-clock seconds of ``REPEATS`` calls to ``action``."""
    timings: list[float] = []
    for _ in range(REPEATS):
        start = time.perf_counter()
        _ = action()
        timings.append(time.perf_counter() - start)
    return timings


def _sort(source: str, line_width: int | None = None) -> "Callable[[], object]":
    return lambda: sort_toml(
        source, OrderMode.NATURAL, Scope.ALL, (), line_width=line_width
    )


def _check_all(targets: list[Path]) -> "Callable[[], object]":
    arguments = [*map(str, targets), "--check"]

    def run() -> object:
        result = CliRunner().invoke(app, arguments)
        if result.exit_code != 0:
            message = f"benchmark CLI run failed ({result.exit_code}): {result.output}"
            raise RuntimeError(message)
        return result

    return run


def _report(name: str, size: str, timings: list[float]) -> None:
    best = min(timings)
    median = statistics.median(timings)
    _ = sys.stdout.write(f"{name:<24} {size:>18} {best:>9.3f} {median:>9.3f}\n")


def main() -> None:
    """Generate every input, time each case, and print the table."""
    # Not cryptographic: a fixed seed keeps the inputs identical across runs.
    rng = random.Random(SEED)  # noqa: S311
    bracketed = _bracketed_tables(rng)
    dotted = _dotted_keys(rng)
    wide = _wide_arrays(rng)

    header = (
        f"machine: {platform.machine()} ({platform.platform()})",
        f"python: {platform.python_version()}",
        f"tomlkit: {importlib.metadata.version('tomlkit')}",
        f"toml_tidy: {Path(toml_tidy.__file__).parent}",
        f"seed: {SEED}, repeats: {REPEATS}",
        "",
        f"{'case':<24} {'size':>18} {'best s':>9} {'median s':>9}",
    )
    _ = sys.stdout.write("\n".join(header) + "\n")
    _report("bracketed tables", f"{len(bracketed):,} B", _time(_sort(bracketed)))
    _report("dotted keys", f"{len(dotted):,} B", _time(_sort(dotted)))
    _report(
        f"wide arrays, width {LINE_WIDTH}",
        f"{len(wide):,} B",
        _time(_sort(wide, LINE_WIDTH)),
    )
    with tempfile.TemporaryDirectory(prefix="toml-tidy-bench-") as directory:
        targets = _cli_tree(Path(directory))
        _report(
            "CLI --check, many files",
            f"{len(targets)} files/{CLI_DIRECTORIES} dirs",
            _time(_check_all(targets)),
        )


if __name__ == "__main__":
    main()
