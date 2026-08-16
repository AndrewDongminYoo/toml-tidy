import errno
import getpass
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Self, TextIO, cast

import pytest
from typer.testing import CliRunner

import toml_tidy.cli
from toml_tidy.cli import app

if TYPE_CHECKING:
    from collections.abc import Callable

_INVALID_UTF8 = b"a = \xff\xfe\n"
# rich treats GitHub Actions as a color terminal, so usage errors carry ANSI
# codes in CI but not locally; strip them before asserting on message text.
_ANSI_CODES = re.compile(r"\x1b\[[0-9;]*m")
_GETEUID = cast("Callable[[], int] | None", vars(os).get("geteuid"))
_RUNNING_AS_ROOT = _GETEUID is not None and _GETEUID() == 0


def _acl_command(verb: str, path: Path) -> list[str]:
    """Build the platform's grant-an-ACL-entry or print-the-ACL command."""
    if sys.platform == "darwin":
        entry = f"{getpass.getuser()} allow delete"
        return (
            ["/bin/chmod", "+a", entry, str(path)]
            if verb == "grant"
            else ["/bin/ls", "-lde", str(path)]
        )
    return (
        ["/usr/bin/setfacl", "-m", "u:root:rwx", str(path)]
        if verb == "grant"
        else ["/usr/bin/getfacl", "-cp", str(path)]
    )


def _grant_acl(path: Path) -> None:
    # No skip on a missing tool: a precondition that cannot be built is a
    # failure to report, not a pass to record.
    _ = subprocess.run(_acl_command("grant", path), check=True)  # noqa: S603


def _read_acl(path: Path) -> str:
    completed = subprocess.run(  # noqa: S603
        _acl_command("read", path), check=True, capture_output=True, text=True
    )
    return completed.stdout


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
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    source = "b = 1\na = 2\n"
    _ = path.write_text(source, encoding="utf-8")

    path.chmod(0o444)
    runner = CliRunner()

    result = runner.invoke(app, [str(path), "--in-place"])

    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert path.read_text(encoding="utf-8") == source


