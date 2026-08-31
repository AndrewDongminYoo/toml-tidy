"""Sort TOML key entries while retaining parsed formatting items."""

import re
from enum import StrEnum
from typing import Literal, cast

import tomlkit
from tomlkit.container import Container
from tomlkit.items import AoT, Array, Comment, InlineTable, Item, Key, Table, Whitespace

type BodyEntry = tuple[Key | None, Item]
type SegmentKind = Literal["key", "table"]
type SortKey = tuple[tuple[int, str | tuple[int, str]], ...]

_NATURAL_PARTS = re.compile(r"(\d+)")


class OrderMode(StrEnum):
    """Supported direct-key ordering modes."""

    NATURAL = "natural"
    ALPHA = "alpha"


class Scope(StrEnum):
    """Which segment kinds get sorted."""

    ALL = "all"
    TABLES = "tables"
    KEYS = "keys"


def sort_toml(  # noqa: PLR0913
    source: str,
    order: OrderMode = OrderMode.NATURAL,
    scope: Scope = Scope.ALL,
    first: tuple[str, ...] = (),
    *,
    blank_lines: bool = False,
    line_width: int | None = None,
) -> str:
    """Return source with direct keys sorted recursively.

    ``first`` pins top-level entries whose leading key segment matches a
    listed name, in listed order, ahead of their sorted siblings; it never
    applies inside nested tables. ``blank_lines`` additionally normalizes
    blank lines to exactly one before every table header and none elsewhere.
    ``line_width`` expands single-line arrays whose rendered line is wider
    than the given column count; ``None`` leaves every array's layout alone.
    """
    # A trailing lone "\r" is invalid TOML; appending "\n" would turn it into
    # a valid CRLF line instead of letting the parser reject it.
    if source and not source.endswith(("\n", "\r")):
        source += "\n"
    document = tomlkit.parse(source)
    _sort_document(
        document, order, scope, first, blank_lines=blank_lines, line_width=line_width
    )
    return tomlkit.dumps(document)


def _sort_document(  # noqa: PLR0913
    container: Container,
    order: OrderMode,
    scope: Scope = Scope.ALL,
    first: tuple[str, ...] = (),
    *,
    blank_lines: bool = False,
    line_width: int | None = None,
) -> None:
    """Sort a container's tree, then restore key-to-index map consistency.

    ``_sort_container`` reorders and splices bodies throughout the tree
    (slice assignment, sibling-table merges, comment re-attachment), all of
    which bypass tomlkit's private ``_map`` bookkeeping. A mutation made
    while restoring one container's comment attachment can delete entries
    from an already-visited descendant's body, so rebuilding each
    container's map inline during the recursive sort is not safe: the
    rebuild has to run once, after every mutation in the tree is done.
    Blank-line normalization also adds and drops body entries, so it has to
    run before that rebuild for the same reason.

    Array expansion runs after spacing normalization so the measured width
    is the width the normalized array actually renders at.
    """
    _sort_container(container, order, scope, first)
    _normalize_inline_array_whitespace(container)
    if line_width is not None:
        _expand_wide_arrays(container, line_width)
    if blank_lines:
        _normalize_blank_lines(container, separate_first=False, followed=False)
    _restore_maps(container)


def _normalize_inline_array_whitespace(container: Container) -> None:
    """Use one edge space in every non-empty single-line array."""
    for _, item in container.body:
        match item:
            case Array() as array:
                for value in cast("list[Item]", array):
                    _normalize_inline_array_item(value)
                _normalize_array_spacing(array)
            case InlineTable():
                _normalize_inline_array_whitespace(item.value)
            case Table():
                _normalize_inline_array_whitespace(item.value)
            case AoT():
                for table in item.body:
                    _normalize_inline_array_whitespace(table.value)
            case _:
                continue


def _normalize_inline_array_item(item: Item) -> None:
    """Normalize an array nested inside another array."""
    match item:
        case Array() as array:
            for value in cast("list[Item]", array):
                _normalize_inline_array_item(value)
            _normalize_array_spacing(array)
        case InlineTable():
            _normalize_inline_array_whitespace(item.value)
        case _:
            pass


def _normalize_array_spacing(array: Array) -> None:
    """Set edge and separator spaces without changing an array's contents.

    Whitespace between an array's values carries the commas, so the run is
    rebuilt rather than edited: every separator renders as ``", "`` and each
    edge as one space. A trailing comma keeps its position at the end. Only
    single-line arrays qualify, which is also what keeps this safe against
    comments: a ``#`` runs to end of line, so a comment inside an array
    always forces the closing bracket onto another line.
    """
    if not array or "\n" in array.as_string():
        return

    rebuilt: list[Item] = [Whitespace(" ")]
    separator_pending = False
    items = array._iter_items()  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    for item in items:
        if isinstance(item, Whitespace):
            separator_pending = separator_pending or "," in item.s
            continue
        if separator_pending:
            rebuilt.extend((Whitespace(","), Whitespace(" ")))
            separator_pending = False
        rebuilt.append(item)
    if separator_pending:
        rebuilt.append(Whitespace(","))
    rebuilt.append(Whitespace(" "))
    array._value = (  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
        array._group_values(  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
            rebuilt,
        )
    )
    _ = array._reindex()  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001


