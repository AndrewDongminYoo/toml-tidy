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
    _sort_document(document, order)
    return tomlkit.dumps(document)


def _sort_document(container: Container, order: OrderMode) -> None:
    """Sort a container's tree, then restore key-to-index map consistency.

    ``_sort_container`` reorders and splices bodies throughout the tree
    (slice assignment, sibling-table merges, comment re-attachment), all of
    which bypass tomlkit's private ``_map`` bookkeeping. A mutation made
    while restoring one container's comment attachment can delete entries
    from an already-visited descendant's body, so rebuilding each
    container's map inline during the recursive sort is not safe: the
    rebuild has to run once, after every mutation in the tree is done.
    """
    _sort_container(container, order)
    _restore_maps(container)


def _restore_maps(container: Container) -> None:
    """Rebuild this container's map and recurse into every descendant table."""
    _rebuild_map(container)
    for _, item in container.body:
        match item:
            case Table():
                _restore_maps(item.value)
            case AoT():
                for table in item.body:
                    _restore_maps(table.value)
            case _:
                continue


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

    container.body[:] = _restore_comment_attachment(container.body)


def _rebuild_map(container: Container) -> None:
    """Rebuild a container's key-to-index map to match its current body.

    Sorting reorders ``body`` via direct slice assignment and splicing,
    which bypasses tomlkit's ``append``/``_raw_append`` bookkeeping that
    keeps the private ``_map`` in sync. Left stale, key lookups on the live
    document (e.g. ``doc['a']``) resolve through the old index and return
    whatever entry now occupies that slot. This mirrors ``_raw_append``'s
    map bookkeeping: a repeated key (out-of-order tables) collects a tuple
    of every index it occupies instead of a single int.
    """
    new_map: dict[Key, int | tuple[int, ...]] = {}
    for index, (key, _) in enumerate(container.body):
        if key is None:
            continue
        existing = new_map.get(key)
        if existing is None:
            new_map[key] = index
        elif isinstance(existing, tuple):
            new_map[key] = (*existing, index)
        else:
            new_map[key] = (existing, index)
    container._map = new_map  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001


def _sort_segments(entries: list[BodyEntry], order: OrderMode) -> list[BodyEntry]:
    """Sort direct keys and sibling tables without crossing their boundaries."""
    sorted_entries: list[BodyEntry] = []
    segment: list[BodyEntry] = []
    segment_kind: SegmentKind | None = None

    for entry in entries:
        key, item = entry
        match item:
            case Table() | AoT() if key is None or not key.is_dotted():
                if segment_kind == "key":
                    # The key segment's trailing comment run annotates the
                    # upcoming table header; carry it into the table segment
                    # so it travels as that table's leading-comment group.
                    tail = len(segment)
                    while tail and segment[tail - 1][0] is None:
                        tail -= 1
                    kept, carried = _split_before_first_comment(segment[tail:])
                    segment = [*segment[:tail], *kept]
                    sorted_entries.extend(_sort_segment(segment, order))
                    segment = carried
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

    groups = _merge_sibling_tables(groups)
    sorted_groups = sorted(
        groups,
        key=lambda group: tuple(_sort_key(segment, order) for segment in group[0]),
    )
    return [
        *leading,
        *(entry for _, group in sorted_groups for entry in group),
        *pending,
    ]


def _merge_sibling_tables(
    groups: list[tuple[tuple[str, ...], list[BodyEntry]]],
) -> list[tuple[tuple[str, ...], list[BodyEntry]]]:
    """Fold duplicate-key sibling table entries into a single body entry.

    ``[a.y] … [b] … [a.x]`` parses as two body entries keyed ``a``. Sorting
    them as separate entries is never idempotent: reparsing the output merges
    them into one super table whose children then sort together, so pass 2
    could interleave children across the pass-1 entry boundary. Merging here
    makes pass 1 produce the shape a reparse would.
    """
    merged: list[tuple[tuple[str, ...], list[BodyEntry]]] = []
    indexes: dict[str, int] = {}

    for path, entries in groups:
        key, item = entries[-1]
        if key is None or key.is_dotted() or not isinstance(item, Table):
            merged.append((path, entries))
            continue

        index = indexes.get(key.key)
        if index is not None:
            _, dest_item = merged[index][1][-1]
            if isinstance(dest_item, Table) and (
                dest_item.is_super_table() or item.is_super_table()
            ):
                if item.is_super_table():
                    _splice_super_table(dest_item, item, entries[:-1])
                else:
                    # The concrete table owns the header; fold the super in.
                    _splice_super_table(item, dest_item, merged[index][1][:-1])
                    merged[index] = (path, entries)
                surviving = merged[index][1]
                merged[index] = (_key_path(key, surviving[-1][1]), surviving)
                continue

        indexes[key.key] = len(merged)
        merged.append((path, entries))
    return merged


