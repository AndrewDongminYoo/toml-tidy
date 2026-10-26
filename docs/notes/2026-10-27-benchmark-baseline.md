# Benchmark baseline, 2026-10-27

Baseline for `scripts/benchmark.py`, recorded with the per-invocation configuration discovery cache from issue #41.
The script is a local comparison tool, not a CI gate, so these numbers only compare revisions on this one machine.

## Environment

- Machine: Mac mini `Mac16,10`, Apple M4, `arm64` (`uname -m`), macOS 27.0.1.
- Python: CPython 3.13.14, the interpreter `uv run` selected.
- tomlkit: 0.15.2.
- Load average during the runs: about 3.0 to 4.0.

## Command

The "after" numbers come from this branch:

```sh
uv run python scripts/benchmark.py
```

The "before" numbers come from the same script, importing the package source of `origin/main` at `7c0ad27`, which was exported with `git show` into a scratch directory placed first on `PYTHONPATH`:

```sh
PYTHONPATH=<scratch>/main-src uv run python scripts/benchmark.py
```

The script prints the `toml_tidy` directory that it imported, and that line confirmed which source each run used.
The before and after runs were interleaved (main, branch, main, branch) so that a change in machine load would show in both.

## Cases

Each case runs 5 times with seed 41, and the table gives the best and the median wall-clock seconds.

- Bracketed tables: 2,000 shuffled tables of 10 shuffled keys each, sorted with `sort_toml`.
- Dotted keys: 800 groups of 10 top-level dotted keys (`group1.key2 = …`), shuffled, sorted with `sort_toml`.
- Wide arrays: 1,000 shuffled single-line arrays of 20 strings, sorted with `line_width=88` so that every array expands.
- Multi-file CLI: one `--check` invocation through `CliRunner` over 400 already sorted files in 4 directories, below a table-less `pyproject.toml` and a configured `pyproject.toml` further up.

## Results

The single-file cases do not touch discovery, so their numbers are the same on both revisions within noise.
The block below is from the first `origin/main` run:

```log
case                                   size    best s  median s
bracketed tables                  493,789 B     0.320     0.343
dotted keys                       165,790 B     0.358     0.366
wide arrays, width 88             379,690 B     0.187     0.197
```

The multi-file case is the one that the cache targets:

| Revision                  | Run 1 best / median (s) | Run 2 best / median (s) |
| ------------------------- | ----------------------- | ----------------------- |
| `origin/main` (`7c0ad27`) | 0.093 / 0.099           | 0.093 / 0.094           |
| This branch (cache)       | 0.055 / 0.056           | 0.056 / 0.057           |

With the cache, the multi-file invocation takes about 40% less time.
Before the cache, each of the 400 targets walked up to the configured `pyproject.toml`, probed three names in every directory on the way, and parsed both `pyproject.toml` files.
With the cache, each directory is searched once and each configuration file is parsed once.

## Observations

- The dotted-key file is a third the size of the bracketed-table file and takes about the same time, so at this shape `sort_toml` (tomlkit parse and dump included) spends about 3 times as long per byte on dotted keys.
- A full script run takes about 5 seconds on this machine.
- CLI start-up is not in these numbers, because the multi-file case runs in-process.
