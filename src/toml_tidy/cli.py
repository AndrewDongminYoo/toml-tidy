"""Command-line interface for hierarchical TOML sorting."""

import ctypes
import ctypes.util
import difflib
import errno
import os
import re
import shutil
import stat
import sys
import tomllib
import unicodedata
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
_DIFF_EXCLUSIVE_OPTIONS: Final = "--diff cannot be used with --in-place or --check"
_MULTIPLE_PATHS_NEED_MODE: Final = (
    "multiple paths require --diff, --in-place or --check; stdout output takes one path"
)
_NO_NEWLINE_MARKER: Final = "\\ No newline at end of file\n"
_AMBIGUOUS_DIFF_PATH: Final = (
    "--diff cannot name this path in a patch header: patch would not read it "
    "back as exactly this name (leading or trailing whitespace, a control "
    "character, or a leading double quote)"
)
_LONE_LF: Final = re.compile(r"(?<!\r)\n")
_CONFIG_KEYS: Final = frozenset(
    {"order", "scope", "first", "blank-lines", "line-width"}
)
_PYPROJECT: Final = "pyproject.toml"
# Per directory, the first of these that applies wins (Ruff's order).
_CONFIG_FILES: Final = (".toml-tidy.toml", "toml-tidy.toml", _PYPROJECT)
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
    it — and :func:`_acl_matches` confirms afterwards that it did. macOS
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
    """Invalid or unreadable toml-tidy configuration file."""


class _Settings(NamedTuple):
    order: OrderMode
    scope: Scope
    first: tuple[str, ...]
    blank_lines: bool
    line_width: int | None


def _as_table(value: object) -> dict[str, object]:
    # TOML table keys are always strings, so the cast is sound.
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _load_section(path: Path) -> dict[str, object] | None:
    """Read the settings that a configuration file holds.

    A dedicated file holds them at its top level, so it always applies, even
    when empty. A ``pyproject.toml`` holds them in ``[tool.toml-tidy]`` and
    returns ``None`` when that table is absent.
    """
    try:
        with path.open("rb") as handle:
            document: dict[str, object] = tomllib.load(handle)
    except (
        tomllib.TOMLDecodeError,
        UnicodeDecodeError,
        OSError,
        RecursionError,
    ) as error:
        message = f"{path}: {error}"
        raise _ConfigError(message) from None
    if path.name != _PYPROJECT:
        return document
    raw_section = _as_table(document.get("tool")).get("toml-tidy")
    if raw_section is None:
        return None
    # A present but non-table value is a config mistake, not an absent
    # config — silently falling back to defaults would hide it.
    if not isinstance(raw_section, dict):
        message = f"{path}: [tool.toml-tidy] must be a table, got {raw_section!r}"
        raise _ConfigError(message)
    return cast("dict[str, object]", raw_section)


def _user_config_dir() -> Path:
    """Return the per-user configuration directory, as Ruff locates it.

    This mirrors etcetera's ``choose_base_strategy().config_dir()``:
    ``%APPDATA%`` on Windows, else ``$XDG_CONFIG_HOME`` when it is an
    absolute path, else ``~/.config`` — on macOS too.
    """
    if _IS_WINDOWS:
        appdata = os.environ.get("APPDATA")
        return Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    xdg_config_home = Path(os.environ.get("XDG_CONFIG_HOME", ""))
    if xdg_config_home.is_absolute():
        return xdg_config_home
    return Path.home() / ".config"


def _find_user_config() -> Path | None:
    """Return the first existing file in the user-level ``toml-tidy`` directory.

    The lookup is an optional fallback, so a home directory that cannot be
    determined or a probe that cannot stat its candidate means no user file,
    as in Ruff, whose ``find_user_settings_toml`` gives up on both.
    """
    try:
        user_dir = _user_config_dir() / "toml-tidy"
        for name in _CONFIG_FILES:
            candidate = user_dir / name
            if candidate.is_file():
                return candidate
    except (OSError, RuntimeError):
        return None
    return None


def _find_config(target: Path) -> tuple[Path, dict[str, object]] | None:
    """Locate the configuration file that applies to ``target``.

    The closest directory holding a configuration file wins, and within one
    directory the first of :data:`_CONFIG_FILES` that applies; a
    ``pyproject.toml`` without ``[tool.toml-tidy]`` is skipped. Nothing is
    merged. When the hierarchy has none, the first existing file in the
    user-level ``toml-tidy`` directory applies.
    """
    try:
        # Python 3.12's Path.resolve() raises RuntimeError on cyclic
        # symlinks (3.13+ resolves as far as possible instead), and
        # is_file() raises OSError for candidates it cannot stat, e.g. a
        # pyproject.toml symlinked into an unsearchable directory. Reading a
        # candidate raises _ConfigError instead, which passes through here.
        directory = target.resolve().parent
        for candidate_dir in (directory, *directory.parents):
            for name in _CONFIG_FILES:
                candidate = candidate_dir / name
                if candidate.is_file():
                    section = _load_section(candidate)
                    if section is not None:
                        return candidate, section
    except (OSError, RuntimeError) as error:
        message = f"{target}: {error}"
        raise _ConfigError(message) from None
    user_config = _find_user_config()
    if user_config is None:
        return None
    return user_config, _load_section(user_config) or {}


