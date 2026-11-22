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

from toml_tidy.sorter import OrderMode, Override, Scope, sort_toml

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
# C0 and C1 controls (TAB, line breaks, ESC, DEL), the Unicode line and
# paragraph separators, and the surrogates an undecodable filename byte
# arrives as: each can split one error into several lines or drive the
# terminal, so `_report` prints them as escapes.
_UNPRINTABLE: Final = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff]")
_CONFIG_KEYS: Final = frozenset(
    {"blank-lines", "first", "line-width", "order", "overrides", "scope"}
)
_OVERRIDE_KEYS: Final = frozenset({"first", "last", "path", "sort"})
# One segment of an override path: a basic or literal TOML string, or a bare
# key widened by the glob characters fnmatch reads. A closed bracket class is
# one token, so a "." inside it never splits the segment; a lone "[" is kept
# so _unclosed_class can reject it with a clear message.
_PATH_SEGMENT: Final = re.compile(
    r"""
    [ \t]*
    (
        "(?:[^"\\\n]|\\.)*"
        | '[^'\n]*'
        | (?:\[!?\]?[^\]\n]*\]|[A-Za-z0-9_\-*?!\[])+
    )
    [ \t]*
    """,
    re.VERBOSE,
)
_PYPROJECT: Final = "pyproject.toml"
# Per directory, the first of these that applies wins (Ruff's order).
_CONFIG_FILES: Final = (".toml-tidy.toml", "toml-tidy.toml", _PYPROJECT)
_IS_WINDOWS: Final = os.name == "nt"
_ACL_TYPE_EXTENDED: Final = 0x00000100  # <sys/acl.h>
# A NULL acl_get_file is both "this file carries none" and "I could not look".
# Only these two mean the former, and only the former is safe to replace on.
_ACL_ABSENT_ERRNOS: Final = frozenset({errno.ENOENT, errno.ENOTSUP, errno.EOPNOTSUPP})
_CAPABILITY_XATTR: Final = "security.capability"  # Linux file capabilities
# The same distinction one API over: only these mean the attribute is absent.
_XATTR_ABSENT_ERRNOS: Final = frozenset(
    {errno.ENODATA, errno.ENOTSUP, errno.EOPNOTSUPP}
)


