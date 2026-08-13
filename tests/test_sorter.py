import pytest
import tomlkit
from tomlkit.exceptions import TOMLKitError

from toml_tidy import sorter
from toml_tidy.sorter import OrderMode, Scope, sort_toml

_sort_document = sorter._sort_document  # pyright: ignore[reportPrivateUsage]

_INVARIANT_DOCUMENTS = (
    'z = 1\na.b = 2\n"quoted key" = 3\n',
    "# zebra\n[z]\nb = 1\na = 2\n\n# apple\n[a]\nx = 3\n",
    "[[items]]\nz = 1\n[items.child]\nb = 2\na = 1\n[[items]]\ny = 2\n",
    "[a.y]\nz = 1\n[b]\nk = 2\n[a.x]\nq = 3\n",
)


def test_sort_document_keeps_document_key_lookup_consistent() -> None:
    document = tomlkit.parse("b = 1\na = 2\nc = 3\n")

    _sort_document(document, OrderMode.NATURAL)

    assert document["a"] == 2
    assert document["b"] == 1
    assert document["c"] == 3


@pytest.mark.parametrize("source", _INVARIANT_DOCUMENTS)
@pytest.mark.parametrize("order", OrderMode)
@pytest.mark.parametrize("scope", Scope)
@pytest.mark.parametrize("blank_lines", [False, True])
def test_sort_toml_preserves_semantics_and_is_idempotent(
    source: str, order: OrderMode, scope: Scope, *, blank_lines: bool
) -> None:
    result = sort_toml(source, order, scope, blank_lines=blank_lines)

    assert tomlkit.parse(result).unwrap() == tomlkit.parse(source).unwrap()
    assert sort_toml(result, order, scope, blank_lines=blank_lines) == result


def test_sort_document_keeps_nested_table_key_lookup_consistent() -> None:
    document = tomlkit.parse("[table]\nb = 1\na = 2\nc = 3\n")

    _sort_document(document, OrderMode.NATURAL)

    assert document["table"]["a"] == 2
    assert document["table"]["b"] == 1
    assert document["table"]["c"] == 3


def test_sort_document_keeps_super_table_key_lookup_consistent() -> None:
    # A leading comment hoisted into a merged super table's body (via
    # sibling-table splicing) is later deleted from that body when comment
    # attachment is restored -- a mutation to an already map-rebuilt
    # descendant that a per-container inline rebuild would miss.
    document = tomlkit.parse("[a.y]\nv = 1\n# mid\n[a.x]\nv = 3\n[b]\nv = 2\n")

    _sort_document(document, OrderMode.NATURAL)

    assert document["a"]["x"]["v"] == 3
    assert document["a"]["y"]["v"] == 1
    assert document["b"]["v"] == 2


def test_sort_document_keeps_array_of_tables_lookup_consistent() -> None:
    document = tomlkit.parse("[[items]]\nb = 1\na = 2\n[[items]]\nd = 3\nc = 4\n")

    _sort_document(document, OrderMode.NATURAL)

    assert document["items"][0]["a"] == 2
    assert document["items"][1]["c"] == 4
    assert tomlkit.parse(tomlkit.dumps(document)).unwrap() == document.unwrap()


def test_sort_toml_when_natural_order_is_selected() -> None:
    source = "item10 = 10\nitem2 = 2\nitem1 = 1\n"

    result = sort_toml(source)

    assert result == "item1 = 1\nitem2 = 2\nitem10 = 10\n"


def test_sort_toml_when_quoted_key_has_logical_order() -> None:
    source = '[plugins]\n"omo-kit" = 1\nomo = 2\n'

    result = sort_toml(source, order=OrderMode.ALPHA)

    assert result == '[plugins]\nomo = 2\n"omo-kit" = 1\n'


def test_sort_toml_when_quoted_table_key_has_logical_order() -> None:
    source = '[plugins]\n[plugins."omo-kit"]\nvalue = 1\n[plugins.omo]\nvalue = 2\n'

    result = sort_toml(source, order=OrderMode.ALPHA)

    assert result == (
        '[plugins]\n[plugins.omo]\nvalue = 2\n[plugins."omo-kit"]\nvalue = 1\n'
    )


