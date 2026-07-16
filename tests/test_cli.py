from pathlib import Path

from typer.testing import CliRunner

from toml_hierarchical_sort.cli import app


def test_help_when_called_without_arguments() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Sort TOML keys while preserving table hierarchy." in result.output


def test_check_when_input_requires_sorting(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_text("b = 1\na = 2\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(path), "--check"])

    assert result.exit_code == 1
    assert path.read_text(encoding="utf-8") == "b = 1\na = 2\n"


def test_in_place_when_input_requires_sorting(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_text("b = 1\na = 2\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(path), "--in-place"])

    assert result.exit_code == 0
    assert path.read_text(encoding="utf-8") == "a = 2\nb = 1\n"


def test_standard_output_when_alpha_order_is_selected(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_text('"omo-kit" = 1\nomo = 2\n', encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(path), "--order", "alpha"])

    assert result.exit_code == 0
    assert result.output == 'omo = 2\n"omo-kit" = 1\n'
    assert path.read_text(encoding="utf-8") == '"omo-kit" = 1\nomo = 2\n'


def test_command_when_invalid_toml_does_not_mutate_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.toml"
    source = "key = [\n"
    _ = path.write_text(source, encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(path), "--in-place"])

    assert result.exit_code != 0
    assert str(path) in result.output
    assert path.read_text(encoding="utf-8") == source
