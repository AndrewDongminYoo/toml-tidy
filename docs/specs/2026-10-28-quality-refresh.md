# Quality Tooling Refresh

<!-- cspell:words pinact -->

## Approved Scope

Refresh compatible locked dependencies and existing quality tooling.
Generate `uv.lock` with `uv lock --upgrade` and preserve public APIs and source behavior.
Keep the deliberate `tomlkit>=0.15.0,<0.16` private-representation contract, Python support, Trunk CLI, plugin refs, and runtime pins.
Correct repository URL metadata and directly affected development guidance.

## Acceptance

- Pinact remains locked to `4.1.1` with `lint` and `upgrade`.
- The full no-fix Trunk scan and declared project gates pass.
- Changed manifests have package-manager-generated lockfiles under repository policy.
- Advisory dispositions have specific evidence and affected knowledge claims are reconciled.
- OSV Scanner 2.7.0 reads the actual `uv.lock`, with package coverage recorded separately from its exit code.
- The CI-declared `tomlkit` 0.15.0 and 0.15.1 overrides pass the test suite on the local Python 3.13 runtime.

## Verification Boundaries

Local verification runs on macOS with Python 3.13.
The Linux runners and Python 3.12 combinations remain hosted CI checks.
Trunk's Ruff integration checks lint rules; the independent Ruff format gate remains required.

## Publication

Push and PR creation require explicit repository-specific approval.
After approval, continue the requested hosted gates, merge, cleanup and memory recording.