def test_sort_toml_when_comments_and_child_table_exist() -> None:
    source = (
        "# package settings\nz = 1 # trailing\n\n# keep with a\na = 2\n"
        "[tool]\ny = 1\nx = 2\n[tool.child]\nb = 1\na = 2\n"
    )

    result = sort_toml(source)

    assert result == (
        "# keep with a\na = 2\n# package settings\nz = 1 # trailing\n\n"
        "[tool]\nx = 2\ny = 1\n[tool.child]\na = 2\nb = 1\n"
    )


def test_sort_toml_when_array_of_tables_exists() -> None:
    source = "[[items]]\nz = 1\na = 2\n\n[[items]]\ny = 3\nx = 4\n"

    result = sort_toml(source)

    assert result == "[[items]]\na = 2\nz = 1\n\n[[items]]\nx = 4\ny = 3\n"


def test_sort_toml_when_aot_sits_between_sibling_tables() -> None:
    source = "[z]\nk = 1\n[[items]]\nk = 2\n[a]\nk = 3\n"

    result = sort_toml(source)

    assert result == "[a]\nk = 3\n[[items]]\nk = 2\n[z]\nk = 1\n"
    assert sort_toml(result) == result
    assert tomlkit.parse(result).unwrap() == tomlkit.parse(source).unwrap()


def test_sort_toml_when_sibling_aots_are_out_of_order() -> None:
    source = "[[b]]\nk = 1\n[[a]]\nk = 2\n"

    result = sort_toml(source)

    assert result == "[[a]]\nk = 2\n[[b]]\nk = 1\n"
    assert sort_toml(result) == result
    assert tomlkit.parse(result).unwrap() == tomlkit.parse(source).unwrap()


def test_sort_toml_when_multi_element_aot_keeps_element_order() -> None:
    source = "[z]\nk = 1\n[[items]]\nk = 2\n[[items]]\nk = 3\n[a]\nk = 4\n"

    result = sort_toml(source)

    assert result == "[a]\nk = 4\n[[items]]\nk = 2\n[[items]]\nk = 3\n[z]\nk = 1\n"
    assert sort_toml(result) == result
    assert tomlkit.parse(result).unwrap() == tomlkit.parse(source).unwrap()


def test_sort_toml_when_aot_child_table_stays_attached() -> None:
    source = "[[items]]\nk = 1\n[items.sub]\ns = 1\n[a]\nk = 2\n"

    result = sort_toml(source)

    assert result == "[a]\nk = 2\n[[items]]\nk = 1\n[items.sub]\ns = 1\n"
    assert sort_toml(result) == result
    assert tomlkit.parse(result).unwrap() == tomlkit.parse(source).unwrap()


def test_sort_toml_when_comment_precedes_aot_header() -> None:
    source = "[z]\nk = 1\n# items note\n[[items]]\nk = 2\n[a]\nk = 3\n"

    result = sort_toml(source)

    assert result == "[a]\nk = 3\n# items note\n[[items]]\nk = 2\n[z]\nk = 1\n"
    assert sort_toml(result) == result
    assert tomlkit.parse(result).unwrap() == tomlkit.parse(source).unwrap()


def test_sort_toml_when_comment_after_aot_precedes_table_header() -> None:
    source = "[[b]]\nk = 1\n# a note\n[a]\nk = 2\n"

    result = sort_toml(source)

    assert result == "# a note\n[a]\nk = 2\n[[b]]\nk = 1\n"
    assert sort_toml(result) == result
    assert tomlkit.parse(result).unwrap() == tomlkit.parse(source).unwrap()


def test_sort_toml_when_dotted_keys_sort_with_sibling_keys() -> None:
    source = "b.z = 1\nb.a = 2\na = 3\n"

    result = sort_toml(source)

    assert result == "a = 3\nb.a = 2\nb.z = 1\n"
    assert tomlkit.parse(result).unwrap() == tomlkit.parse(source).unwrap()


def test_sort_toml_when_dotted_key_precedes_table() -> None:
    source = "m.x = 1\n[a]\nv = 1\n"

    result = sort_toml(source)

    assert result == "m.x = 1\n[a]\nv = 1\n"
    assert tomlkit.parse(result).unwrap() == tomlkit.parse(source).unwrap()


