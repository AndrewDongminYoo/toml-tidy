# Quality Tooling Refresh Plan

## Work

1. Verify repository identity, instructions, current changes and Oracle precedent.
2. Verify public release metadata, refresh compatible locked dependencies with `uv lock --upgrade`, and pin the latest supported enabled linters.
3. Run the declared local gates, direct OSV lockfile scan, CI-declared `tomlkit` overrides, and `trunk check --all --no-fix --jobs=2`.
4. Reconcile affected knowledge, prove custom validators reject broken inputs, and review the scoped candidate.
5. Commit locally and prepare visible evidence before requesting publication approval.

## Success Criteria

1. Compatible dependency refresh → verify: `uv lock --check` and `uv sync --locked`.
2. Project behavior and static quality → verify: the declared Python gates and the two CI `tomlkit` overrides.
3. Full tool and security coverage → verify: no-fix Trunk output and direct OSV Scanner 2.7.0 output naming the actual lockfile.
4. Knowledge consistency → verify: checked commands, paths, metadata URLs, and internal links in `knowledge-audit.json` in the visible review folder.

## Declared Gates

- `uv sync --locked`
- `uv run --no-sync pytest`
- `uv run ruff check src tests`
- `uv run ruff format --check src tests`
- `uv run basedpyright`
- `trunk check --all --no-fix --jobs=2`