def _expand_wide_arrays(
    container: Container, line_width: int, prefix: str = ""
) -> None:
    """Expand every single-line array whose own line exceeds ``line_width``.

    Only an array that is the direct value of a key gets measured, because
    only that array occupies a line of its own. Arrays nested inside another
    array or an inline table share their parent's line and are left alone,
    and an array already spanning several lines is never rejoined, so a
    single expanding pass reaches a fixed point. An empty array is skipped
    because expanding it cannot make its line any shorter.

    ``prefix`` carries the leading segments of a dotted key. A dotted
    key-value parses as a ``Table`` wrapper that renders no header of its
    own, so ``a.b = [...]`` reaches this walk as key ``b`` one level down and
    would otherwise be measured two columns short.
    """
    for key, item in container.body:
        match item:
            case Array() as array if key is not None and array:
                if _rendered_width(prefix, key, array) > line_width:
                    _ = array.multiline(multiline=True)
            case Table() if key is not None and key.is_dotted():
                _expand_wide_arrays(
                    item.value, line_width, f"{prefix}{key.as_string()}."
                )
            case Table():
                _expand_wide_arrays(item.value, line_width)
            case AoT():
                for table in item.body:
                    _expand_wide_arrays(table.value, line_width)
            case _:
                continue


def _rendered_width(prefix: str, key: Key, array: Array) -> int:
    """Measure an array's line, excluding any comment trailing the value.

    The trailing comment is excluded deliberately: expanding the array moves
    the comment but cannot shorten it, so counting it would expand arrays
    that the expansion could not bring under the limit. The indent sits on
    the innermost item even for a dotted key, so it leads the whole line.
    """
    rendered = array.as_string()
    if "\n" in rendered:
        return 0
    return len(array.trivia.indent + prefix + key.as_string() + key.sep + rendered)


def _normalize_blank_lines(
    container: Container,
    *,
    separate_first: bool,
    followed: bool,
    linesep: str = "\n",
) -> None:
    """Rewrite trivia runs to one blank line before a table header, none elsewhere.

    Each boundary's blank line has exactly one owner, so no boundary can end
    up doubled or empty. A separator before a table header belongs to this
    body when the preceding entry is a key here; otherwise the preceding
    declaration's own deepest body tail supplies it -- which is where tomlkit
    parses it, and what ``followed`` reports since the local body cannot see
    its successors. ``separate_first`` covers the one boundary a body's first
    entry can own: the header line this container renders directly above it. A
    super table renders no header and the document has nothing above its first
    line, so neither owns that boundary.

    Comments keep their order and attachment; only ``Whitespace`` entries are
    rewritten, so blank lines inside multi-line string values (parsed as part
    of the value, not as trivia) are untouched.

    ``linesep`` is the ending to write when a boundary has no trivia of its own
    to copy: an empty table's body holds nothing, so the ending of the header
    that declared it is the only nearby evidence.
    """
    body = container.body
    last = max(
        (index for index, (key, _) in enumerate(body) if key is not None), default=-1
    )
    result: list[BodyEntry] = []
    run: list[BodyEntry] = []
    after_declaration = False

    for index, entry in enumerate(body):
        key, item = entry
        if key is None:
            run.append(entry)
            continue
        # A dotted key parses as a Table but renders as one key line, so it
        # separates like a key, not like a header.
        declaration = isinstance(item, Table | AoT) and not key.is_dotted()
        separate = (
            declaration and not after_declaration and (bool(result) or separate_first)
        )
        result.extend(
            _rebuild_run(run, separate=separate, previous=result, fallback=linesep)
        )
        run = []
        result.append(entry)
        if declaration:
            _normalize_children(
                item, followed=index < last or followed, linesep=linesep
            )
        after_declaration = declaration

    result.extend(
        _rebuild_run(
            run,
            separate=followed and not after_declaration,
            previous=result,
            fallback=linesep,
        )
    )
    body[:] = result


def _normalize_children(item: Item, *, followed: bool, linesep: str) -> None:
    """Normalize the bodies a declaration owns, tracking rendered neighbours."""
    match item:
        case Table():
            _normalize_blank_lines(
                item.value,
                separate_first=not item.is_super_table(),
                followed=followed,
                linesep=_header_linesep(item, linesep),
            )
        case AoT():
            for index, table in enumerate(item.body):
                _normalize_blank_lines(
                    table.value,
                    separate_first=True,
                    followed=index < len(item.body) - 1 or followed,
                    linesep=_header_linesep(table, linesep),
                )
        case _:
            pass


def _header_linesep(item: Item, fallback: str) -> str:
    """Return the line ending of a declaration's own header line."""
    trail = item.trivia.trail
    if trail.endswith("\r\n"):
        return "\r\n"
    if trail.endswith("\n"):
        return "\n"
    return fallback


