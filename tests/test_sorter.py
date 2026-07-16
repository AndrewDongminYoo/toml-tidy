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


def test_sort_toml_when_output_is_rendered() -> None:
    result = sort_toml("b = 1\na = 2\n")

    parsed = tomlkit.parse(result)

    assert parsed["a"] == 2
