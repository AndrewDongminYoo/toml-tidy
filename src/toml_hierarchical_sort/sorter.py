"""Sort TOML key entries while retaining parsed formatting items."""

import re
from enum import StrEnum
from typing import Literal

import tomlkit
from tomlkit.container import Container
from tomlkit.items import AoT, Item, Key, Table, Whitespace

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
    _sort_container(document, order)
    return tomlkit.dumps(document)


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
            case Table():
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
    leading_whitespace: list[BodyEntry] = []
    pending_comments: list[BodyEntry] = []
    pending_whitespace: list[BodyEntry] = []
    groups: list[tuple[str, list[BodyEntry]]] = []

    for entry in entries:
        key, item = entry
        if key is not None:
            if groups:
                groups[-1][1].extend(pending_whitespace)
            else:
                leading_whitespace.extend(pending_whitespace)
            pending_whitespace = []
            groups.append((key.key, [*pending_comments, entry]))
            pending_comments = []
            continue

        match item:
            case Whitespace():
                pending_whitespace.append(entry)
            case _:
                pending_comments.append(entry)

    sorted_groups = sorted(groups, key=lambda group: _sort_key(group[0], order))
    return [
        *leading_whitespace,
        *(entry for _, group in sorted_groups for entry in group),
        *pending_whitespace,
        *pending_comments,
    ]


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
