from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from toml_hierarchical_sort.cli import app

if TYPE_CHECKING:
    from typing import IO

    from _typeshed import OpenTextMode

_INVALID_UTF8 = b"a = \xff\xfe\n"


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

    assert result.exit_code == 2
    assert result.stderr.startswith(f"{path}: ")
    assert path.read_text(encoding="utf-8") == source


def test_non_utf8_file_exits_with_error_code(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_bytes(_INVALID_UTF8)
    runner = CliRunner()

    result = runner.invoke(app, [str(path)])

    assert result.exit_code == 2
    assert str(path) in result.stderr
    assert "Traceback" not in result.output


def test_in_place_on_unwritable_file_exits_with_error_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    source = "b = 1\na = 2\n"
    _ = path.write_text(source, encoding="utf-8")
    original_open = Path.open

    def deny_write(
        self: Path,
        mode: "OpenTextMode" = "r",
        *,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> "IO[str]":
        # chmod(0o444) is bypassed by root (Docker/CI), so simulate the
        # write failure deterministically instead.
        if "w" in mode:
            raise PermissionError(13, "Permission denied", str(self))
        return original_open(
            self, mode, encoding=encoding, errors=errors, newline=newline
        )

    monkeypatch.setattr(Path, "open", deny_write)
    runner = CliRunner()

    result = runner.invoke(app, [str(path), "--in-place"])

    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert path.read_text(encoding="utf-8") == source


def test_deeply_nested_header_exits_with_error_code(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    header = "[" + "a." * 1500 + "a]"
    _ = path.write_text(header + "\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(path)])

    assert result.exit_code == 2
    assert "Traceback" not in result.output


def test_in_place_preserves_crlf_line_endings(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_bytes(b"b = 1\r\na = 2\r\n")
    runner = CliRunner()

    result = runner.invoke(app, [str(path), "--in-place"])

    assert result.exit_code == 0
    content = path.read_bytes()
    assert content == b"a = 2\r\nb = 1\r\n"
    assert b"\n" not in content.replace(b"\r\n", b"")


def test_check_on_sorted_crlf_file_leaves_bytes_untouched(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    original = b"a = 2\r\nb = 1\r\n"
    _ = path.write_bytes(original)
    runner = CliRunner()

    result = runner.invoke(app, [str(path), "--check"])

    assert result.exit_code == 0
    assert path.read_bytes() == original


def test_in_place_preserves_mixed_line_endings(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_bytes(b"b = 1\r\na = 2\n")
    runner = CliRunner()

    result = runner.invoke(app, [str(path), "--in-place"])

    assert result.exit_code == 0
    content = path.read_bytes()
    # Each line keeps its own original ending after re-sorting.
    assert content == b"a = 2\nb = 1\r\n"

    recheck = runner.invoke(app, [str(path), "--check"])
    assert recheck.exit_code == 0


def test_in_place_on_sorted_mixed_endings_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    original = b"a = 2\nb = 1\r\n"
    _ = path.write_bytes(original)
    runner = CliRunner()

    result = runner.invoke(app, [str(path), "--in-place"])

    assert result.exit_code == 0
    assert path.read_bytes() == original


def test_stdout_preserves_crlf_line_endings(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_bytes(b"b = 1\r\na = 2\r\n")
    runner = CliRunner()

    result = runner.invoke(app, [str(path)])

    assert result.exit_code == 0
    # Bytes-level assert: text-mode newline translation must not double the CR.
    assert result.stdout_bytes == b"a = 2\r\nb = 1\r\n"
