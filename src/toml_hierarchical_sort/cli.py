"""Command-line interface for hierarchical TOML sorting."""

from pathlib import Path
from typing import Annotated, Final

import typer
from tomlkit.exceptions import TOMLKitError

from toml_hierarchical_sort.sorter import OrderMode, sort_toml

app = typer.Typer(help="Sort TOML keys while preserving table hierarchy.")
_MUTUALLY_EXCLUSIVE_OPTIONS: Final = "--in-place and --check cannot be used together"


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

    source = path.read_text(encoding="utf-8")
    try:
        sorted_source = sort_toml(source, order)
    except TOMLKitError as error:
        typer.echo(f"{path}: {error}", err=True)
        raise typer.Exit(code=2) from None

    if check:
        if source != sorted_source:
            raise typer.Exit(code=1)
        return

    if in_place:
        if source != sorted_source:
            _ = path.write_text(sorted_source, encoding="utf-8")
        return

    typer.echo(sorted_source, nl=False)
