# Local STEP library

Install Python 3.11 or 3.12 and the app extra, then launch:

```sh
python -m pip install 'shapematch[app]'
shapematch app
```

From a checkout use `python -m pip install -e '.[app]'`. The command starts a
loopback HTTP server on an automatically allocated port and opens your browser.
If automatic opening fails, use the session URL printed in the terminal.
Keep that terminal running; Ctrl+C stops the application. No Node, native GUI
SDK, upload, hosted service, CDN, or Internet connection is needed at runtime.
Plotly JavaScript ships with the optional Python visualization dependency.

Choose a library folder using directory navigation, breadcrumbs, the Windows
drive selector, or a direct directory path. Select **Use this folder**, then
**Rescan**. Discovery accepts `.stp` and `.step` in any extension case, including
nested folders and Unicode paths. Recursive discovery skips directory links,
Windows junctions, and other redirecting directory reparse points.

Type a filename or relative path and explicitly choose a query from the matches.
Use **Browse for external STEP** to select a file outside the indexed library.
The browser navigates files on the machine running ShapeMatch; it does not upload
files. Library matches and filesystem listings are paginated. Switching library
folders retains each folder's catalog and the active selection survives restart.

Fast search uses the union of the top 100 radial-feature histograms and top 100
scale-independent summaries (area / volume^(2/3), principal-inertia ratios).
There is no histogram rejection threshold. At most 200 candidates receive CAD
surface registration. Thorough search compares every eligible indexed file.
Both searches return up to 20 provisional results, ordered by normalized
bidirectional surface RMS, physical RMS, then relative path. The query's canonical
path is excluded, while identical copies at other paths remain eligible.

Uniform scale is ignored by default. **Match physical size** fixes scale to one
through candidate fitting, principal-frame registration and refinement. Results
show RMS in mm, RMS / target bounding-box diagonal, and the query-to-result scale.
These sampled surface deviations are not probabilities, and registration does
not guarantee a globally optimal fit. Fast, cancelled, interrupted, and failed-part
searches explicitly report incomplete coverage, with checked/shortlisted and
eligible counts. Changed files produce errors until rescanned.

Select a result to generate an interactive aligned overlay on demand. Toggle
either part, adjust opacity, switch to original STEP coordinates, rotate and zoom.
Previews reuse the saved alignment; Boolean partitions are omitted. Existing CLI
HTML export still computes Boolean regions by default.

## Storage and jobs

The default storage is platformdirs' local user-data location for `ShapeMatch`
(`~/.local/share/ShapeMatch` on typical Linux installations, local AppData on
Windows, and `~/Library/Application Support/ShapeMatch` on macOS). SQLite and JSON
caches stay there even when the library is a network share. Files are never
modified. Advanced launch options:

```sh
shapematch app --workers 2 --timeout 300 --port 0 --no-browser
shapematch app --data-dir /path/to/local/cache
```

Keep `--data-dir` on local storage, outside the scanned folder. The default
worker count is two, capped by available CPUs. The **CAD workers** control
changes the limit during a running job;
increases start additional tasks and decreases let existing tasks finish.
Progress shows discovery, hashing and extraction phases, elapsed time, average
files per minute (including cache hits and failures), and active filenames with
their running times. The first scan must hash all files before extraction starts.
Only one scan or search runs at a time; browsing remains available.
Workers use `spawn` on every platform and
exchange paths/JSON-compatible data, never CAD handles. Each task gets a disposable
process, so native crashes and timeouts do not poison subsequent tasks. Previews
share the same worker limit. Cancellation stops active worker tasks, preserves
completed caches and results, and stops scheduling new work.

Every manual rescan hashes every discovered STEP file. Content and explicit
extraction-version matches reuse deterministic native-coordinate features.
When the selected library was indexed with an older extractor, the next app
launch automatically starts a rescan. It retries failed files and refreshes old
successful descriptors together; this one-time refresh can take as long as a
new scan. Later launches reuse the corrected caches. Other libraries are
refreshed when explicitly rescanned.
Byte-identical entries share a feature cache, but keep independent source paths;
a duplicate remains usable if the cache's original source is deleted. Source
hashes are checked again before newly computed features, comparisons or previews
are accepted. Deleted entries are removed only under successfully scanned
parents, including verified missing subtrees; inaccessible directories retain
previous entries and report errors. Searching retained entries still verifies
the source hash. Interrupted jobs are marked on restart and can be retried with
a rescan or new search. Cache garbage collection is not automatic.

Mixed solid/surface exports are matched using their closed bodies. Standalone
closed shells are converted to oriented solids; ancillary open shells and loose
faces are excluded. Surface-only files still require an enclosed body. Files
with import or limited-feature notes show **notes** in the file list; hover over
the entry for details. Invalid topology is reported rather than silently healed.

Centroid detection starts at the CAD center of mass and refines using surface
intersections. Local feature searches have finite iteration and progress limits.
If a feature search reaches its limit, partial radial features and the existing
CAD principal-frame registration candidates remain available, with a note on the
file. App scans also give radial feature search up to 60 seconds (or half the
configured worker timeout, if shorter), then keep available features and the CAD
descriptor instead of failing an otherwise usable model. Feature counts in these
limited scans can depend on runtime speed; command-line extraction has no such
time budget. A single blocked CAD call is still subject to the hard worker timeout.
Surface intersections use an exact, trim-aware analytic fallback when the
CAD kernel fails on a supported analytic face.

The service binds only `127.0.0.1`. API calls require a fresh per-launch session
token, and Host and browser Origin headers are validated. The token travels in
the launch URL fragment, is removed from the address bar, and is kept in the
browser tab's session storage. Run one app instance per data directory. This is
a single-user local application, not a shared-hosting server.

## Validation and measurements

Run `python -m pytest -q` after installing `.[app,test]`. Browser tests are opt-in:

```sh
python -m pip install playwright
python -m playwright install chromium
SHAPEMATCH_BROWSER_TESTS=1 python -m pytest -q tests/test_app_browser.py
```

`SHAPEMATCH_CHROMIUM=/path/to/chromium` selects an existing Chromium executable
for the app browser test. It exercises a real HTTP server with a deterministic
CAD stub; separate integration tests use actual spawned Open CASCADE workers.
`tools/app_smoke.py` verifies the installed app launches and serves bundled assets
and its authenticated API from outside the checkout. CI runs backend tests,
builds wheels and runs the smoke test on Linux, Windows, Intel Mac and Apple
Silicon. Those remote platform results must be checked separately.

`python tools/app_profile.py --output /tmp/app-profile.json` measures STEP loading,
feature extraction, registration and Boolean-free preview separately on repository
fixtures. On the Linux Python 3.12 development worker the initial measurements
were:

| Fixture | STEP load (s) | Features (s) | Self registration (s) | Preview (s) |
| --- | ---: | ---: | ---: | ---: |
| unit cube | 0.010 | 3.081 | 0.330 | 0.003 |
| unit cylinder | 0.007 | 4.607 | 0.262 | 0.018 |
| CubePyramid | 0.014 | 0.733 | 0.685 | 0.004 |

Extraction worker wall times were 4.39, 5.76 and 1.89 seconds respectively,
including process startup/imports, summaries and JSON serialization. Self-match
normalized RMS values were zero to 2.8e-17. These small fixtures do not establish
performance on industrial assemblies or network shares. The synthetic
10,000-entry test validates pagination, a maximum-200 shortlist, exhaustive
candidate coverage and a bounded allocation budget without extracting 10,000
CAD models. No Python-versus-native bottleneck assumption or algorithmic
optimization was made from these initial measurements.