def test_sort_toml_when_nested_dotted_key_precedes_child_table() -> None:
    source = "[t]\nz.x = 1\n[t.a]\nv = 1\n"

    result = sort_toml(source)

    assert tomlkit.parse(result).unwrap() == tomlkit.parse(source).unwrap()


def test_sort_toml_when_dotted_key_mixes_with_direct_keys() -> None:
    source = "[t]\nz = 1\na.b = 2\n"

    result = sort_toml(source)

    assert result == "[t]\na.b = 2\nz = 1\n"
    assert tomlkit.parse(result).unwrap() == tomlkit.parse(source).unwrap()


def test_sort_toml_when_comments_precede_table_headers() -> None:
    source = "# about zebra\n[zebra]\nk = 1\n# about apple\n[apple]\nk = 2\n"

    result = sort_toml(source)

    assert result == "# about apple\n[apple]\nk = 2\n# about zebra\n[zebra]\nk = 1\n"
    assert sort_toml(result) == result
    assert tomlkit.parse(result).unwrap() == tomlkit.parse(source).unwrap()


def test_sort_toml_when_sorted_input_has_header_comment_and_blank_line() -> None:
    source = "# header comment\n\na = 1\nb = 2\n"

    result = sort_toml(source)

    assert result == source
    assert sort_toml(result) == result


def test_sort_toml_when_commented_tables_also_need_key_sorting() -> None:
    source = (
        "# about zebra\n[zebra]\nb = 1\na = 2\n# about apple\n[apple]\nd = 3\nc = 4\n"
    )

    result = sort_toml(source)

    assert result == (
        "# about apple\n[apple]\nc = 4\nd = 3\n# about zebra\n[zebra]\na = 2\nb = 1\n"
    )
    assert sort_toml(result) == result
    assert tomlkit.parse(result).unwrap() == tomlkit.parse(source).unwrap()


def test_sort_toml_when_last_table_ends_with_comment() -> None:
    source = "[b]\nk = 1\n[a]\nk = 2\n# trailing note\n"

    result = sort_toml(source)

    assert result == "[a]\nk = 2\n# trailing note\n[b]\nk = 1\n"
    assert sort_toml(result) == result


def test_sort_toml_when_sibling_subtables_are_out_of_order() -> None:
    source = "[a.y]\nk = 1\n[b]\nk = 2\n[a.x]\nk = 3\n"

    result = sort_toml(source)

    assert result == "[a.x]\nk = 3\n[a.y]\nk = 1\n[b]\nk = 2\n"
    assert sort_toml(result) == result
    assert tomlkit.parse(result).unwrap() == tomlkit.parse(source).unwrap()


def test_sort_toml_when_key_segment_tail_comment_precedes_table() -> None:
    source = "a = 1\n# about zzz\n[zzz]\nx = 1\n[aaa]\ny = 1\n"

    result = sort_toml(source)

    assert result == "a = 1\n[aaa]\ny = 1\n# about zzz\n[zzz]\nx = 1\n"
    assert sort_toml(result) == result
    assert tomlkit.parse(result).unwrap() == tomlkit.parse(source).unwrap()


def test_sort_toml_when_key_segment_tail_whitespace_stays_at_boundary() -> None:
    source = "a = 1\n\n[zzz]\nx = 1\n[aaa]\ny = 1\n"

    result = sort_toml(source)

    assert result == "a = 1\n\n[aaa]\ny = 1\n[zzz]\nx = 1\n"
    assert sort_toml(result) == result
    assert tomlkit.parse(result).unwrap() == tomlkit.parse(source).unwrap()


def test_sort_toml_when_key_segment_tail_comment_has_trailing_blank_line() -> None:
    source = "a = 1\n# about zzz\n\n[zzz]\nx = 1\n[aaa]\ny = 1\n"

    result = sort_toml(source)

    assert result == "a = 1\n[aaa]\ny = 1\n# about zzz\n\n[zzz]\nx = 1\n"
    assert sort_toml(result) == result
    assert tomlkit.parse(result).unwrap() == tomlkit.parse(source).unwrap()


def test_sort_toml_when_super_table_last_child_ends_with_comment() -> None:
    source = "[a.y]\nk = 1\n[b]\nk = 2\n[a.x]\nk = 3\n# tail\n"

    result = sort_toml(source)

    assert result == "[a.x]\nk = 3\n# tail\n[a.y]\nk = 1\n[b]\nk = 2\n"
    assert sort_toml(result) == result
    assert tomlkit.parse(result).unwrap() == tomlkit.parse(source).unwrap()


