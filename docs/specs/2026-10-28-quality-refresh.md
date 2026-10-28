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
- TruffleHog scans filesystem targets offline with `--no-verification --no-update` and a project-relative reader declared in `direct_configs`.
- The reader preserves decoded findings without literal source locations, emits no secret values in SARIF messages, and rejects malformed input without clean output.
- The reader's 10 standard-library tests run in CI, and direct encoded, literal and clean controls retain scanner findings.
- The inherited SARIF output, success codes `0` and `183`, and `copy_targets` sandbox remain in effect.

## Verification Boundaries

Local verification runs on macOS with Python 3.13.
The Linux runners and Python 3.12 combinations remain hosted CI checks.
Trunk's Ruff integration checks lint rules; the independent Ruff format gate remains required.

## Publication

Push and PR creation require explicit repository-specific approval.
After approval, continue the requested hosted gates, merge, cleanup and memory recording.

## Scoped Reader Lint Compatibility

The shared standard-library adapter has exact code-specific Ruff style exceptions for its untyped JSON boundary, CLI output and standalone unittest conventions.
Other Ruff rules remain active on both files.
Only the observed fixed-argument, shell-free subprocess call carries `S603` suppression.
The application and existing test rules are unchanged.
Pinact upgrade also refreshes setup-uv to the verified `v10.3.3` commit in CI and release workflows without changing workflow commands or permissions.