def _resolve_settings(
    config: Path | None,
    section: dict[str, object],
    order: OrderMode | None,
    scope: Scope | None,
    blank_lines: bool | None,
    line_width: int | None,
) -> _Settings:
    """Merge CLI flags over the settings ``section`` read from ``config``.

    Only a key whose flag is absent is validated, since a flag replaces it.
    """
    unknown_keys = sorted(section.keys() - _CONFIG_KEYS)
    if unknown_keys:
        noun = "key" if len(unknown_keys) == 1 else "keys"
        rendered = ", ".join(repr(key) for key in unknown_keys)
        message = f"{config}: unknown configuration {noun}: {rendered}"
        raise _ConfigError(message)

    if order is None:
        order_raw = section.get("order", OrderMode.NATURAL.value)
        order = _parse_enum(order_raw, OrderMode, "order", config)
    if scope is None:
        scope_raw = section.get("scope", Scope.ALL.value)
        scope = _parse_enum(scope_raw, Scope, "scope", config)
    if blank_lines is None:
        blank_lines = _parse_bool(section.get("blank-lines", False), config)
    if line_width is None:
        line_width = _parse_line_width(section.get("line-width"), config)
    first = _parse_first(section.get("first", []), config)

    return _Settings(order, scope, first, blank_lines, line_width)


def _parse_enum[E: StrEnum](
    value: object, enum_type: type[E], key: str, config: Path | None
) -> E:
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError:
            allowed = ", ".join(repr(member.value) for member in enum_type)
            detail = f"invalid {key} {value!r} (expected one of: {allowed})"
    else:
        detail = f"{key} must be a string, got {value!r}"
    message = f"{config}: {detail}"
    raise _ConfigError(message)


def _parse_bool(value: object, config: Path | None) -> bool:
    if isinstance(value, bool):
        return value
    message = f"{config}: blank-lines must be a boolean, got {value!r}"
    raise _ConfigError(message)


def _parse_line_width(value: object, config: Path | None) -> int | None:
    if value is None:
        return None
    # bool is an int subclass, so `line-width = true` would otherwise pass.
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    message = f"{config}: line-width must be a positive integer, got {value!r}"
    raise _ConfigError(message)


def _parse_first(value: object, config: Path | None) -> tuple[str, ...]:
    if isinstance(value, list):
        items = cast("list[object]", value)
        first = tuple(item for item in items if isinstance(item, str))
        if len(first) == len(items):
            return first
    message = f"{config}: first must be a list of strings, got {value!r}"
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


def _split_patch_lines(content: str) -> list[str]:
    """Split on LF alone, keeping each ending, the way ``patch`` counts lines.

    ``str.splitlines`` would also break at characters such as U+0085 and
    U+2028 that TOML strings and comments may hold, which would make the
    hunk line counts disagree with the file ``patch`` reads.
    """
    lines = [f"{line}\n" for line in content.split("\n")]
    lines[-1] = lines[-1].removesuffix("\n")
    if not lines[-1]:
        _ = lines.pop()
    return lines


def _render_diff(path: Path, before: str, after: str) -> bytes:
    """Render a unified diff that ``patch`` applies to the file at ``path``.

    Each line keeps its own ending, so a CRLF file's diff carries CRLF
    content lines. ``difflib`` emits a final line that lacks a newline as-is;
    the standard marker is added after it so the hunk stays well formed.
    A non-UTF-8 filename arrives surrogate-escaped, so ``surrogateescape``
    writes its original bytes into the headers instead of raising.
    Each header name ends with a TAB, as GNU diff writes it: ``patch`` reads
    the name up to that TAB, and without it stops at the first space.
    ``difflib`` adds the TAB only before a file date, so it is appended here.
    """
    rendered: list[str] = []
    for line in difflib.unified_diff(
        _split_patch_lines(before),
        _split_patch_lines(after),
        fromfile=f"{path}\t",
        tofile=f"{path}\t",
    ):
        rendered.append(line)
        if not line.endswith("\n"):
            rendered.extend(("\n", _NO_NEWLINE_MARKER))
    return "".join(rendered).encode("utf-8", "surrogateescape")


def _patch_reads_back(name: str) -> bool:
    """Return whether ``patch`` reads ``name`` back from a header unchanged.

    The invariant is that a header name must come back from ``patch`` as
    exactly that name. ``patch`` strips whitespace around a name, ends it at a
    TAB or line break, and reads a leading double quote as C quoting; no single
    quoting form works for both GNU and BSD ``patch``. So a name passes only
    when it has no surrounding whitespace, no leading double quote, and no
    control character. Other characters, including a non-ASCII space inside
    the name or a surrogate-escaped non-UTF-8 byte, are written as their own
    bytes and read back unchanged.
    """
    return (
        name == name.strip()
        and not name.startswith('"')
        and not any(unicodedata.category(char) == "Cc" for char in name)
    )


