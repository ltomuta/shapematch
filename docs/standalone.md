# Standalone migration

## Runtime architecture

The headless runtime retains the feature extraction and SVD research routines
behind `STEPmodel`. `shapematch_geometry` owns Open CASCADE shapes and provides
STEP import, closed-shell validation, transforms, and bounded-face intersections.
It uses the pinned `cadquery-ocp==8.0.1.1.0` wheel directly, without the CadQuery
modeling framework or the FreeCAD application. OCP transitively installs VTK;
this is a native-library installation, not a pure Python implementation.

The runtime imports no FreeCAD, FreeCADGui, Part, Qt, or Pivy modules. Feature
caches never serialize CAD handles. Loading a cache verifies its STEP source's
SHA-256 hash and reconstructs the stored scale/rotation/translation. Cache version
1 uses `format: "shapematch.features"`; changing the schema requires a new version.
Results use distinct format identifiers, so batch discovery ignores them.
Source paths are stored with forward slashes; historical Windows relative paths
are accepted on Unix too. New single-run reports reference caches relative to
the report, so report/cache/STEP trees can move between operating systems.
Cross-drive references on Windows fall back to absolute paths and remain local;
keep the bundle on one drive for portability. The cache schema remains version 1.

The wheel contains seven Python modules and project metadata. Benchmark data,
reference tooling, and the original viewer are repository/source-distribution
resources, not installed runtime modules. Original `.obj` and `.iv` examples are
unchanged. No old cache deserialization or conversion is performed.

## Intentional behavior corrections

- Forward queries use `V2 - V1` as their direction, exclude the starting surface
  point within geometric tolerance, and have no 300-unit cutoff. Full-line queries
  likewise remove the old 100-unit cutoff. Intersections use bounded faces,
  including for the historical third intersection method; hits at seams/shared
  edges are deduplicated and sorted by signed line parameter.
- Scale, rotation, and translation are applied in that order. Positive scales
  below one now work. Reloading a model preserves explicit zero/default transform
  values rather than confusing them with omitted arguments. Invalid transforms,
  zero-length rays, invalid models, and surface-only open shells produce errors.
- Removed NumPy APIs use their supported equivalents (`inf`, floating `asarray`,
  and supported array operations). The corrected alignment solver uses explicit
  array matrix products.
- Spherical-only feature representations are recognized from their stored radii
  even when they contain no registration points. Histogram construction includes
  spherical radii, and comparisons
  consisting solely of spherical categories avoid division by zero.
- Empty/zero-distance signatures return no feature correspondence instead of
  taking a quantile of an empty array. Incomplete helical permutations are ignored.
  The corrected solver also evaluates independent CAD geometry candidates;
  missing features no longer prevent a surface-based fit.
- CLI parsing, entry points, output paths, and packaging metadata are repaired.
  Workflows copy feature data before pairwise centroid correction and preserve
  zero alignment errors. Model selection is sorted, seeded, and capped at 20
  candidates; batch comparison evaluates every ordered pair.

### Scan algorithm corrections

Centroid discovery starts from CAD mass properties rather than requiring a part
to intersect a sparse set of rays from the world origin. The seed is refined
using the existing surface bounding-center calculation, with a scale-relative
convergence threshold. Feature counts and centroids may intentionally differ
from older caches. The missing-cluster return value no longer places feature
labels in coordinate fields. Midpoint refinement rejects nonprogressing brackets;
tangent and outer feature searches have finite iteration limits. Surface-point
deduplication uses a hash index while retaining the public list's ordering.

App radial-feature extraction also has a soft time budget. When it expires, the
validated CAD geometry and any completed features remain available to the
existing geometry-based registration path; the file receives a note. Ordinary
CLI extraction remains unbudgeted. These are algorithm/workflow corrections,
not changes to the SVD transform or bidirectional surface-error definition.

### CAD import corrections

