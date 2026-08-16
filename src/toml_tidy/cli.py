"""Command-line interface for hierarchical TOML sorting."""

import ctypes
import ctypes.util
import errno
import os
import re
import shutil
import stat
import sys
import tomllib
from contextlib import suppress
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Annotated, Final, NamedTuple, cast

if TYPE_CHECKING:
    from collections.abc import Callable

import typer
from tomlkit.exceptions import TOMLKitError

from toml_tidy.sorter import OrderMode, Scope, sort_toml

app = typer.Typer(help="Sort TOML keys while preserving table hierarchy.")
_MUTUALLY_EXCLUSIVE_OPTIONS: Final = "--in-place and --check cannot be used together"
_MULTIPLE_PATHS_NEED_MODE: Final = (
    "multiple paths require --in-place or --check; stdout output takes one path"
)
_LONE_LF: Final = re.compile(r"(?<!\r)\n")
_CONFIG_KEYS: Final = frozenset({"order", "scope", "first", "blank-lines"})
_IS_WINDOWS: Final = os.name == "nt"
_ACL_TYPE_EXTENDED: Final = 0x00000100  # <sys/acl.h>
# A NULL acl_get_file is both "this file carries none" and "I could not look".
# Only these two mean the former, and only the former is safe to replace on.
_ACL_ABSENT_ERRNOS: Final = frozenset({errno.ENOENT, errno.ENOTSUP, errno.EOPNOTSUPP})
_ACL_XATTR: Final = "system.posix_acl_access"  # where Linux keeps a POSIX ACL
# The same distinction one API over: only these mean the attribute is absent.
_XATTR_ABSENT_ERRNOS: Final = frozenset(
    {errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP}
)


def _load_acl_probe() -> "Callable[[Path], bool]":
    """Build the test for an ACL that replacement would not carry over.

    Linux needs none up front: ``shutil.copystat`` copies extended attributes
    there, which is where a POSIX ACL lives, so the replacement normally keeps
    it — and :func:`_acl_reached` confirms afterwards that it did. macOS
    exposes its ACLs through ``acl_get_file`` alone, which the standard
    library does not wrap, so the check is bound through ``ctypes``.

    Every other POSIX platform answers yes unconditionally. FreeBSD and its
    relatives do carry ACLs that ``copystat`` drops, but nothing here can
    exercise them, and a probe written blind is as likely to answer yes for
    every file — retiring the atomic write — as to answer correctly. Giving
    up the replacement is the half of that trade that cannot lose anything.
    """
    if sys.platform == "linux":
        return lambda _path: False
    if sys.platform != "darwin":
        return lambda _path: True

    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    acl_get_file = libc.acl_get_file
    acl_get_file.restype = ctypes.c_void_p
    acl_get_file.argtypes = (ctypes.c_char_p, ctypes.c_uint)
    acl_free = libc.acl_free
    acl_free.restype = ctypes.c_int
    acl_free.argtypes = (ctypes.c_void_p,)

    def has_acl(path: Path) -> bool:
        _ = ctypes.set_errno(0)
        acl = cast("int | None", acl_get_file(os.fsencode(path), _ACL_TYPE_EXTENDED))
        if acl is None:
            return ctypes.get_errno() not in _ACL_ABSENT_ERRNOS
        _ = cast("int", acl_free(acl))
        return True

    return has_acl


_HAS_ACL: Final = _load_acl_probe()


class _ConfigError(Exception):
    """Invalid or unreadable ``[tool.toml-tidy]`` configuration."""


class _Settings(NamedTuple):
    order: OrderMode
    scope: Scope
    first: tuple[str, ...]
    blank_lines: bool


def _as_table(value: object) -> dict[str, object]:
    # TOML table keys are always strings, so the cast is sound.
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _find_pyproject(target: Path) -> Path | None:
    try:
        # Python 3.12's Path.resolve() raises RuntimeError on cyclic
        # symlinks (3.13+ resolves as far as possible instead), and
        # is_file() raises OSError for candidates it cannot stat, e.g. a
        # pyproject.toml symlinked into an unsearchable directory.
        directory = target.resolve().parent
        for candidate_dir in (directory, *directory.parents):
            candidate = candidate_dir / "pyproject.toml"
            if candidate.is_file():
                return candidate
    except (OSError, RuntimeError) as error:
        message = f"{target}: {error}"
        raise _ConfigError(message) from None
    return None


