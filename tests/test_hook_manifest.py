import re
from pathlib import Path

import pytest

_MANIFEST = Path(__file__).parent.parent / ".pre-commit-hooks.yaml"
# The project has no YAML dependency, so read the one plain scalar by line.
_FILES_LINE = re.compile(r"^\s*files:\s*(\S+)\s*$", re.MULTILINE)


def _files_pattern() -> str:
    match = _FILES_LINE.search(_MANIFEST.read_text(encoding="utf-8"))
    assert match is not None, "hook manifest has no files: pattern"
    return match.group(1)


# identify tags these lockfiles as toml, so types: [toml] alone selects them;
# pre-commit filters filenames with re.search against the files pattern.
@pytest.mark.parametrize(
    "filename",
    [
        "uv.lock",
        "poetry.lock",
        "Cargo.lock",
        "sub/uv.lock",
        "sub/Cargo.lock",
        "Pipfile.lock",
        "NotPipfile",
    ],
)
def test_hook_skips_lockfiles(filename: str) -> None:
    assert re.search(_files_pattern(), filename) is None


@pytest.mark.parametrize(
    "filename",
    [
        "config.toml",
        "pyproject.toml",
        "Cargo.toml",
        "nested/settings.toml",
        "CONFIG.TOML",
        "a.Toml",
        "Pipfile",
        "sub/Pipfile",
    ],
)
def test_hook_selects_toml_files(filename: str) -> None:
    assert re.search(_files_pattern(), filename) is not None