@pytest.mark.skipif(
    os.name != "posix" or _RUNNING_AS_ROOT,
    reason="unprivileged POSIX permission classes required",
)
def test_in_place_checks_effective_write_access(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    source = "b = 1\na = 2\n"
    _ = path.write_text(source, encoding="utf-8")
    # The owner class wins even though group and other have write bits.
    path.chmod(0o466)

    result = CliRunner().invoke(app, [str(path), "--in-place"])

    assert result.exit_code == 2
    assert "Traceback" not in result.output
    assert path.read_text(encoding="utf-8") == source


@pytest.mark.skipif(os.name == "nt", reason="non-Windows atomic replacement required")
def test_in_place_write_failure_preserves_original_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    source = "b = 1\na = 2\n"
    _ = path.write_text(source, encoding="utf-8")
    original_tempfile = NamedTemporaryFile

    class FailingWriter:
        _handle: TextIO
        name: str

        def __init__(self, handle: TextIO) -> None:
            self._handle = handle
            self.name = handle.name

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            self._handle.close()

        def fileno(self) -> int:
            return self._handle.fileno()

        def flush(self) -> None:
            self._handle.flush()

        def write(self, content: str) -> int:
            _ = self._handle.write(content[:2])
            message = "disk full"
            raise OSError(message)

    def failed_tempfile(*_args: object, **_kwargs: object) -> FailingWriter:
        handle = cast(
            "TextIO",
            cast(
                "object",
                original_tempfile(
                    "w",
                    encoding="utf-8",
                    newline="",
                    dir=tmp_path,
                    prefix=".config.toml.",
                    delete=False,
                ),
            ),
        )
        return FailingWriter(handle)

    monkeypatch.setattr(toml_tidy.cli, "NamedTemporaryFile", failed_tempfile)
    monkeypatch.delattr(os, "fchown", raising=False)

    result = CliRunner().invoke(app, [str(path), "--in-place"])

    assert result.exit_code == 2
    assert path.read_text(encoding="utf-8") == source


@pytest.mark.skipif(not hasattr(os, "fchown"), reason="file ownership API required")
def test_in_place_preserves_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_text("b = 1\na = 2\n", encoding="utf-8")
    original_stat = path.stat()
    ownership: list[tuple[int, int]] = []

    def record_ownership(_path: object, uid: int, gid: int) -> None:
        ownership.append((uid, gid))

    monkeypatch.setattr(os, "fchown", record_ownership)

    result = CliRunner().invoke(app, [str(path), "--in-place"])

    assert result.exit_code == 0
    assert ownership == [(original_stat.st_uid, original_stat.st_gid)]


@pytest.mark.skipif(not hasattr(os, "fchown"), reason="file ownership API required")
def test_in_place_falls_back_when_ownership_cannot_be_transferred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    source = "b = 1\na = 2\n"
    _ = path.write_text(source, encoding="utf-8")
    original_inode = path.stat().st_ino

    def deny_ownership_transfer(_path: object, _uid: int, _gid: int) -> None:
        raise PermissionError(errno.EPERM, "Operation not permitted", str(path))

    monkeypatch.setattr(os, "fchown", deny_ownership_transfer)

    result = CliRunner().invoke(app, [str(path), "--in-place"])

    assert result.exit_code == 0
    assert path.read_text(encoding="utf-8") == "a = 2\nb = 1\n"
    assert path.stat().st_ino == original_inode


def test_in_place_preserves_windows_security_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_text("b = 1\na = 2\n", encoding="utf-8")
    original_inode = path.stat().st_ino
    monkeypatch.setattr(toml_tidy.cli, "_IS_WINDOWS", True)

    result = CliRunner().invoke(app, [str(path), "--in-place"])

    assert result.exit_code == 0
    assert path.read_text(encoding="utf-8") == "a = 2\nb = 1\n"
    assert path.stat().st_ino == original_inode


@pytest.mark.skipif(
    os.name != "posix" or _RUNNING_AS_ROOT,
    reason="unprivileged POSIX directory modes required",
)
def test_in_place_writable_file_in_unwritable_directory(tmp_path: Path) -> None:
    directory = tmp_path / "unwritable"
    directory.mkdir()
    path = directory / "config.toml"
    _ = path.write_text("b = 1\na = 2\n", encoding="utf-8")
    original_inode = path.stat().st_ino
    directory.chmod(0o555)

    try:
        result = CliRunner().invoke(app, [str(path), "--in-place"])
    finally:
        directory.chmod(0o755)

    assert result.exit_code == 0
    assert path.read_text(encoding="utf-8") == "a = 2\nb = 1\n"
    assert path.stat().st_ino == original_inode


def test_in_place_updates_all_hard_links(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_text("b = 1\na = 2\n", encoding="utf-8")
    linked_path = tmp_path / "linked.toml"
    os.link(path, linked_path)

    result = CliRunner().invoke(app, [str(path), "--in-place"])

    assert result.exit_code == 0
    assert path.read_text(encoding="utf-8") == "a = 2\nb = 1\n"
    assert linked_path.read_text(encoding="utf-8") == "a = 2\nb = 1\n"
    assert path.stat().st_ino == linked_path.stat().st_ino


@pytest.mark.skipif(
    os.name != "posix",
    reason="symlink creation is privileged on Windows, which rewrites the inode",
)
def test_in_place_through_symlink_rewrites_the_target(tmp_path: Path) -> None:
    # Replacement targets the resolved path, so the link keeps pointing at a
    # file it still names instead of being overwritten by the temporary one.
    path = tmp_path / "config.toml"
    _ = path.write_text("b = 1\na = 2\n", encoding="utf-8")
    link_path = tmp_path / "link.toml"
    link_path.symlink_to(path)

    result = CliRunner().invoke(app, [str(link_path), "--in-place"])

    assert result.exit_code == 0
    assert link_path.is_symlink()
    assert path.read_text(encoding="utf-8") == "a = 2\nb = 1\n"


@pytest.mark.skipif(not hasattr(os, "fchown"), reason="file ownership API required")
def test_in_place_sync_failure_preserves_original_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    source = "b = 1\na = 2\n"
    _ = path.write_text(source, encoding="utf-8")

    def fail_sync(_file_descriptor: int) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "fsync", fail_sync)

    result = CliRunner().invoke(app, [str(path), "--in-place"])

    assert result.exit_code == 2
    assert path.read_text(encoding="utf-8") == source


def test_in_place_keeps_reused_temp_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_text("b = 1\na = 2\n", encoding="utf-8")
    reused_content = "created by another process"
    original_replace = Path.replace

    def replace_and_reuse(self: Path, target: Path) -> Path:
        result = original_replace(self, target)
        _ = self.write_text(reused_content, encoding="utf-8")
        return result

    monkeypatch.setattr(Path, "replace", replace_and_reuse)

    result = CliRunner().invoke(app, [str(path), "--in-place"])

    assert result.exit_code == 0
    reused_paths = list(tmp_path.glob(".toml-tidy-*"))
    assert len(reused_paths) == 1
    assert reused_paths[0].read_text(encoding="utf-8") == reused_content


def test_in_place_preserves_extended_attributes(tmp_path: Path) -> None:
    if not hasattr(os, "setxattr") or not hasattr(os, "getxattr"):
        pytest.skip("extended attribute APIs are unavailable")

    set_xattr = cast("Callable[[Path, str, bytes], None]", vars(os)["setxattr"])
    get_xattr = cast("Callable[[Path, str], bytes]", vars(os)["getxattr"])
    path = tmp_path / "config.toml"
    _ = path.write_text("b = 1\na = 2\n", encoding="utf-8")
    attribute = "user.toml_tidy_test"
    try:
        set_xattr(path, attribute, b"preserved")
    except OSError as error:
        pytest.skip(f"extended attributes are unsupported: {error}")

    result = CliRunner().invoke(app, [str(path), "--in-place"])

    assert result.exit_code == 0
    assert get_xattr(path, attribute) == b"preserved"


def test_in_place_replaces_the_inode(tmp_path: Path) -> None:
    # The counterpart to every st_ino equality above: without this, a
    # fallback that fires too eagerly would retire the atomic path unnoticed.
    path = tmp_path / "config.toml"
    _ = path.write_text("b = 1\na = 2\n", encoding="utf-8")
    original_inode = path.stat().st_ino

    result = CliRunner().invoke(app, [str(path), "--in-place"])

    assert result.exit_code == 0
    assert path.read_text(encoding="utf-8") == "a = 2\nb = 1\n"
    assert path.stat().st_ino != original_inode


@pytest.mark.skipif(os.name != "posix", reason="POSIX ACL tooling required")
def test_in_place_preserves_access_control_list(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_text("b = 1\na = 2\n", encoding="utf-8")
    plain = _read_acl(path)
    _grant_acl(path)
    granted = _read_acl(path)
    # Prove the fixture before trusting the result: a platform that quietly
    # refused the entry would otherwise report "preserved" having preserved
    # nothing. Not a skip — the precondition failing is a real failure.
    assert granted != plain, f"the ACL entry did not attach: {granted!r}"

    result = CliRunner().invoke(app, [str(path), "--in-place"])

    assert result.exit_code == 0
    assert path.read_text(encoding="utf-8") == "a = 2\nb = 1\n"
    assert _read_acl(path) == granted


@pytest.mark.skipif(
    os.name != "posix" or _RUNNING_AS_ROOT,
    reason="unprivileged POSIX write semantics required",
)
def test_in_place_preserves_setid_on_a_hard_linked_file(tmp_path: Path) -> None:
    # The rewrite-in-place paths share one writer, and an unprivileged write
    # clears these bits; the hard-link path lost them before the ACL path
    # existed, so the guard belongs to the writer rather than to one branch.
    path = tmp_path / "config.toml"
    _ = path.write_text("b = 1\na = 2\n", encoding="utf-8")
    os.link(path, tmp_path / "linked.toml")
    path.chmod(0o6755)

    result = CliRunner().invoke(app, [str(path), "--in-place"])

    assert result.exit_code == 0
    assert path.read_text(encoding="utf-8") == "a = 2\nb = 1\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o6755


@pytest.mark.skipif(
    os.name != "posix" or _RUNNING_AS_ROOT,
    reason="unprivileged POSIX write semantics required",
)
def test_in_place_preserves_setid(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_text("b = 1\na = 2\n", encoding="utf-8")
    path.chmod(0o6755)

    result = CliRunner().invoke(app, [str(path), "--in-place"])

    assert result.exit_code == 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o6755


def test_in_place_updates_mtime(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_text("b = 1\na = 2\n", encoding="utf-8")
    old_timestamp = 946684800
    os.utime(path, (old_timestamp, old_timestamp))

    result = CliRunner().invoke(app, [str(path), "--in-place"])

    assert result.exit_code == 0
    assert path.stat().st_mtime > old_timestamp


@pytest.mark.skipif(not hasattr(os, "pathconf"), reason="path limits unavailable")
def test_in_place_long_filename(tmp_path: Path) -> None:
    name_max = os.pathconf(tmp_path, "PC_NAME_MAX")
    suffix = ".toml"
    path = tmp_path / ("x" * (name_max - len(suffix)) + suffix)
    _ = path.write_text("b = 1\na = 2\n", encoding="utf-8")

    result = CliRunner().invoke(app, [str(path), "--in-place"])

    assert result.exit_code == 0
    assert path.read_text(encoding="utf-8") == "a = 2\nb = 1\n"


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


def test_unreadable_pyproject_probe_does_not_stop_remaining_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    target = bad_dir / "target.toml"
    _ = target.write_text("a = 1\n", encoding="utf-8")
    good_dir = tmp_path / "good"
    good_dir.mkdir()
    fixable = good_dir / "fixable.toml"
    _ = fixable.write_text("b = 1\na = 2\n", encoding="utf-8")
    original_is_file = Path.is_file

    def deny_probe(self: Path) -> bool:
        # A pyproject.toml symlinked into an unsearchable directory makes
        # is_file() raise PermissionError (verified on 3.12 and 3.13);
        # chmod-based setups are bypassed by root in CI, so simulate it.
        if self.name == "pyproject.toml" and self.parent.name == "bad":
            raise PermissionError(13, "Permission denied", str(self))
        return original_is_file(self)

    monkeypatch.setattr(Path, "is_file", deny_probe)
    runner = CliRunner()

    result = runner.invoke(app, [str(target), str(fixable), "--in-place"])

    assert result.exit_code == 2
    assert result.stderr.startswith(f"{target}: ")
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
    assert "--in-place or --check" in _ANSI_CODES.sub("", result.stderr)


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


def test_deeply_nested_config_exits_with_error(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    depth = 1500
    _ = pyproject.write_text(
        "meta = " + "[" * depth + "]" * depth + "\n", encoding="utf-8"
    )
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


def test_rejects_unknown_config_key(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    _ = pyproject.write_text("[tool.toml-tidy]\nblank_line = true\n", encoding="utf-8")
    path = tmp_path / "config.toml"
    _ = path.write_text("a = 1\n", encoding="utf-8")

    result = CliRunner().invoke(app, [str(path)])

    assert result.exit_code == 2
    assert result.stderr.startswith(f"{pyproject}: ")
    assert "unknown configuration key: 'blank_line'" in result.stderr


def test_stdout_preserves_crlf_line_endings(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_bytes(b"b = 1\r\na = 2\r\n")
    runner = CliRunner()

    result = runner.invoke(app, [str(path)])

    assert result.exit_code == 0
    # Bytes-level assert: text-mode newline translation must not double the CR.
    assert result.stdout_bytes == b"a = 2\r\nb = 1\r\n"


def test_blank_lines_flag_normalizes_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    _ = path.write_text("[y]\nq = 1\n\n\n[x]\np = 1\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(path), "--blank-lines"])

    assert result.exit_code == 0
    assert result.output == "[x]\np = 1\n\n[y]\nq = 1\n"


def test_config_blank_lines_is_read_from_pyproject(tmp_path: Path) -> None:
    _ = (tmp_path / "pyproject.toml").write_text(
        "[tool.toml-tidy]\nblank-lines = true\n", encoding="utf-8"
    )
    path = tmp_path / "config.toml"
    _ = path.write_text("[y]\nq = 1\n\n\n[x]\np = 1\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(path)])

    assert result.exit_code == 0
    assert result.output == "[x]\np = 1\n\n[y]\nq = 1\n"


def test_cli_no_blank_lines_flag_overrides_config(tmp_path: Path) -> None:
    _ = (tmp_path / "pyproject.toml").write_text(
        "[tool.toml-tidy]\nblank-lines = true\n", encoding="utf-8"
    )
    path = tmp_path / "config.toml"
    _ = path.write_text("[y]\nq = 1\n\n\n[x]\np = 1\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(path), "--no-blank-lines"])

    assert result.exit_code == 0
    assert result.output == "[x]\np = 1\n[y]\nq = 1\n\n\n"


def test_check_reports_blank_line_changes(tmp_path: Path) -> None:
    _ = (tmp_path / "pyproject.toml").write_text(
        "[tool.toml-tidy]\nblank-lines = true\n", encoding="utf-8"
    )
    path = tmp_path / "config.toml"
    _ = path.write_text("[x]\np = 1\n\n\n[y]\nq = 1\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(path), "--check"])

    assert result.exit_code == 1


def test_non_boolean_blank_lines_config_exits_with_error(tmp_path: Path) -> None:
    _ = (tmp_path / "pyproject.toml").write_text(
        '[tool.toml-tidy]\nblank-lines = "yes"\n', encoding="utf-8"
    )
    path = tmp_path / "config.toml"
    _ = path.write_text("b = 1\na = 2\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(app, [str(path)])

    assert result.exit_code == 2
    assert "blank-lines must be a boolean" in result.output