def _print_diff(path: Path, before: str, after: str) -> int:
    """Print the diff for ``path`` and return 1, or report it and return 2.

    A path that ``patch`` would read back as another name gets no diff.
    """
    if not _patch_reads_back(str(path)):
        typer.echo(f"{path}: {_AMBIGUOUS_DIFF_PATH}", err=True)
        return 2
    typer.echo(_render_diff(path, before, after), nl=False)
    return 1


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


def _read_acl_xattr(
    path: Path, getxattr: "Callable[[Path, str], bytes]"
) -> bytes | None:
    """Return the file's POSIX ACL, or ``None`` when it demonstrably has none.

    Raises when the answer could not be obtained, which is not the same thing
    as an absence and must not be recorded as one.
    """
    try:
        return getxattr(path, _ACL_XATTR)
    except OSError as error:
        if error.errno in _XATTR_ABSENT_ERRNOS:
            return None
        raise


def _acl_matches(target: Path, copy: Path) -> bool:
    """Say whether the replacement carries exactly the target's ACL.

    Both directions matter. ``shutil.copystat`` swallows a refused
    ``setxattr``, so it can report success having carried nothing — and the
    temporary file is created in the target's own directory, so a default ACL
    there is inherited onto a replacement whose target had none, handing the
    file an access rule it never carried. Either way the inode is kept
    instead.
    """
    getxattr = cast("Callable[[Path, str], bytes] | None", vars(os).get("getxattr"))
    if getxattr is None:
        # Only macOS reaches here without the attribute API, and its own probe
        # already found the target ACL-free, so the copy is the open question.
        # Anywhere else, no API means no way to tell, which is not a yes.
        return sys.platform == "darwin" and not _HAS_ACL(copy)
    try:
        return _read_acl_xattr(target, getxattr) == _read_acl_xattr(copy, getxattr)
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
                if not _acl_matches(target, temporary_path):
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
    diff: Annotated[bool, typer.Option("--diff")] = False,
    order: Annotated[OrderMode | None, typer.Option("--order")] = None,
    scope: Annotated[Scope | None, typer.Option("--scope")] = None,
    blank_lines: Annotated[
        bool | None, typer.Option("--blank-lines/--no-blank-lines")
    ] = None,
    line_width: Annotated[int | None, typer.Option("--line-width", min=1)] = None,
    # No exists=True: a bad --config reports "{path}: {message}" like any
    # other configuration file, not as a Click usage error.
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Sort TOML keys while preserving table hierarchy."""
    if in_place and check:
        raise typer.BadParameter(_MUTUALLY_EXCLUSIVE_OPTIONS)
    if diff and (in_place or check):
        raise typer.BadParameter(_DIFF_EXCLUSIVE_OPTIONS)
    # Concatenated documents on stdout have no boundaries, so a redirect
    # would silently merge them into one invalid TOML file.
    if len(paths) > 1 and not (in_place or check or diff):
        raise typer.BadParameter(_MULTIPLE_PATHS_NEED_MODE)

    # --config skips discovery, so its settings are resolved and validated
    # once, before any target is touched.
    fixed_settings: _Settings | None = None
    if config is not None:
        try:
            section = _load_section(config) or {}
            fixed_settings = _resolve_settings(
                config, section, order, scope, blank_lines, line_width
            )
        except _ConfigError as error:
            typer.echo(str(error), err=True)
            raise typer.Exit(code=2) from None

    exit_code = 0
    for path in paths:
        exit_code = max(
            exit_code,
            _process_file(
                path,
                in_place=in_place,
                check=check,
                diff=diff,
                order=order,
                scope=scope,
                blank_lines=blank_lines,
                line_width=line_width,
                fixed_settings=fixed_settings,
            ),
        )
    if exit_code:
        raise typer.Exit(code=exit_code)


def _process_file(
    path: Path,
    *,
    in_place: bool,
    check: bool,
    diff: bool,
    order: OrderMode | None,
    scope: Scope | None,
    blank_lines: bool | None,
    line_width: int | None,
    fixed_settings: _Settings | None,
) -> int:
    """Sort one file and return its exit code (0 ok, 1 would change, 2 error)."""
    settings = fixed_settings
    if settings is None:
        try:
            found = _find_config(path)
            config, section = found if found is not None else (None, {})
            settings = _resolve_settings(
                config, section, order, scope, blank_lines, line_width
            )
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
            line_width=settings.line_width,
        )
    except (TOMLKitError, UnicodeDecodeError, OSError, RecursionError) as error:
        typer.echo(f"{path}: {error}", err=True)
        return 2

    output = _apply_linesep(sorted_source, linesep)

    if check or diff:
        changed = source != sorted_source
        # Against the raw bytes and the restored output, never the
        # LF-normalized forms, so a CRLF file's diff applies to that file.
        return (
            _print_diff(path, raw_source, output) if diff and changed else int(changed)
        )

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
