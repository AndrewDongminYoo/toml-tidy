"""Command-line interface for hierarchical TOML sorting."""

import re
from pathlib import Path
from typing import Annotated, Final

import typer
from tomlkit.exceptions import TOMLKitError

from toml_hierarchical_sort.sorter import OrderMode, sort_toml

app = typer.Typer(help="Sort TOML keys while preserving table hierarchy.")
_MUTUALLY_EXCLUSIVE_OPTIONS: Final = "--in-place and --check cannot be used together"
_LONE_LF: Final = re.compile(r"(?<!\r)\n")


def _detect_linesep(content: str) -> str:
    """Detect the file's dominant line ending, mirroring TOMLFile.read()."""
    num_newline = content.count("\n")
    if num_newline == 0:
        return "\n"
    num_win_eol = content.count("\r\n")
    if num_win_eol == num_newline:
        return "\r\n"
    if num_win_eol == 0:
        return "\n"
    return "mixed"


def _apply_linesep(content: str, linesep: str) -> str:
    """Restore a previously detected line ending, mirroring TOMLFile.write().

    "mixed" input (inconsistent endings) is passed through unchanged: the
    caller never normalized it before parsing, so tomlkit's per-line trivia
    already preserves each original ending.
    """
    if linesep == "\r\n":
        return _LONE_LF.sub("\r\n", content)
    if linesep == "\n":
        return content.replace("\r\n", "\n")
    return content


@app.command()
def sort_file(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    in_place: Annotated[bool, typer.Option("--in-place")] = False,
    check: Annotated[bool, typer.Option("--check")] = False,
    order: Annotated[OrderMode, typer.Option("--order")] = OrderMode.NATURAL,
) -> None:
    """Sort TOML keys while preserving table hierarchy."""
    if in_place and check:
        raise typer.BadParameter(_MUTUALLY_EXCLUSIVE_OPTIONS)

    try:
        with path.open(encoding="utf-8", newline="") as handle:
            raw_source = handle.read()
        linesep = _detect_linesep(raw_source)
        # Mixed endings: parse the raw source unchanged so tomlkit's per-line
        # trivia keeps each line's original ending, mirroring TOMLFile's
        # passthrough behavior instead of flattening everything to LF.
        source = raw_source if linesep == "mixed" else raw_source.replace("\r\n", "\n")
        sorted_source = sort_toml(source, order)
    except (TOMLKitError, UnicodeDecodeError, OSError, RecursionError) as error:
        typer.echo(f"{path}: {error}", err=True)
        raise typer.Exit(code=2) from None

    if check:
        if source != sorted_source:
            raise typer.Exit(code=1)
        return

    output = _apply_linesep(sorted_source, linesep)

    if in_place:
        if source != sorted_source:
            try:
                with path.open("w", encoding="utf-8", newline="") as handle:
                    _ = handle.write(output)
            except OSError as error:
                typer.echo(f"{path}: {error}", err=True)
                raise typer.Exit(code=2) from None
        return

    typer.echo(output, nl=False)