def _load_acl_probe() -> "Callable[[Path], bool]":
    """Build the test for an ACL that replacement would not carry over.

    Linux needs none up front: ``shutil.copystat`` copies extended attributes
    there, which is where a POSIX ACL lives, so the replacement normally keeps
    it — and :func:`_attributes_match` confirms afterwards that it did. macOS
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
    overrides: tuple[Override, ...]


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


type _Found = tuple[Path, dict[str, object]]


def _reraise[T](outcome: T | OSError | _ConfigError) -> T:
    """Return a memoized result, or raise the memoized error again."""
    if isinstance(outcome, (OSError, _ConfigError)):
        # Drop the previous raise's traceback so it does not grow per target.
        raise outcome.with_traceback(None)
    return outcome


class _Discovery:
    """Configuration discovery memoized for one CLI invocation.

    ``sort_file`` creates one per call, so nothing outlives the process or
    carries over between invocations. Within one, each directory is searched,
    each configuration file read, and the user-level directory probed at
    most once. A failure is memoized like a result and raised again for every
    target that reaches it, so each of them reports it as it would uncached.
    """

    def __init__(self) -> None:
        self._sections: dict[Path, dict[str, object] | _ConfigError | None] = {}
        self._directories: dict[Path, _Found | OSError | _ConfigError | None] = {}
        self._user_config: Path | None = None
        self._user_config_looked_up: bool = False

    def load_section(self, path: Path) -> dict[str, object] | None:
        """Return :func:`_load_section` for ``path``, reading it only once."""
        if path not in self._sections:
            try:
                self._sections[path] = _load_section(path)
            except _ConfigError as error:
                self._sections[path] = error
        return _reraise(self._sections[path])

    def search(self, directory: Path) -> _Found | None:
        """Return the closest configuration at or above ``directory``.

        The walk stops at the first directory already searched, and its
        outcome is recorded for every directory passed on the way, since
        none of those held a configuration of its own.
        """
        walked: list[Path] = []
        outcome: _Found | OSError | _ConfigError | None = None
        for candidate_dir in (directory, *directory.parents):
            if candidate_dir in self._directories:
                outcome = self._directories[candidate_dir]
                break
            walked.append(candidate_dir)
            try:
                outcome = self._probe(candidate_dir)
            except (OSError, _ConfigError) as error:
                outcome = error
            if outcome is not None:
                break
        for walked_dir in walked:
            self._directories[walked_dir] = outcome
        return _reraise(outcome)

    def _probe(self, directory: Path) -> _Found | None:
        for name in _CONFIG_FILES:
            candidate = directory / name
            if candidate.is_file():
                section = self.load_section(candidate)
                if section is not None:
                    return candidate, section
        return None

    def user_config(self) -> Path | None:
        """Return :func:`_find_user_config`, looking only once."""
        if not self._user_config_looked_up:
            self._user_config = _find_user_config()
            self._user_config_looked_up = True
        return self._user_config


def _find_config(target: Path, discovery: _Discovery) -> _Found | None:
    """Locate the configuration file that applies to ``target``.

    The closest directory holding a configuration file wins, and within one
    directory the first of :data:`_CONFIG_FILES` that applies; a
    ``pyproject.toml`` without ``[tool.toml-tidy]`` is skipped. Nothing is
    merged. When the hierarchy has none, the first existing file in the
    user-level ``toml-tidy`` directory applies. ``discovery`` memoizes every
    step after resolving ``target`` itself.
    """
    try:
        # Python 3.12's Path.resolve() raises RuntimeError on cyclic
        # symlinks (3.13+ resolves as far as possible instead), and
        # is_file() raises OSError for candidates it cannot stat, e.g. a
        # pyproject.toml symlinked into an unsearchable directory. Reading a
        # candidate raises _ConfigError instead, which passes through here.
        found = discovery.search(target.resolve().parent)
    except (OSError, RuntimeError) as error:
        message = f"{target}: {error}"
        raise _ConfigError(message) from None
    if found is not None:
        return found
    user_config = discovery.user_config()
    if user_config is None:
        return None
    return user_config, discovery.load_section(user_config) or {}


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
    overrides = _parse_overrides(section.get("overrides", []), config)

    return _Settings(order, scope, first, blank_lines, line_width, overrides)


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


def _parse_first(
    value: object, config: Path | None, key: str = "first"
) -> tuple[str, ...]:
    if isinstance(value, list):
        items = cast("list[object]", value)
        first = tuple(item for item in items if isinstance(item, str))
        if len(first) == len(items):
            return first
    message = f"{config}: {key} must be a list of strings, got {value!r}"
    raise _ConfigError(message)


def _parse_overrides(value: object, config: Path | None) -> tuple[Override, ...]:
    if not isinstance(value, list):
        message = f"{config}: overrides must be an array of tables, got {value!r}"
        raise _ConfigError(message)
    return tuple(
        _parse_override(entry, config, f"overrides[{index}]")
        for index, entry in enumerate(cast("list[object]", value))
    )


def _parse_override(value: object, config: Path | None, where: str) -> Override:
    """Validate one ``[[overrides]]`` entry; every field error is fatal."""
    if not isinstance(value, dict):
        message = f"{config}: {where} must be a table, got {value!r}"
        raise _ConfigError(message)
    entry = cast("dict[str, object]", value)
    unknown_keys = sorted(entry.keys() - _OVERRIDE_KEYS)
    if unknown_keys:
        rendered = ", ".join(repr(key) for key in unknown_keys)
        message = f"{config}: {where}: unknown key(s): {rendered}"
        raise _ConfigError(message)
    if "path" not in entry:
        message = f"{config}: {where}: path is required"
        raise _ConfigError(message)
    path = _parse_key_pattern(entry["path"], config, where)
    first = _parse_first(entry.get("first", []), config, f"{where}.first")
    last = _parse_first(entry.get("last", []), config, f"{where}.last")
    sort = entry.get("sort", True)
    if not isinstance(sort, bool):
        message = f"{config}: {where}.sort must be a boolean, got {sort!r}"
        raise _ConfigError(message)
    return Override(path, first, last, sort)


def _parse_key_pattern(
    value: object, config: Path | None, where: str
) -> tuple[str, ...]:
    """Split a dotted-key glob into one ``fnmatch`` pattern per segment.

    Quoted segments decode as TOML strings, so they compare against the same
    logical key a parsed document holds. ``**`` is refused rather than read
    as ``*``: no pattern may cross a segment boundary.
    """
    if not isinstance(value, str):
        message = f"{config}: {where}.path must be a string, got {value!r}"
        raise _ConfigError(message)
    segments = _split_key_pattern(value)
    if segments is None:
        message = f"{config}: {where}.path is not a dotted key pattern: {value!r}"
        raise _ConfigError(message)
    if any("**" in segment for segment in segments):
        message = f"{config}: {where}.path does not support '**': {value!r}"
        raise _ConfigError(message)
    if any(_unclosed_class(segment) for segment in segments):
        message = f"{config}: {where}.path has an unclosed '[': {value!r}"
        raise _ConfigError(message)
    return segments


def _unclosed_class(pattern: str) -> bool:
    """Return whether a ``[`` in ``pattern`` opens a class with no ``]``.

    This follows ``fnmatch.translate``: a ``!`` and then a ``]`` directly
    after ``[`` are class members. ``fnmatch`` reads an unclosed ``[`` as a
    literal and raises nothing, so the override would never match a bare key.
    """
    index = 0
    while index < len(pattern):
        if pattern[index] != "[":
            index += 1
            continue
        end = index + 1
        if pattern.startswith("!", end):
            end += 1
        if pattern.startswith("]", end):
            end += 1
        end = pattern.find("]", end)
        if end < 0:
            return True
        index = end + 1
    return False


def _split_key_pattern(text: str) -> tuple[str, ...] | None:
    """Return a dotted key's segments, or ``None`` when it is not one."""
    segments: list[str] = []
    position = 0
    while True:
        match = _PATH_SEGMENT.match(text, position)
        if match is None:
            return None
        raw = match.group(1)
        if raw[0] in "\"'":
            try:
                raw = cast("str", tomllib.loads(f"k = {raw}")["k"])
            except tomllib.TOMLDecodeError:
                return None
        segments.append(raw)
        position = match.end()
        if position == len(text):
            return tuple(segments)
        if text[position] != ".":
            return None
        position += 1


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


def _report(message: str) -> None:
    r"""Print one error line to stderr with control characters escaped.

    A path or exception text can hold any character a filename can, so each
    one ``_UNPRINTABLE`` matches is written as its ``repr`` escape (``\t``,
    ``\x1b``, ``\u2028``, ``\udcff``) and the error stays one physical line.
    """
    escaped = _UNPRINTABLE.sub(lambda match: repr(match.group())[1:-1], message)
    typer.echo(escaped, err=True)


def _print_diff(path: Path, before: str, after: str) -> int:
    """Print the diff for ``path`` and return 1, or report it and return 2.

    A path that ``patch`` would read back as another name gets no diff.
    """
    if not _patch_reads_back(str(path)):
        _report(f"{path}: {_AMBIGUOUS_DIFF_PATH}")
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

    # Open without truncating and check the opened file before emptying it,
    # so the write lands only on the file that was checked.
    descriptor = os.open(path, os.O_WRONLY)
    try:
        _require_same_file(os.fstat(descriptor), target_stat, path)
        _refuse_file_capabilities(descriptor, path)
        os.ftruncate(descriptor, 0)
    except BaseException:
        os.close(descriptor)
        raise
    with open(descriptor, "w", encoding="utf-8", newline="") as handle:  # noqa: PTH123
        _ = handle.write(content)


def _refuse_file_capabilities(descriptor: int, path: Path) -> None:
    """Refuse a file that may carry Linux file capabilities.

    The kernel drops them on the first write, like the set-ID bits, so such a
    file is declined for the same reason. Asked of the checked descriptor
    rather than the name, so the answer is about the file to be truncated.
    """
    if _may_have_attribute(descriptor, _CAPABILITY_XATTR):
        message = "refusing to rewrite a file with file capabilities in place"
        raise PermissionError(errno.EPERM, message, str(path))


def _require_same_file(
    current: os.stat_result, expected: os.stat_result, path: Path
) -> None:
    """Refuse to write when ``path`` no longer names the file that was read."""
    if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
        message = "changed while it was being sorted; not written"
        raise OSError(errno.ESTALE, message, str(path))


def _read_attributes(
    path: Path,
    listxattr: "Callable[[Path], list[str]]",
    getxattr: "Callable[[Path, str], bytes]",
) -> dict[str, bytes]:
    """Return every extended attribute of ``path`` by name.

    A filesystem without extended attributes yields none. Raises when the
    answer could not be obtained, which is not the same thing as an absence
    and must not be recorded as one.
    """
    try:
        names = listxattr(path)
    except OSError as error:
        if error.errno in _XATTR_ABSENT_ERRNOS:
            return {}
        raise
    return {name: getxattr(path, name) for name in names}


def _attributes_match(target: Path, copy: Path) -> bool:
    """Say whether the replacement carries exactly the target's attributes.

    Both directions matter. ``shutil.copystat`` swallows a refused
    ``setxattr``, so it can report success having carried nothing: an ACL, a
    security label, an integrity or capability attribute. And the temporary
    file is created in the target's own directory, so a default ACL there is
    inherited onto a replacement whose target had none, handing the file an
    access rule it never carried. Either way the inode is kept instead.
    """
    listxattr = cast("Callable[[Path], list[str]] | None", vars(os).get("listxattr"))
    getxattr = cast("Callable[[Path, str], bytes] | None", vars(os).get("getxattr"))
    if listxattr is None or getxattr is None:
        # Only macOS reaches here without the attribute API, and its own probe
        # already found the target ACL-free, so the copy is the open question.
        # Anywhere else, no API means no way to tell, which is not a yes.
        return sys.platform == "darwin" and not _HAS_ACL(copy)
    try:
        return _read_attributes(target, listxattr, getxattr) == _read_attributes(
            copy, listxattr, getxattr
        )
    except OSError:
        return False


def _may_have_attribute(descriptor: int, name: str) -> bool:
    """Say whether the open file may carry attribute ``name``.

    An unreadable list counts as a yes; a filesystem without extended
    attributes counts as a no. Where the standard library has no
    ``listxattr`` (macOS), the answer is no.
    """
    listxattr = cast("Callable[[int], list[str]] | None", vars(os).get("listxattr"))
    if listxattr is None:
        return False
    try:
        return name in listxattr(descriptor)
    except OSError as error:
        return error.errno not in _XATTR_ABSENT_ERRNOS


def _atomic_write(path: Path, content: str, read_stat: os.stat_result) -> None:
    """Replace a file only after its complete replacement is safely written.

    ``read_stat`` is the file that was read and sorted. The name is resolved
    again here, so a file swapped in under it is refused rather than
    overwritten; the check repeats just before the rename, which leaves only
    the gap between that check and the rename itself.
    """
    target = path.resolve(strict=True)
    target_stat = target.stat()
    _require_same_file(target_stat, read_stat, path)
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
                if not _attributes_match(target, temporary_path):
                    _write_existing(target, content, target_stat)
                    return

                os.utime(temporary_path, None)
                os.fsync(handle.fileno())
        except PermissionError:
            if temporary_path is not None:
                raise
            _write_existing(target, content, target_stat)
            return

        # lstat: the rename overwrites this directory entry itself, so a
        # symlink placed here must not pass by naming the original file.
        _require_same_file(target.lstat(), target_stat, path)
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
            _report(str(error))
            raise typer.Exit(code=2) from None

    discovery = _Discovery()
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
                discovery=discovery,
            ),
        )
    if exit_code:
        raise typer.Exit(code=exit_code)