def test_sort_toml_when_comment_sits_at_super_table_merge_seam() -> None:
    source = "[a.y]\nk = 1\n[b]\nk = 2\n# above x\n[a.x]\nk = 3\n"

    result = sort_toml(source)

    assert result == "# above x\n[a.x]\nk = 3\n[a.y]\nk = 1\n[b]\nk = 2\n"
    assert sort_toml(result) == result
    assert tomlkit.parse(result).unwrap() == tomlkit.parse(source).unwrap()


def test_sort_toml_when_shuffled_documents_are_resorted() -> None:
    sources = [
        "[a.y]\nk = 1\n[b]\nk = 2\n[a.x]\nk = 3\n",
        "[a.b.z]\nk = 1\n[a.b.y]\nk = 2\n[c]\nk = 3\n[a.b.x]\nk = 4\n",
        "[t.b]\nk = 1\n[t]\nk = 2\n[t.a]\nk = 3\n",
        "[b]\nk = 1\n[a.x]\nk = 2\n[a]\nk = 3\n",
        "[a.x.q]\nk = 1\n[b]\nk = 2\n[a.x.p]\nk = 3\n[a.w]\nk = 4\n",
    ]

    for source in sources:
        result = sort_toml(source)

        assert sort_toml(result) == result, source
        assert tomlkit.parse(result).unwrap() == tomlkit.parse(source).unwrap()


def test_sort_toml_when_output_is_rendered() -> None:
    result = sort_toml("b = 1\na = 2\n")

    parsed = tomlkit.parse(result)

    assert parsed["a"] == 2


def test_sort_toml_when_source_lacks_trailing_newline() -> None:
    result = sort_toml("b = 1\na = 2")

    assert result == "a = 2\nb = 1\n"
    assert tomlkit.parse(result).unwrap() == tomlkit.parse("b = 1\na = 2").unwrap()


def test_sort_toml_when_sorted_source_lacks_trailing_newline_is_idempotent() -> None:
    result = sort_toml("a = 1\nb = 2")

    assert result == "a = 1\nb = 2\n"
    assert sort_toml(result) == result


def test_sort_toml_when_table_source_lacks_trailing_newline() -> None:
    source = "[b]\nx = 1\n[a]\ny = 2"

    result = sort_toml(source)

    parsed = tomlkit.parse(result)
    assert parsed.unwrap() == tomlkit.parse(source).unwrap()
    assert list(parsed.unwrap().keys()) == ["a", "b"]


def test_sort_toml_when_empty_source() -> None:
    assert sort_toml("") == ""


def test_sort_toml_when_split_aot_source_lacks_trailing_newline() -> None:
    # tomlkit coalesces the two [[b]] declarations into one AoT body entry
    # positioned at the first declaration, so the textually-last line
    # ("a = 1") actually lives inside the AoT's last element -- a container
    # the structural walk in `_sort_container` reaches, unlike a walk that
    # only follows the last *structural* body entry ([z]).
    source = "[[b]]\nk = 1\n[z]\nx = 1\n[[b]]\nz = 2\na = 1"

    result = sort_toml(source)

    reparsed = tomlkit.parse(result)
    assert reparsed.unwrap() == tomlkit.parse(source).unwrap()
    assert sort_toml(result) == result


def test_sort_toml_when_split_aot_source_has_sorted_keys_already() -> None:
    source = "[[b]]\nk = 1\n[z]\nx = 1\n[[b]]\na = 1\nz = 2"

    result = sort_toml(source)

    assert sort_toml(result) == result


def test_sort_toml_when_case_only_tie_spans_dotted_segments() -> None:
    source = "[A.z]\nk = 1\n[a.a]\nk = 2\n"
    expected = "[a.a]\nk = 2\n[A.z]\nk = 1\n"

    for order in (OrderMode.NATURAL, OrderMode.ALPHA):
        result = sort_toml(source, order)

        assert result == expected
        assert sort_toml(result, order) == result


def test_sort_toml_when_keys_differ_only_by_case() -> None:
    source = "a = 1\nA = 2\n"

    result = sort_toml(source, OrderMode.ALPHA)

    assert result == "A = 2\na = 1\n"
    assert sort_toml(result, OrderMode.ALPHA) == result