def _resolve_settings(
    target: Path,
    order: OrderMode | None,
    scope: Scope | None,
    blank_lines: bool | None,
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
        except (
            tomllib.TOMLDecodeError,
            UnicodeDecodeError,
            OSError,
            RecursionError,
        ) as error:
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

    unknown_keys = sorted(section.keys() - _CONFIG_KEYS)
    if unknown_keys:
        noun = "key" if len(unknown_keys) == 1 else "keys"
        rendered = ", ".join(repr(key) for key in unknown_keys)
        message = f"{pyproject}: unknown configuration {noun}: {rendered}"
        raise _ConfigError(message)

    if order is None:
        order_raw = section.get("order", OrderMode.NATURAL.value)
        order = _parse_enum(order_raw, OrderMode, "order", pyproject)
    if scope is None:
        scope_raw = section.get("scope", Scope.ALL.value)
        scope = _parse_enum(scope_raw, Scope, "scope", pyproject)
    if blank_lines is None:
        blank_lines = _parse_bool(section.get("blank-lines", False), pyproject)
    first = _parse_first(section.get("first", []), pyproject)

    return _Settings(order, scope, first, blank_lines)


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


def _parse_bool(value: object, pyproject: Path | None) -> bool:
    if isinstance(value, bool):
        return value
    message = f"{pyproject}: blank-lines must be a boolean, got {value!r}"
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


def _write_existing(path: Path, content: str, target_stat: os.stat_result) -> None:
    """Rewrite an existing inode when replacement cannot preserve security metadata.

    Refuses a set-user-ID or set-group-ID target outright. Writing clears
    those bits from the first byte on, and putting them back is not reliably
    available: it needs ownership the writer need not have, and for
    set-group-ID a ``chmod`` outside the file's group clears the bit while
    reporting success. Every restoring version of this ended up able to
    strip a bit under some caller, so the whole class is declined instead —
    on the atomic path, where ``shutil.copystat`` carries the mode across
    intact, these files are still sorted normally.
    """
    if target_stat.st_mode & (stat.S_ISUID | stat.S_ISGID):
        message = "refusing to rewrite a set-ID file in place"
        raise PermissionError(errno.EPERM, message, str(path))

    with path.open("w", encoding="utf-8", newline="") as handle:
        _ = handle.write(content)


def _acl_reached(target: Path, copy: Path) -> bool:
    """Say whether the target's POSIX ACL actually landed on its replacement.

    ``shutil.copystat`` carries a Linux ACL across as an extended attribute,
    but swallows the ``setxattr`` failure when a policy or mount refuses it —
    so it can report success having copied nothing. Asked here rather than
    assumed, since the answer decides whether replacing the inode would drop
    an access rule.
    """
    getxattr = cast("Callable[[Path, str], bytes] | None", vars(os).get("getxattr"))
    if getxattr is None:
        return True
    try:
        wanted = getxattr(target, _ACL_XATTR)
    except OSError as error:
        # Absent means there is nothing to carry, and replacing is safe.
        # Unreadable means the check did not run, which is not the same
        # answer — and the copy suppresses the very failure that produced it.
        return error.errno in _XATTR_ABSENT_ERRNOS
    try:
        return getxattr(copy, _ACL_XATTR) == wanted
    except OSError:
        return False


def _atomic_write(path: Path, content: str) -> None:
    """Replace a file only after its complete replacement is safely written."""
    target = path.resolve(strict=True)
    target_stat = target.stat()
    if not target_stat.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
        raise PermissionError(13, "Permission denied", str(path))

    writable_descriptor = os.open(target, os.O_WRONLY)
    os.close(writable_descriptor)
    if _IS_WINDOWS or target_stat.st_nlink > 1 or _HAS_ACL(target):
        _write_existing(target, content, target_stat)
        return

    temporary_path: Path | None = None
    try:
        try:
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                newline="",
                dir=target.parent,
                prefix=".toml-tidy-",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                _ = handle.write(content)
                handle.flush()
                if hasattr(os, "fchown"):
                    try:
                        os.fchown(
                            handle.fileno(), target_stat.st_uid, target_stat.st_gid
                        )
                    except (NotImplementedError, PermissionError):
                        _write_existing(target, content, target_stat)
                        return

                shutil.copystat(target, temporary_path)
                if not _acl_reached(target, temporary_path):
                    _write_existing(target, content, target_stat)
                    return

                os.utime(temporary_path, None)
                os.fsync(handle.fileno())
        except PermissionError:
            if temporary_path is not None:
                raise
            _write_existing(target, content, target_stat)
            return

        _ = temporary_path.replace(target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink()


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
    blank_lines: Annotated[
        bool | None, typer.Option("--blank-lines/--no-blank-lines")
    ] = None,
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
                path,
                in_place=in_place,
                check=check,
                order=order,
                scope=scope,
                blank_lines=blank_lines,
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
    blank_lines: bool | None,
) -> int:
    """Sort one file and return its exit code (0 ok, 1 check diff, 2 error)."""
    try:
        settings = _resolve_settings(path, order, scope, blank_lines)
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
            source,
            settings.order,
            settings.scope,
            settings.first,
            blank_lines=settings.blank_lines,
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
                _atomic_write(path, output)
            except OSError as error:
                typer.echo(f"{path}: {error}", err=True)
                return 2
        return 0

    # Bytes bypass text-mode newline translation: on Windows a str write would
    # rewrite the already-restored "\r\n" endings to "\r\r\n".
    typer.echo(output.encode("utf-8"), nl=False)
    return 0