def _write_in_place(
    path: Path,
    output: str,
    read_stat: os.stat_result,
    held: int | None,
    *,
    changed: bool,
) -> int:
    """Write ``output`` over the file that was read, then release ``held``."""
    try:
        if changed:
            _atomic_write(path, output, read_stat)
    except OSError as error:
        _report(f"{path}: {error}")
        return 2
    finally:
        if held is not None:
            os.close(held)
    return 0


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
    discovery: _Discovery,
) -> int:
    """Sort one file and return its exit code (0 ok, 1 would change, 2 error)."""
    settings = fixed_settings
    if settings is None:
        try:
            found = _find_config(path, discovery)
            config, section = found if found is not None else (None, {})
            settings = _resolve_settings(
                config, section, order, scope, blank_lines, line_width
            )
        except _ConfigError as error:
            _report(str(error))
            return 2

    held: int | None = None
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            read_stat = os.fstat(handle.fileno())
            raw_source = handle.read()
            if in_place:
                # Kept open until the write ends, so the file's inode number
                # cannot be reused by another file in the meantime.
                held = os.dup(handle.fileno())
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
            overrides=settings.overrides,
        )
    except (TOMLKitError, UnicodeDecodeError, OSError, RecursionError) as error:
        if held is not None:
            os.close(held)
        _report(f"{path}: {error}")
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
        return _write_in_place(
            path, output, read_stat, held, changed=source != sorted_source
        )

    # Bytes bypass text-mode newline translation: on Windows a str write would
    # rewrite the already-restored "\r\n" endings to "\r\r\n".
    typer.echo(output.encode("utf-8"), nl=False)
    return 0