def test_sort_toml_when_numeric_key_exceeds_int_conversion_limit() -> None:
    big = "9" * 5000
    source = f"b = 1\n{big} = 2\n"

    result = sort_toml(source)

    assert result == f"{big} = 2\nb = 1\n"
    assert sort_toml(result) == result


def test_sort_toml_when_numeric_runs_have_leading_zeros() -> None:
    source = "item010 = 1\nitem9 = 2\nitem0 = 3\n"

    result = sort_toml(source)

    assert result == "item0 = 3\nitem9 = 2\nitem010 = 1\n"
    assert sort_toml(result) == result


def test_sort_toml_when_source_ends_with_lone_carriage_return() -> None:
    with pytest.raises(TOMLKitError):
        _ = sort_toml("a = 1\r")


def test_scope_tables_sorts_tables_but_not_keys() -> None:
    source = "b = 1\na = 2\n\n[z]\nx = 2\nw = 1\n\n[y]\nk = 1\n"

    result = sort_toml(source, scope=Scope.TABLES)

    assert result == "b = 1\na = 2\n\n[y]\nk = 1\n[z]\nx = 2\nw = 1\n\n"


def test_scope_keys_sorts_keys_but_not_tables() -> None:
    source = "b = 1\na = 2\n\n[z]\nx = 2\nw = 1\n\n[y]\nk = 1\n"

    result = sort_toml(source, scope=Scope.KEYS)

    assert result == "a = 2\nb = 1\n\n[z]\nw = 1\nx = 2\n\n[y]\nk = 1\n"


def test_scope_tables_is_idempotent() -> None:
    source = "b = 1\na = 2\n\n[z]\nx = 2\nw = 1\n\n[y]\nk = 1\n"

    once = sort_toml(source, scope=Scope.TABLES)

    assert sort_toml(once, scope=Scope.TABLES) == once


def test_first_pins_listed_tables_before_sorted_rest() -> None:
    source = "[b]\nn = 1\n[project]\nm = 1\n[a]\nk = 1\n"

    result = sort_toml(source, first=("project",))

    assert result == "[project]\nm = 1\n[a]\nk = 1\n[b]\nn = 1\n"


def test_first_preserves_listed_order_over_sort_order() -> None:
    source = "[a]\n[build-system]\n[project]\n"

    result = sort_toml(source, first=("project", "build-system"))

    assert result == "[project]\n[build-system]\n[a]\n"


def test_first_does_not_pin_nested_tables() -> None:
    source = "[z]\n[z.project]\nm = 1\n[z.a]\nk = 1\n"

    result = sort_toml(source, first=("project",))

    assert result == "[z]\n[z.a]\nk = 1\n[z.project]\nm = 1\n"


def test_scope_keys_split_aot_matches_tomlkit_roundtrip() -> None:
    # tomlkit itself coalesces split AoT declarations at parse time
    # (dumps(parse(src)) != src), so the scope-skipped path preserves
    # tomlkit's round-trip form, not the raw source bytes.
    source = "[[a]]\nx = 1\n[b]\ny = 1\n[[a]]\nz = 1\n"

    result = sort_toml(source, scope=Scope.KEYS)

    assert result == tomlkit.dumps(tomlkit.parse(source))
    assert sort_toml(result, scope=Scope.KEYS) == result


_BLANK_LINE_SOURCE = (
    "b = 1\n\n\na = 2\n"
    "[y]\nq = 1\n\n\n\n# note for x\n[x]\np = 1\n"
    "[[z]]\nr = 1\n\n\n[[z]]\nr = 2\n"
)


def test_blank_lines_normalizes_one_before_headers_and_none_elsewhere() -> None:
    result = sort_toml(_BLANK_LINE_SOURCE, blank_lines=True)

    assert result == (
        "a = 2\nb = 1\n\n"
        "# note for x\n[x]\np = 1\n\n"
        "[y]\nq = 1\n\n"
        "[[z]]\nr = 1\n\n"
        "[[z]]\nr = 2\n"
    )


def test_blank_lines_is_idempotent() -> None:
    result = sort_toml(_BLANK_LINE_SOURCE, blank_lines=True)

    assert sort_toml(result, blank_lines=True) == result


