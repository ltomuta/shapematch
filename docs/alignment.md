# Alignment and surface-error corrections

The original benchmark could report substantial error when comparing a STEP
file with a randomly transformed copy of itself. This update repairs the fit
and its measurement; it does not remove the benchmark's random transforms.
Keep using the existing `singlePRtest` command. Existing JSON feature caches
can also be reused with `-sf`, `-nf`, and `-pf`.

## Transform convention and fitting

All alignment functions now use:

```text
target_point = scale * rotation @ source_point + translation
```

Corresponding points are centered on their own paired means. Independently
estimated model centroids are not valid substitutes: missing features or added
holes shift those estimates. SVD enforces `det(rotation) = +1`; mirror transforms
are not silently accepted. Scale is fitted from centered correspondences and
translation is composed about the coordinate origin. `rigidTransform3D` retains
its old arguments, but uses paired means and fixes scale at one.

`predictionTest5` keeps its four-element return tuple, now consistently returning
the forward rotation, translation, and **target/source** scale with the error.
`SVDerror` undoes the legacy temporary target scaling before fitting, and returns
the same forward-transform convention. Python callers should apply these
transforms directly, rather than scaling again about a model centroid.

Two point correspondences cannot determine full 3D orientation. Feature-based
fits are therefore hypotheses, not the only possible registration. Principal
inertia frames and planar-face frames add geometry-derived hypotheses, including
the 24 proper signed axis permutations. Planar frames handle isotropic inertia,
such as a cube. No filenames, benchmark transform metadata, or known test
transforms are used to recover alignment.

All valid generated helical candidates are retained instead of choosing by signed radial
error or returning after the first fit. Candidates are scored on actual CAD
boundaries; the best three coarse fits undergo bounded closest-surface
refinement when needed. Refinement trims its largest residuals to tolerate local
differences, but reported errors include every sampled residual.

## Measuring differences

For a source-to-target transform, forward distances measure transformed source
surface points against the target boundary. Reverse distances measure target
surface points against the inverse-transformed source boundary, converted to
target units. The symmetric error is:

```text
sqrt((mean(forward_distance²) + mean(reverse_distance²)) / 2)
```

Each direction has equal weight even if the point counts differ. Distances are
to bounded CAD faces, including hole walls. A point inside the other solid must
have a nonzero distance to its boundary, rather than being treated as an overlap
with zero error. The implementation uses Open CASCADE's
[shape-distance API](https://occt3d.com/dev/doc/refman/html/class_b_rep_extrema___dist_shape_shape.html)
against a compound of faces, not a solid.

Sampling is deterministic, weighted by triangulated face area, with at least one
point per face so small hole faces remain represented. Mesh samples are projected
back onto exact CAD faces; tessellation is not the final distance metric. The
default comparison uses at least 96 points per model. RMS values remain sampled
estimates and do not certify the maximum deviation or all local differences.
Independent remeshing can change the sample locations and nonzero estimates;
the physical-unit conversion does not remove that sampling variation.

CLI `alignment_error`, `forward_error`, and `reverse_error` are in millimeters,
after undoing the placed target's benchmark scale. The normalized error divides
the placed target-frame RMS by the target bounding-box diagonal in that same
frame. Thus its denominator is dimensionally consistent. Historical CSV errors
used a different measurement and normalization and should not be compared
numerically with these new values. `error_units` records `mm` explicitly.

## Histogram and regression behavior

Normalized descriptor differences no longer cancel through their signed sum.
For example, `[1, 2, 3]` and `[3, 2, 1]` now have correlation distance 2 instead
of zero. Constant/empty descriptor cases avoid undefined correlation results.
Feature categories still influence the histogram score; matching sparse
descriptors is not proof of matching surfaces.

`histogram_passed` preserves the former single/batch thresholds as diagnostics.
Geometry is evaluated independently instead of discarding plausible matches
because feature extraction missed or misclassified a surface. `alignment_found`
only means that a finite fit exists; users must assess its measured deviation.

Tests cover exact known rotation/translation/scale recovery, biased centroids,
reflection rejection, signed histogram cancellation, interior boundary distance,
and added-hole differences. Seeded real-geometry controls require near-zero
normalized self-error and larger error for a different shape. Old reference
captures are retained as evidence; tests no longer require the histogram bug's
previous gate outcomes. Windows/macOS and replacement desktop visualization are deferred.
Offline [HTML overlays](html.md) display the original and recovered placements.