def _splice_super_table(dest: Table, src: Table, comments: list[BodyEntry]) -> None:
    """Move src's body (with its hoisted leading comments) under dest.

    Descends the shared single-child super-table chain so the splice happens
    at the first level where the chains diverge, keeping each comment
    directly above the concrete header it annotates. Duplicates created at a
    deeper level are folded when that container's segment is sorted.
    """
    while True:
        children = [entry for entry in src.value.body if entry[0] is not None]
        if len(children) != 1:
            break
        child_key, child_item = children[0]
        if not isinstance(child_item, Table) or not child_item.is_super_table():
            break
        existing = None
        for entry_key, entry_item in dest.value.body:
            if entry_key == child_key and isinstance(entry_item, Table):
                existing = entry_item
                break
        if existing is None or not existing.is_super_table():
            break
        dest, src = existing, child_item
    dest.value.body.extend([*comments, *src.value.body])


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

    A standalone comment written directly above a ``[table]`` or ``[[aot]]``
    header is parsed into the tail of the previous declaration's body, so
    reordering siblings would strand it. Hoisting it to the segment level lets
    it travel as the next declaration's leading-comment group. The last
    declaration keeps its tail; an AoT's tail lives in its last element.
    """
    table_indexes = [
        index
        for index, (key, item) in enumerate(entries)
        if isinstance(item, Table | AoT) and (key is None or not key.is_dotted())
    ]
    if not table_indexes:
        return entries

    hoisted: list[BodyEntry] = []
    for index, entry in enumerate(entries):
        hoisted.append(entry)
        if index in table_indexes[:-1]:
            match entry[1]:
                case Table() as table:
                    hoisted.extend(_pop_trailing_comment_run(table.value))
                case AoT() as aot if aot.body:
                    hoisted.extend(_pop_trailing_comment_run(aot.body[-1].value))
                case _:
                    pass
    return hoisted


def _restore_comment_attachment(entries: list[BodyEntry]) -> list[BodyEntry]:
    """Re-nest container-level comment runs the way a reparse would attach them.

    tomlkit renders a super table's implicit header as soon as a comment
    entry sits directly in its body, so a comment left at container level by
    hoisting or splicing would materialize an ``[a]`` line on the next pass.
    Sinking each trivia run that follows a table into that table's deepest
    tail, and lifting a comment run that leads a super table's body up to the
    parent, reproduces the shape the parser builds — keeping repeated sorts
    byte-identical.
    """
    result: list[BodyEntry] = []
    target: Container | None = None

    for entry in entries:
        key, item = entry
        if key is None and target is not None:
            target.body.append(entry)
            continue
        if isinstance(item, Table) and item.is_super_table():
            body = item.value.body
            end = 0
            while end < len(body) and body[end][0] is None:
                end += 1
            whitespace, comments = _split_before_first_comment(body[:end])
            if comments:
                del body[len(whitespace) : end]
                if target is not None:
                    target.body.extend(comments)
                else:
                    result.extend(comments)
        result.append(entry)
        match item:
            case Table() if key is None or not key.is_dotted():
                target = _trailing_container(item.value)
            case AoT() if item.body:
                target = _trailing_container(item.body[-1].value)
            case _:
                target = None
    return result


def _trailing_container(container: Container) -> Container | None:
    """Return the deepest container a reparse would attach trailing trivia to."""
    while container.body:
        key, item = container.body[-1]
        match item:
            case Table() if key is None or not key.is_dotted():
                container = item.value
            case AoT():
                if not item.body:
                    return None
                container = item.body[-1].value
            case _:
                break
    return container


def _pop_trailing_comment_run(container: Container) -> list[BodyEntry]:
    """Remove and return the trailing comment run of the deepest last body.

    The run starts at the first comment of the body's trailing trivia; pure
    whitespace before it stays at the table boundary, while whitespace
    interleaved with the comments moves along in its original order.
    """
    target = _trailing_container(container)
    if target is None:
        return []

    body = target.body
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
    """Return a key's logical segments, expanding dotted keys and super tables.

    ``[a.y]`` at root parses as a super-table entry keyed ``a``; two such
    siblings would otherwise compare equal, keep their original order, and
    only sort after a reparse merges them — breaking idempotence. Walking
    single-child super tables recovers the effective header path instead.
    """
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
