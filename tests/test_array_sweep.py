"""Permutation sweep over array spacing and ``line_width`` expansion.

Unit tests assert chosen shapes; this asserts properties over the product of
block shapes, option combinations, and line endings. The properties are read
off the dumped text rather than the AST, so the width invariant is checked by
something other than the code that measures it.

Re-run this on any ``tomlkit`` bump inside the pinned ``>=0.15.0,<0.16``
range: the sorter mutates private array internals, so a patch release can
change the parse shape without changing any public API.
"""

import itertools
import re
import tomllib

import pytest

from toml_tidy.sorter import OrderMode, Scope, sort_toml

_BLOCKS = [
    "a = [1,2,3]\n",
    'b = [ "xxxxxxxx", "yyyyyyyy", "zzzzzzzz" ]\n',
    "c = []\n",
    "d = [ 1, 2, ]\n",
    "e = [[1,2],[3,4]]\n",
    "f = [\n    1,\n    2,\n]\n",
    "g = [1,2]  # a trailing comment that is quite long indeed\n",
    'h.i = [ "aaaaaaaa", "bbbbbbbb" ]\n',
    '[t]\nj = [9,8,7]\nk = [ "qqqqqqqqqqqq", "wwwwwwwwwwww" ]\n',
    '[[u]]\nl = [3,2,1]\n[[u]]\nm = [ "eeeeeeee", "rrrrrrrr" ]\n',
    'n = """\nnot = [1,2] a real array\n[ x ,y ]\n"""\n',
    '[v.w]\no = [5,4]\np = [ "yyyyyyyyyy", "uuuuuuuuuu" ]\n',
]
_TRIPLE = re.compile(r'"""(?:.|\n)*?"""|\'\'\'(?:.|\n)*?\'\'\'', re.MULTILINE)
# A whole-line array value carrying no inline table and no trailing comment.
_ARRAY_LINE = re.compile(r"^\s*[\w.\"']+(?:\.[\w.\"']+)* = \[[^{#]*\]$")
_QUOTED = re.compile(r'"[^"]*"|\'[^\']*\'')


def _array_lines(text: str) -> list[str]:
    """Return whole-line arrays, with multi-line string spans blanked out.

    A blank line inside a multi-line string is part of the value, so its
    content must never reach the layout assertions.
    """
    masked = _TRIPLE.sub(lambda match: "\n" * match.group(0).count("\n"), text)
    return [
        stripped
        for line in masked.split("\n")
        if _ARRAY_LINE.match(stripped := line.rstrip("\r"))
    ]


def _boundary_widths(source: str) -> list[int]:
    """Return widths landing on each array line's own length, and either side.

    Fixed widths never sit on a boundary, so an off-by-one in the measurement
    survives them; these make every rendered array line a boundary case.
    """
    lengths = {len(line) for line in _array_lines(sort_toml(source))}
    return sorted({w for n in lengths for w in (n - 1, n, n + 1) if w > 0})


def _assert_line_endings(source: str, result: str) -> None:
    """Assert expansion did not introduce an ending the source never had.

    The array-line assertions cannot see this: an expanded array matches no
    whole-line pattern, so a bare LF spliced into a CRLF document passes
    every other property here. That is how it shipped past a CRLF sweep.
    """
    if "\r\n" in source and "\n" not in source.replace("\r\n", ""):
        assert "\n" not in result.replace("\r\n", ""), "bare LF in a CRLF document"


def _assert_layout(source: str, result: str, width: int | None) -> None:
    assert tomllib.loads(result) == tomllib.loads(source)
    _assert_line_endings(source, result)
    for line in _array_lines(result):
        body = line[line.index("=") + 1 :].strip()
        if body == "[]":
            # An empty array has nothing to expand, so it may exceed the width.
            continue
        assert body.startswith("[ ")
        assert body.endswith(" ]")
        unquoted = _QUOTED.sub('""', body)
        assert " ," not in unquoted
        assert not re.search(r",(?! |\])", unquoted)
        if width is not None:
            assert len(line) <= width


@pytest.mark.parametrize("block", _BLOCKS)
def test_array_layout_holds_across_option_permutations(block: str) -> None:
    widths: list[int | None] = [None, 20, *_boundary_widths(block)]
    for order, scope, blank, width, crlf in itertools.product(
        OrderMode, Scope, (False, True), widths, (False, True)
    ):
        source = block.replace("\n", "\r\n") if crlf else block
        baseline = sort_toml(source, order, scope, blank_lines=blank)
        result = sort_toml(source, order, scope, blank_lines=blank, line_width=width)
        _assert_layout(source, result, width)
        # A width no array line reaches must expand nothing; without this an
        # over-measurement expands eagerly and every width assertion still
        # passes, because those only catch lines that stayed too wide.
        widest = max((len(line) for line in _array_lines(baseline)), default=0)
        if width is None or width >= widest:
            assert result == baseline, "expanded below the width"

        again = sort_toml(result, order, scope, blank_lines=blank, line_width=width)
        assert again == result, "not idempotent"