def test_blank_lines_is_off_by_default() -> None:
    result = sort_toml("[y]\np = 1\n\n\n[x]\nq = 1\n")

    assert result == "[x]\nq = 1\n[y]\np = 1\n\n\n"


def test_blank_lines_separates_tables_that_had_none() -> None:
    result = sort_toml("[x]\np = 1\n[x.b]\nq = 1\n[x.a]\nr = 1\n", blank_lines=True)

    assert result == "[x]\np = 1\n\n[x.a]\nr = 1\n\n[x.b]\nq = 1\n"


def test_blank_lines_never_indents_the_first_rendered_line() -> None:
    # A leading blank line lives in the document body, ahead of the super
    # table entry whose child header renders first.
    result = sort_toml("\n[a.b]\nx = 1\n", blank_lines=True)

    assert result == "[a.b]\nx = 1\n"


def test_blank_lines_strips_trailing_blank_lines() -> None:
    result = sort_toml("[x]\np = 1\n\n\n", blank_lines=True)

    assert result == "[x]\np = 1\n"


def test_blank_lines_leaves_multiline_string_values_untouched() -> None:
    source = 'b = """\nline\n\n\nline\n"""\na = 1\n'

    result = sort_toml(source, blank_lines=True)

    assert result == 'a = 1\nb = """\nline\n\n\nline\n"""\n'


def test_blank_lines_reuses_crlf_line_endings() -> None:
    # Mixed-ending sources reach the sorter unnormalized, so an inserted
    # blank line has to match the endings already in the file.
    source = "[x]\r\np = 1\r\n[y]\r\nq = 1\r\n"

    result = sort_toml(source, blank_lines=True)

    assert result == "[x]\r\np = 1\r\n\r\n[y]\r\nq = 1\r\n"


def test_blank_lines_does_not_double_before_a_super_table_sibling() -> None:
    # The separator after [a.z] belongs to that table's tail; the super table
    # [b] renders no header, so its first child must not add a second one.
    source = "[a.z]\nx = 1\n[b.c]\ny = 2\n"

    result = sort_toml(source, blank_lines=True)

    assert result == "[a.z]\nx = 1\n\n[b.c]\ny = 2\n"


def test_blank_lines_separates_a_super_table_that_follows_a_key() -> None:
    source = "a = 1\n[b.c]\ny = 2\n"

    result = sort_toml(source, blank_lines=True)

    assert result == "a = 1\n\n[b.c]\ny = 2\n"


def test_blank_lines_normalizes_segments_that_scope_leaves_unsorted() -> None:
    source = "[y]\nb = 1\n\n\na = 2\n\n\n[x]\np = 1\n"

    result = sort_toml(source, scope=Scope.KEYS, blank_lines=True)

    assert result == "[y]\na = 2\nb = 1\n\n[x]\np = 1\n"


def test_blank_lines_with_source_lacking_a_trailing_newline() -> None:
    result = sort_toml("[y]\nq = 1\n\n\n[x]\np = 1", blank_lines=True)

    assert result == "[x]\np = 1\n\n[y]\nq = 1\n"


def test_blank_lines_in_a_comment_only_document() -> None:
    result = sort_toml("# one\n\n\n# two\n", blank_lines=True)

    assert result == "# one\n# two\n"


def test_blank_lines_reuses_crlf_when_separating_empty_tables() -> None:
    # An empty table's body holds no trivia to copy an ending from, so it has
    # to come from the header that declared it.
    result = sort_toml("[x]\r\n[y]\r\n", blank_lines=True)

    assert result == "[x]\r\n\r\n[y]\r\n"


def test_blank_lines_reuses_crlf_when_separating_empty_aot_elements() -> None:
    result = sort_toml("[[z]]\r\n[[z]]\r\n", blank_lines=True)

    assert result == "[[z]]\r\n\r\n[[z]]\r\n"


def test_blank_lines_reuses_crlf_after_a_dotted_key() -> None:
    # A dotted key-value parses as a Table wrapper whose own trail is a bare
    # newline; the CRLF lives on the value nested inside it.
    result = sort_toml("z.y = 1\r\n[t]\r\nq = 1\r\n", blank_lines=True)

    assert result == "z.y = 1\r\n\r\n[t]\r\nq = 1\r\n"
