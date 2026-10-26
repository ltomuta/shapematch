# Repository Guidelines

## Project Structure & Module Organization

ShapeMatch compares STEP CAD geometry through feature points and SVD alignment.

- `src/shapematch.py` retains the research algorithms and `STEPmodel` interface.
- `src/shapematch_geometry.py` wraps CAD operations; `src/shapematch_alignment.py` fits proper similarity transforms and scores both boundaries.
- `src/shapematch_cache.py` handles versioned JSON feature caches; `src/shapematch_workflows.py` implements the headless CLI.
- `src/shapematch_html.py` exports offline overlays; `src/shapematch_boolean.py` computes common/exclusive solids. Install `.[html]` to enable export.
- `tests/` contains pytest coverage and captured reference reports. `tools/reference.py` regenerates isolated reference runs.
- `data/primitives/` contains benchmark STEP models. Preserve its license and source fixtures.
- `legacy/` archives the original FreeCAD implementation and viewer, excluded from installed wheels. `data/shapeCache/` contains historical artifacts.
- `docs/images/` supplies research illustrations; `docs/standalone.md` documents migration behavior.

## Build, Test, and Development Commands

Use Python 3.11 or 3.12 in a virtual environment on Linux x86-64, Windows x86-64, or macOS (Intel/Apple Silicon). FreeCAD and a display server are not runtime requirements.

```bash
python -m pip install -e ".[test]"
python -m pytest -q
python -m pytest -m integration
python -m pip install build
python -m build
shapematch singlePRtest -I data/primitives -O shapematch-output/features --seed 0
shapematch multiPRtest -I shapematch-output/features -O shapematch-output/results --seed 0
```

These commands install development dependencies, run checks, build distributions, and exercise feature generation and batch matching. `python -m shapematch` is equivalent to the console command.

## Coding Style & Naming Conventions

Use four-space indentation. Preserve existing camelCase algorithm APIs and PascalCase classes; new infrastructure uses snake_case. Avoid unrelated formatting changes. No formatter or linter is configured. Explain numerical tolerances, coordinate transforms, and geometry assumptions in comments or docstrings.

## Testing Guidelines

Use pytest with `test_*.py` filenames. Test geometry against analytic expectations and FreeCAD reference intersections. Cover transforms, cache invalidation, and headless CLI behavior. Use fixed seeds and known-transform self-controls for matching regressions; do not preserve incorrect historical scores or require identical feature counts. No coverage percentage is mandated. Keep generated experiments outside tracked fixtures. Record reference failures explicitly rather than replacing them with invented expected values.

## Commit & Pull Request Guidelines

History uses short messages such as `Update shapematch.py`; no formal convention exists. Write specific, concise subjects. PRs should explain affected workflows, link relevant issues, and report validation results or blockers. Include numerical comparisons for matching changes. Document intentional algorithm corrections separately from backend changes.