def _rebuild_run(
    run: list[BodyEntry], *, separate: bool, previous: list[BodyEntry], fallback: str
) -> list[BodyEntry]:
    """Drop a trivia run's whitespace, optionally re-adding one leading blank line."""
    comments = [entry for entry in run if not isinstance(entry[1], Whitespace)]
    if not separate:
        return comments
    return [(None, Whitespace(_run_linesep(run, previous, fallback))), *comments]


def _run_linesep(run: list[BodyEntry], previous: list[BodyEntry], fallback: str) -> str:
    """Reuse a neighbouring line ending so a CRLF source keeps CRLF blank lines."""
    endings = [item.as_string() for _, item in run if isinstance(item, Whitespace)]
    if previous:
        endings.append(_entry_trail(previous[-1][1]))
    if any("\r\n" in ending for ending in endings):
        return "\r\n"
    # An empty body offers no evidence at all; fall back to the enclosing
    # declaration's header ending rather than assuming LF.
    return "\n" if any(ending.endswith("\n") for ending in endings) else fallback


def _entry_trail(item: Item) -> str:
    """Return the trail of the last line an entry renders.

    A dotted key-value parses as a ``Table`` wrapper whose own trail is a bare
    newline, while the ending that actually renders belongs to the value nested
    inside it -- reading the wrapper would report LF for a CRLF line.
    """
    if isinstance(item, Whitespace):
        return item.as_string()
    if isinstance(item, Table):
        container = _trailing_container(item.value)
        if container is not None and container.body:
            return _entry_trail(container.body[-1][1])
    return item.trivia.trail


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


def _sort_container(
    container: Container,
    order: OrderMode,
    scope: Scope,
    first: tuple[str, ...],
) -> None:
    """Sort direct key segments and then visit descendant table containers."""
    container.body[:] = _sort_segments(container.body, order, scope, first)

    for _, item in container.body:
        match item:
            case Table():
                _sort_container(item.value, order, scope, ())
            case AoT():
                for table in item.body:
                    _sort_container(table.value, order, scope, ())
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


def _sort_segments(
    entries: list[BodyEntry],
    order: OrderMode,
    scope: Scope,
    first: tuple[str, ...],
) -> list[BodyEntry]:
    """Sort direct keys and sibling tables without crossing their boundaries."""
    sorted_entries: list[BodyEntry] = []
    segment: list[BodyEntry] = []
    segment_kind: SegmentKind | None = None

    def flush(segment: list[BodyEntry], kind: SegmentKind | None) -> list[BodyEntry]:
        skip = (kind == "key" and scope is Scope.TABLES) or (
            kind == "table" and scope is Scope.KEYS
        )
        return segment if skip else _sort_segment(segment, order, first)

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
                    sorted_entries.extend(flush(segment, segment_kind))
                    segment = carried
                segment.append(entry)
                segment_kind = "table"
            case _:
                if key is not None and segment_kind == "table":
                    sorted_entries.extend(flush(segment, segment_kind))
                    segment = []
                segment.append(entry)
                if key is not None:
                    segment_kind = "key"

    sorted_entries.extend(flush(segment, segment_kind))
    return sorted_entries


def _sort_segment(
    entries: list[BodyEntry],
    order: OrderMode,
    first: tuple[str, ...] = (),
) -> list[BodyEntry]:
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
    pin_rank = {name: rank for rank, name in enumerate(first)}
    sorted_groups = sorted(
        groups,
        key=lambda group: (
            pin_rank.get(group[0][0], len(first)),
            _path_sort_key(group[0], order),
        ),
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


def _path_sort_key(
    path: tuple[str, ...], order: OrderMode
) -> tuple[tuple[SortKey, ...], tuple[str, ...]]:
    """Compare normalized segments first; raw spelling breaks full-path ties only.

    Folding the raw key into each segment would let a case-only difference in
    an early segment (``A`` vs ``a``) decide the order before later segments
    are compared, misplacing ``A.z`` after ``a.a``.
    """
    return tuple(_sort_key(segment, order) for segment in path), path


def _sort_key(key: str, order: OrderMode) -> SortKey:
    """Produce a comparable key from a TOML key's parsed logical value."""
    match order:
        case OrderMode.NATURAL:
            return _natural_key(key)
        case OrderMode.ALPHA:
            return ((0, key.casefold()),)


def _natural_key(key: str) -> SortKey:
    """Return case-insensitive text and numeric runs in natural comparison order."""
    parts: list[tuple[int, str | tuple[int, str]]] = []

    for part in _NATURAL_PARTS.split(key):
        if part.isdecimal():
            # (digit count, digits) compares numerically without materializing
            # an int, which would raise past sys.get_int_max_str_digits().
            digits = part.lstrip("0")
            parts.append((1, (len(digits), digits)))
        else:
            parts.append((0, part.casefold()))

    return tuple(parts)
