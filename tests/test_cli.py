from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from toml_tidy.cli import app

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


def test_in_place_accepts_multiple_paths(tmp_path: Path) -> None:
    first = tmp_path / "a.toml"
    _ = first.write_text("b = 1\na = 2\n", encoding="utf-8")
    second = tmp_path / "b.toml"
    _ = second.write_text("d = 1\nc = 2\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(first), str(second), "--in-place"])

    assert result.exit_code == 0
    assert first.read_text(encoding="utf-8") == "a = 2\nb = 1\n"
    assert second.read_text(encoding="utf-8") == "c = 2\nd = 1\n"


def test_check_multiple_paths_flags_any_unsorted_file(tmp_path: Path) -> None:
    sorted_file = tmp_path / "a.toml"
    _ = sorted_file.write_text("a = 1\nb = 2\n", encoding="utf-8")
    unsorted_file = tmp_path / "b.toml"
    _ = unsorted_file.write_text("d = 1\nc = 2\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(sorted_file), str(unsorted_file), "--check"])

    assert result.exit_code == 1


def test_error_in_one_file_still_processes_remaining_files(tmp_path: Path) -> None:
    broken = tmp_path / "broken.toml"
    _ = broken.write_text("key = [\n", encoding="utf-8")
    fixable = tmp_path / "fixable.toml"
    _ = fixable.write_text("b = 1\na = 2\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(broken), str(fixable), "--in-place"])

    assert result.exit_code == 2
    assert result.stderr.startswith(f"{broken}: ")
    assert fixable.read_text(encoding="utf-8") == "a = 2\nb = 1\n"


def test_missing_file_does_not_stop_remaining_files(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    fixable = tmp_path / "fixable.toml"
    _ = fixable.write_text("b = 1\na = 2\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(missing), str(fixable), "--in-place"])

    assert result.exit_code == 2
    assert result.stderr.startswith(f"{missing}: ")
    assert fixable.read_text(encoding="utf-8") == "a = 2\nb = 1\n"


def test_symlink_loop_during_config_lookup_does_not_stop_remaining_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    looped = tmp_path / "looped.toml"
    _ = looped.write_text("a = 1\n", encoding="utf-8")
    fixable = tmp_path / "fixable.toml"
    _ = fixable.write_text("b = 1\na = 2\n", encoding="utf-8")
    original_resolve = Path.resolve

    def raise_symlink_loop(self: Path, strict: bool = False) -> Path:
        # Python 3.12's Path.resolve() raises RuntimeError on cyclic
        # symlinks (3.13 resolves as far as possible instead), so simulate
        # it deterministically on every version.
        if self.name == "looped.toml":
            msg = f"Symlink loop from {str(self)!r}"
            raise RuntimeError(msg)
        return original_resolve(self, strict)

    monkeypatch.setattr(Path, "resolve", raise_symlink_loop)
    runner = CliRunner()

    result = runner.invoke(app, [str(looped), str(fixable), "--in-place"])

    assert result.exit_code == 2
    assert result.stderr.startswith(f"{looped}: ")
    assert "Traceback" not in result.output
    assert fixable.read_text(encoding="utf-8") == "a = 2\nb = 1\n"


def test_scope_option_keys_leaves_tables_unsorted(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_text("[z]\nb = 1\na = 2\n[y]\nk = 1\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(path), "--scope", "keys"])

    assert result.exit_code == 0
    assert result.output == "[z]\na = 2\nb = 1\n[y]\nk = 1\n"


def test_config_order_is_read_from_pyproject(tmp_path: Path) -> None:
    _ = (tmp_path / "pyproject.toml").write_text(
        '[tool.toml-tidy]\norder = "alpha"\n', encoding="utf-8"
    )
    path = tmp_path / "config.toml"
    _ = path.write_text("item2 = 1\nitem10 = 2\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(path)])

    assert result.exit_code == 0
    assert result.output == "item10 = 2\nitem2 = 1\n"


def test_cli_order_flag_overrides_config(tmp_path: Path) -> None:
    _ = (tmp_path / "pyproject.toml").write_text(
        '[tool.toml-tidy]\norder = "alpha"\n', encoding="utf-8"
    )
    path = tmp_path / "config.toml"
    _ = path.write_text("item10 = 2\nitem2 = 1\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(path), "--order", "natural"])

    assert result.exit_code == 0
    assert result.output == "item2 = 1\nitem10 = 2\n"


def test_config_scope_is_read_from_pyproject(tmp_path: Path) -> None:
    _ = (tmp_path / "pyproject.toml").write_text(
        '[tool.toml-tidy]\nscope = "tables"\n', encoding="utf-8"
    )
    path = tmp_path / "config.toml"
    _ = path.write_text("b = 1\na = 2\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(path), "--check"])

    assert result.exit_code == 0


def test_config_first_pins_top_level_tables(tmp_path: Path) -> None:
    _ = (tmp_path / "pyproject.toml").write_text(
        '[tool.toml-tidy]\nfirst = ["project"]\n', encoding="utf-8"
    )
    path = tmp_path / "config.toml"
    _ = path.write_text("[b]\n[project]\n[a]\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(path)])

    assert result.exit_code == 0
    assert result.output == "[project]\n[a]\n[b]\n"


def test_non_table_config_section_exits_with_error(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    _ = pyproject.write_text('[tool]\ntoml-tidy = "alpha"\n', encoding="utf-8")
    path = tmp_path / "config.toml"
    _ = path.write_text("a = 1\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(path)])

    assert result.exit_code == 2
    assert result.stderr.startswith(f"{pyproject}: ")
    assert "Traceback" not in result.output


def test_multiple_paths_to_stdout_is_a_usage_error(tmp_path: Path) -> None:
    first = tmp_path / "a.toml"
    _ = first.write_text("a = 1\n", encoding="utf-8")
    second = tmp_path / "b.toml"
    _ = second.write_text("b = 1\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(first), str(second)])

    assert result.exit_code == 2
    assert "--in-place or --check" in result.stderr


def test_invalid_utf8_config_exits_with_error(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    _ = pyproject.write_bytes(b"[tool]\n" + _INVALID_UTF8)
    path = tmp_path / "config.toml"
    _ = path.write_text("a = 1\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(path)])

    assert result.exit_code == 2
    assert result.stderr.startswith(f"{pyproject}: ")
    assert "Traceback" not in result.output


def test_invalid_config_value_exits_with_error(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    _ = pyproject.write_text('[tool.toml-tidy]\norder = "bogus"\n', encoding="utf-8")
    path = tmp_path / "config.toml"
    _ = path.write_text("a = 1\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(path)])

    assert result.exit_code == 2
    assert result.stderr.startswith(f"{pyproject}: ")
    assert "Traceback" not in result.output


def test_stdout_preserves_crlf_line_endings(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_bytes(b"b = 1\r\na = 2\r\n")
    runner = CliRunner()

    result = runner.invoke(app, [str(path)])

    assert result.exit_code == 0
    # Bytes-level assert: text-mode newline translation must not double the CR.
    assert result.stdout_bytes == b"a = 2\r\nb = 1\r\n"
