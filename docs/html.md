# HTML part overlays

Install the optional exporter in the active environment:

```bash
python -m pip install '.[html]'
```

Export an existing single-comparison report:

```bash
python src/shapematch.py visualize /tmp/comparison/comparisons.json \
  -o /tmp/comparison/comparison.html
```

The default pair is the source and the file passed as `-nf`. Use `--pair positive`
to inspect the source and its transformed control. New JSON reports store the
recovered transform, so visualization does not repeat registration. Older reports
are refitted once using the saved feature caches. STEP sources must still exist
and pass the cache hash check when exporting. The resulting HTML no longer needs
the report, caches, STEP files, Python, or internet access.

For a new run, append `--html` or `--html filename.html` to `singlePRtest`. Relative
HTML paths are resolved under `-O`. The default is `comparison.html`. HTML export
currently targets single comparisons, not a whole batch matrix.

## Reading the overlay

- Blue is the source; orange is the compared part. Checkboxes and independent
  opacity sliders reveal coincident faces, openings, and internal walls.
- Drag to orbit, scroll to zoom, or shift-drag to pan. Isometric, top, front, and
  side buttons restore standard views. Mesh edges can be shown separately.
- **Recovered alignment** displays the fit used by the saved RMS measurement.
  The native-unit fit scale is shown explicitly.
- **Original STEP placement** shows each file in its original coordinates,
  without the benchmark transformations or recovered scaling. Start here with
  `--placement original` when the inputs are already aligned in CAD.

## Boolean regions

Select **Display → Boolean regions** to inspect the actual material partition:

- **Common (A ∩ B)** is solid material present in both parts.
- **Only A (A − B)** is material present in A and absent in B.
- **Only B (B − A)** is material present in B and absent in A.

A is the source file; B is the compared file. A hole added to B appears as an
Only A region, because A still contains material at that location. Each category
has a visibility checkbox, a CAD volume in mm³, and its number of solids.
**Differences only** hides the common material; **All regions** restores the
three categories. Common and exclusive material have separate opacity controls.
Choose blue/orange, magenta/cyan, or blue/yellow color schemes.

Both original and recovered placements are calculated during export. Switching
placement selects the corresponding precomputed regions and volumes offline.
Recovered alignment includes its fitted uniform scale and can introduce thin
difference regions from small residual misalignment. Use original placement
when your CAD files already share their intended coordinates.

Regions are produced by Open CASCADE Boolean intersection and subtraction on
the CAD solids, before tessellation, preserving all disconnected solid results.
Volumes are computed from CAD geometry, rather than inferred from display meshes
or the RMS samples. Shared faces, edges, or vertices alone have no common solid
volume and are shown as empty. Identical bodies have empty directional differences.

Boolean export requires solid bodies. Surface-shell inputs and native Boolean
failures retain the part overlay, but mark regions as **Unavailable** for the
affected placement. An operation failure is never reported as an empty difference.
Precomputing both placements adds export time and geometry to the HTML file;
the browser still needs no Python service or network access.

### Positional tolerance

**Boolean tolerance (mm)** controls the CAD kernel's additional fuzzy tolerance
for near-coincident faces, edges, and vertices. It is a linear distance, not a
minimum-volume filter: a thin skin across a large face can be ignored without
discarding a larger drilled hole. For example, a 0.001 mm tolerance can suppress
skins caused by a 0.0005 mm coordinate offset. Actual feature changes larger than
the selected tolerance remain in the test cases; large tolerances can also merge
or ignore genuine small features, so choose the value deliberately.

The default is **0**, retaining the numerical baseline of at least 1e-7 mm.
The offline file includes 0, 0.001, 0.01, and 0.1 mm, plus any custom export value:

```bash
shapematch visualize /tmp/comparison/comparisons.json \
  -o /tmp/comparison/comparison.html --placement original --boolean-tolerance 0.01
```

The same flag is accepted by `singlePRtest --html`. Values must be finite and
nonnegative. Tolerance is applied after removing benchmark poses, in original
millimeters. Both placements are precomputed at every included level so the
browser can change the actual CAD regions offline; it does not merely hide
triangles or components. This increases export time and file size.

RMS, registration, and original input solids are unchanged. Reported volumes
reflect the selected fuzzy classification and need not obey an exact original
material balance at positive tolerances. Near-coincident curved faces can make
some fuzzy operations invalid or unmeshable. Those levels are labeled
**unavailable** for that placement; use the numerical baseline or original
placement rather than silently accepting an invalid result.

Recovered-fit residuals are not floating-point rounding: a small fitted scale or
rotation can create visible difference sheets. When inputs are already aligned
in CAD, original placement is usually the useful starting view. Tolerance is
separate from that alignment choice and does not repair an incorrect fit.

The report hides RMS values in original-placement mode because those saved
measurements describe the recovered fit. Switching the view does not recompute
distances or claim an original-placement measurement.

Display coordinates are in millimeters in the target's native STEP frame. The
exporter applies the fitted source-to-target transform and then removes the
target's benchmark pose; it removes each model's own benchmark pose for the
original-placement view. Arbitrary random benchmark scale/translation must never
leak into the displayed dimensions.

Meshes use Open CASCADE's bounded-face triangulation with explicit triangle
indices, including trimmed holes, outward winding, and face locations. This
display tessellation is approximate; it does not replace the exact CAD surfaces
used to compute sampled RMS distances. Coincident transparent surfaces can
blend ambiguously: hide one part or adjust opacity to inspect details. No
deviation heatmap or manufacturing tolerance verdict is implied by the colors.

Plotly.js and all geometry are embedded in the file. Export size is typically
several megabytes because the renderer is included. Use a browser with WebGL
enabled. Filenames are embedded as inert JSON and rendered as text.

## Validation

The normal pytest suite checks Boolean volume conservation, holes, containment,
disjoint/touching/identical inputs, multiple solids, pose conversion, and failure
handling, as well as mesh winding, holes, pose conversion, stored-fit
reuse, old report migration, dependency errors, and HTML escaping. The optional
browser regression runs a local file while explicitly offline and verifies its
controls and coordinate changes:

```bash
python -m pip install playwright
python -m playwright install chromium
SHAPEMATCH_BROWSER_TESTS=1 python -m pytest tests/test_html_browser.py
```

Browser tests are opt-in because they need a separately installed Chromium.
