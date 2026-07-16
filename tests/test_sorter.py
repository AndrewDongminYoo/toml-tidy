import tomlkit

from toml_hierarchical_sort.sorter import OrderMode, sort_toml


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
