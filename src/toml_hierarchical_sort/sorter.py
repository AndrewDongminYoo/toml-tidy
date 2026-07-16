"""Sort TOML key entries while retaining parsed formatting items."""

import re
from enum import StrEnum
from typing import Literal

import tomlkit
from tomlkit.container import Container
from tomlkit.items import AoT, Comment, Item, Key, Table, Whitespace

type BodyEntry = tuple[Key | None, Item]
type SegmentKind = Literal["key", "table"]
type SortKey = tuple[tuple[int, int | str], ...]

_NATURAL_PARTS = re.compile(r"(\d+)")


class OrderMode(StrEnum):
    """Supported direct-key ordering modes."""

    NATURAL = "natural"
    ALPHA = "alpha"


def sort_toml(source: str, order: OrderMode = OrderMode.NATURAL) -> str:
    """Return source with direct keys sorted recursively."""
    document = tomlkit.parse(source)
    _ensure_trailing_newline(document)
    _sort_container(document, order)
    return tomlkit.dumps(document)


def _ensure_trailing_newline(container: Container) -> None:
    """Force the deepest last body item to end with a newline.

    A source without a trailing newline leaves its last item's trivia trail
    empty. If sorting moves that item away from the end, it would otherwise
    be glued to whatever now follows it, producing invalid TOML.
    """
    while container.body:
        key, item = container.body[-1]
        match item:
            case Table():
                container = item.value
            case AoT():
                if not item.body:
                    return
                container = item.body[-1].value
            case _:
                if key is not None and not item.trivia.trail.endswith("\n"):
                    item.trivia.trail += "\n"
                return


def _sort_container(container: Container, order: OrderMode) -> None:
    """Sort direct key segments and then visit descendant table containers."""
    container.body[:] = _sort_segments(container.body, order)

    for _, item in container.body:
        match item:
            case Table():
                _sort_container(item.value, order)
            case AoT():
                for table in item.body:
                    _sort_container(table.value, order)
            case _:
                continue


def _sort_segments(entries: list[BodyEntry], order: OrderMode) -> list[BodyEntry]:
    """Sort direct keys and sibling tables without crossing their boundaries."""
    sorted_entries: list[BodyEntry] = []
    segment: list[BodyEntry] = []
    segment_kind: SegmentKind | None = None

    for entry in entries:
        key, item = entry
        match item:
            case AoT():
                sorted_entries.extend(_sort_segment(segment, order))
                sorted_entries.append(entry)
                segment = []
                segment_kind = None
            case Table() if key is None or not key.is_dotted():
                if segment_kind == "key":
                    sorted_entries.extend(_sort_segment(segment, order))
                    segment = []
                segment.append(entry)
                segment_kind = "table"
            case _:
                if key is not None and segment_kind == "table":
                    sorted_entries.extend(_sort_segment(segment, order))
                    segment = []
                segment.append(entry)
                if key is not None:
                    segment_kind = "key"

    sorted_entries.extend(_sort_segment(segment, order))
    return sorted_entries


def _sort_segment(entries: list[BodyEntry], order: OrderMode) -> list[BodyEntry]:
    """Move leading comments with keys while keeping whitespace after prior keys."""
    entries = _hoist_header_comments(entries)
    leading: list[BodyEntry] = []
    pending: list[BodyEntry] = []
    groups: list[tuple[tuple[str, ...], list[BodyEntry]]] = []

    for entry in entries:
        key, item = entry
        if key is None:
            pending.append(entry)
            continue

        whitespace, comments = _split_before_first_comment(pending)
        pending = []
        if groups:
            groups[-1][1].extend(whitespace)
        else:
            leading.extend(whitespace)
        groups.append((_key_path(key, item), [*comments, entry]))

    sorted_groups = sorted(
        groups,
        key=lambda group: tuple(_sort_key(segment, order) for segment in group[0]),
    )
    return [
        *leading,
        *(entry for _, group in sorted_groups for entry in group),
        *pending,
    ]


def _split_before_first_comment(
    trivia: list[BodyEntry],
) -> tuple[list[BodyEntry], list[BodyEntry]]:
    """Split a trivia run at its first comment, keeping the interleaved order."""
    first_comment = next(
        (i for i, (_, item) in enumerate(trivia) if not isinstance(item, Whitespace)),
        len(trivia),
    )
    return trivia[:first_comment], trivia[first_comment:]


def _hoist_header_comments(entries: list[BodyEntry]) -> list[BodyEntry]:
    """Splice each table's trailing comment run in front of the next sibling.

    A standalone comment written directly above a ``[table]`` header is parsed
    into the tail of the previous table's body, so reordering sibling tables
    would strand it. Hoisting it to the segment level lets it travel as the
    next declaration's leading-comment group. The last table keeps its tail.
    """
    table_indexes = [
        index
        for index, (key, item) in enumerate(entries)
        if isinstance(item, Table) and (key is None or not key.is_dotted())
    ]
    if not table_indexes:
        return entries

    hoisted: list[BodyEntry] = []
    for index, entry in enumerate(entries):
        hoisted.append(entry)
        if index in table_indexes[:-1] and isinstance(entry[1], Table):
            hoisted.extend(_pop_trailing_comment_run(entry[1].value))
    return hoisted


def _pop_trailing_comment_run(container: Container) -> list[BodyEntry]:
    """Remove and return the trailing comment run of the deepest last body.

    The run starts at the first comment of the body's trailing trivia; pure
    whitespace before it stays at the table boundary, while whitespace
    interleaved with the comments moves along in its original order.
    """
    while container.body:
        _, item = container.body[-1]
        match item:
            case Table():
                container = item.value
            case AoT():
                if not item.body:
                    return []
                container = item.body[-1].value
            case _:
                break

    body = container.body
    start = len(body)
    for index in range(len(body) - 1, -1, -1):
        key, item = body[index]
        if key is not None or not isinstance(item, Comment | Whitespace):
            break
        if isinstance(item, Comment):
            start = index

    run = body[start:]
    del body[start:]
    return run


def _key_path(key: Key, item: Item) -> tuple[str, ...]:
    """Return the parsed logical segments of a key, expanding dotted keys."""
    if not key.is_dotted():
        return (key.key,)

    path = [key.key]
    while isinstance(item, Table) and item.is_super_table():
        children = [
            (child_key, child_item)
            for child_key, child_item in item.value.body
            if child_key is not None
        ]
        if len(children) != 1:
            break
        child_key, item = children[0]
        path.append(child_key.key)
    return tuple(path)


def _sort_key(key: str, order: OrderMode) -> SortKey:
    """Produce a comparable key from a TOML key's parsed logical value."""
    match order:
        case OrderMode.NATURAL:
            return _natural_key(key)
        case OrderMode.ALPHA:
            return ((0, key.casefold()), (0, key))


def _natural_key(key: str) -> SortKey:
    """Return case-insensitive text and numeric runs in natural comparison order."""
    parts: list[tuple[int, int | str]] = []

    for part in _NATURAL_PARTS.split(key):
        if part.isdecimal():
            parts.append((1, int(part)))
        else:
            parts.append((0, part.casefold()))

    parts.append((0, key))
    return tuple(parts)
