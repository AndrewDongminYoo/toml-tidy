"""Command-line interface for hierarchical TOML sorting."""

import re
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final, NamedTuple, cast

import typer
from tomlkit.exceptions import TOMLKitError

from toml_tidy.sorter import OrderMode, Scope, sort_toml

app = typer.Typer(help="Sort TOML keys while preserving table hierarchy.")
_MUTUALLY_EXCLUSIVE_OPTIONS: Final = "--in-place and --check cannot be used together"
_MULTIPLE_PATHS_NEED_MODE: Final = (
    "multiple paths require --in-place or --check; stdout output takes one path"
)
_LONE_LF: Final = re.compile(r"(?<!\r)\n")


class _ConfigError(Exception):
    """Invalid or unreadable ``[tool.toml-tidy]`` configuration."""


class _Settings(NamedTuple):
    order: OrderMode
    scope: Scope
    first: tuple[str, ...]


def _as_table(value: object) -> dict[str, object]:
    # TOML table keys are always strings, so the cast is sound.
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _find_pyproject(target: Path) -> Path | None:
    try:
        # Python 3.12's Path.resolve() raises RuntimeError on cyclic
        # symlinks; 3.13+ resolves as far as possible instead.
        directory = target.resolve().parent
    except (OSError, RuntimeError) as error:
        message = f"{target}: {error}"
        raise _ConfigError(message) from None
    for candidate_dir in (directory, *directory.parents):
        candidate = candidate_dir / "pyproject.toml"
        if candidate.is_file():
            return candidate
    return None


def _resolve_settings(
    target: Path, order: OrderMode | None, scope: Scope | None
) -> _Settings:
    """Merge CLI flags over the nearest pyproject's ``[tool.toml-tidy]`` table.

    The first ``pyproject.toml`` found walking up from the target file wins,
    whether or not it contains a ``[tool.toml-tidy]`` table.
    """
    pyproject = _find_pyproject(target)
    section: dict[str, object] = {}
    if pyproject is not None:
        try:
            with pyproject.open("rb") as handle:
                raw_section = _as_table(tomllib.load(handle).get("tool")).get(
                    "toml-tidy"
                )
        except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as error:
            message = f"{pyproject}: {error}"
            raise _ConfigError(message) from None
        # A present but non-table value is a config mistake, not an absent
        # config — silently falling back to defaults would hide it.
        if raw_section is not None:
            if not isinstance(raw_section, dict):
                message = (
                    f"{pyproject}: [tool.toml-tidy] must be a table,"
                    f" got {raw_section!r}"
                )
                raise _ConfigError(message)
            section = cast("dict[str, object]", raw_section)

    if order is None:
        order_raw = section.get("order", OrderMode.NATURAL.value)
        order = _parse_enum(order_raw, OrderMode, "order", pyproject)
    if scope is None:
        scope_raw = section.get("scope", Scope.ALL.value)
        scope = _parse_enum(scope_raw, Scope, "scope", pyproject)
    first = _parse_first(section.get("first", []), pyproject)

    return _Settings(order, scope, first)


def _parse_enum[E: StrEnum](
    value: object, enum_type: type[E], key: str, pyproject: Path | None
) -> E:
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError:
            allowed = ", ".join(repr(member.value) for member in enum_type)
            detail = f"invalid {key} {value!r} (expected one of: {allowed})"
    else:
        detail = f"{key} must be a string, got {value!r}"
    message = f"{pyproject}: {detail}"
    raise _ConfigError(message)


def _parse_first(value: object, pyproject: Path | None) -> tuple[str, ...]:
    if isinstance(value, list):
        items = cast("list[object]", value)
        first = tuple(item for item in items if isinstance(item, str))
        if len(first) == len(items):
            return first
    message = f"{pyproject}: first must be a list of strings, got {value!r}"
    raise _ConfigError(message)


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
    # No exists=True: Click would usage-error on the first missing path before
    # the loop runs, breaking the "one bad file does not stop the rest"
    # contract. FileNotFoundError/IsADirectoryError are OSErrors, so
    # _process_file already reports them as "{path}: {message}" with code 2.
    paths: Annotated[list[Path], typer.Argument()],
    in_place: Annotated[bool, typer.Option("--in-place")] = False,
    check: Annotated[bool, typer.Option("--check")] = False,
    order: Annotated[OrderMode | None, typer.Option("--order")] = None,
    scope: Annotated[Scope | None, typer.Option("--scope")] = None,
) -> None:
    """Sort TOML keys while preserving table hierarchy."""
    if in_place and check:
        raise typer.BadParameter(_MUTUALLY_EXCLUSIVE_OPTIONS)
    # Concatenated documents on stdout have no boundaries, so a redirect
    # would silently merge them into one invalid TOML file.
    if len(paths) > 1 and not (in_place or check):
        raise typer.BadParameter(_MULTIPLE_PATHS_NEED_MODE)

    exit_code = 0
    for path in paths:
        exit_code = max(
            exit_code,
            _process_file(
                path, in_place=in_place, check=check, order=order, scope=scope
            ),
        )
    if exit_code:
        raise typer.Exit(code=exit_code)


def _process_file(
    path: Path,
    *,
    in_place: bool,
    check: bool,
    order: OrderMode | None,
    scope: Scope | None,
) -> int:
    """Sort one file and return its exit code (0 ok, 1 check diff, 2 error)."""
    try:
        settings = _resolve_settings(path, order, scope)
    except _ConfigError as error:
        typer.echo(str(error), err=True)
        return 2

    try:
        with path.open(encoding="utf-8", newline="") as handle:
            raw_source = handle.read()
        linesep = _detect_linesep(raw_source)
        # Mixed endings: parse the raw source unchanged so tomlkit's per-line
        # trivia keeps each line's original ending, mirroring TOMLFile's
        # passthrough behavior instead of flattening everything to LF.
        source = raw_source if linesep == "mixed" else raw_source.replace("\r\n", "\n")
        sorted_source = sort_toml(
            source, settings.order, settings.scope, settings.first
        )
    except (TOMLKitError, UnicodeDecodeError, OSError, RecursionError) as error:
        typer.echo(f"{path}: {error}", err=True)
        return 2

    if check:
        return 1 if source != sorted_source else 0

    output = _apply_linesep(sorted_source, linesep)

    if in_place:
        if source != sorted_source:
            try:
                with path.open("w", encoding="utf-8", newline="") as handle:
                    _ = handle.write(output)
            except OSError as error:
                typer.echo(f"{path}: {error}", err=True)
                return 2
        return 0

    # Bytes bypass text-mode newline translation: on Windows a str write would
    # rewrite the already-restored "\r\n" endings to "\r\r\n".
    typer.echo(output.encode("utf-8"), nl=False)
    return 0