Mixed solid/surface exports retain closed bodies and explicitly report excluded
ancillary open shells and loose faces. Standalone closed shells become validated,
oriented solids. Invalid topology is still rejected without automatic healing.
Ray limits are derived from the complete selected-body bounding box. Native
intersection failures on analytic faces use exact support-surface intersections
classified against bounded faces, preserving trims and holes. Native-coordinate
imports avoid an unnecessary geometry copy and repeated topology validation.

The local app extractor identity is now `native-features-1-2`. On launch, an old
selected catalog automatically rescans all entries, including previous failures;
successful old descriptors are regenerated too. The data-only cache schema
remains version 1. See [Local STEP library](local-app.md) for scan notes and limits.

## Reproducing validation

Install the package and test tools in a virtual environment:

```bash
python -m pip install -e '.[test]'
python -m pytest -q
python -m pytest -m integration
python -m pip install build
python -m build
```

Tests cover analytic geometry, all 15 captured FreeCAD intersection cases,
cache portability/invalidation, deterministic CLI execution, legacy rejection,
and seeded geometric self-alignment and shape differences. The CLI integration test blocks
FreeCAD and graphics imports explicitly and unsets display variables. CI is
configured for Linux x86-64, Windows x86-64, macOS Intel, and macOS
Apple Silicon with Python 3.11 and 3.12. Each job runs the full suite (including
integration cases), builds distributions, reinstalls the wheel, and checks both
CLI entry points. Native Windows/macOS validation is pending the first CI run.

`tools/reference.py` runs each fixture/seed in a subprocess with a timeout,
captures geometry before extracting features, and records partial progress and
failures. It exercises cube, sphere, cylinder, torus, and tetrahedron models
with seeds 0, 1, and 2. All runs bypass the historical CLI and disable scenes and
sound. FreeCAD capture additionally requires its native installation and Pivy;
the optional `reference` extra supplies dill for the archived module.

```bash
python tools/reference.py --backend ocp --output tests/reference/ocp.json
python -m pip install '.[reference]'
QT_QPA_PLATFORM=offscreen python tools/reference.py --backend freecad \
  --output tests/reference/freecad.json --timeout 180
python tools/compare_references.py tests/reference/freecad.json \
  tests/reference/ocp.json --output tests/reference/comparison.json
```

The archived source is unmodified. The FreeCAD harness provides compatibility
aliases for removed NumPy APIs, recording runtime versions in each result.
`freecad-initial.json` preserves the first run, before all those aliases were
added, including its feature-extraction failures/timeouts. Coordinate comparisons
use `max(1e-7, 1e-7 * reference bounding-box diagonal)`.

The recorded comparison reports agreement for all 15 geometry cases against
FreeCAD 0.21.2. All 15 complete FreeCAD matching trials exceeded the 180-second
per-case limit; eight captured source features before timing out. Consequently,
full FreeCAD ranking/error parity is unverified. Those timeouts and partial
results are retained in `tests/reference/freecad.json` and
`tests/reference/comparison.json`. All 15 standalone trials completed, and
their original results are retained for before/after diagnosis. Current tests
require geometric correctness instead of retaining the old histogram bug.

## Alignment update and accuracy boundaries

See [alignment corrections](alignment.md) for the paired-mean SVD fit, proper
rotations, geometry-based fallback, bidirectional surface errors, and new CSV
columns. The histogram's former signed-sum equality check could return a perfect
score for different descriptors; its historical threshold outcomes are not
compatibility requirements. Thresholds are now informational, and geometry is
registered independently.

Feature discovery still uses absolute tolerances and can vary with scale,
symmetry, and CAD kernel version. Geometry registration is a bounded search
with local refinement, so a global optimum is not guaranteed for arbitrary
parts. Sampling covers every face but does not certify every local deviation.
A finite alignment error is not a calibrated match decision or manufacturing
tolerance check. Identical feature counts and exact reproduction of historical
numeric scores are not release guarantees. Replacement desktop visualization and
legacy-cache conversion remain deferred. Offline [HTML overlays](html.md) are available
through the optional `html` extra.
