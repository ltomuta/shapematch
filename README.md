
# shapematch

**shapematch** identifies similar 3D CAD geometry shapes that have been rotated, translated or scaled, returning a measure of similarity. A set of consistent affine-invariant feature points are extracted from CAD surfaces for comparison.

Humans can look at a scene and immediately recognise moved or similar objects, computers cannot. Surprisingly, there is no obvious method to identify similarity between numerical abstract representations of geometry. 
This has real world ramifications, one [example](http://web.archive.org/web/20230309122240/https://www.brookings.edu/techstream/understanding-the-errors-introduced-by-military-ai-applications/) is a semi-autonomous missile defence system incorrectly classifying a friendly military jet as an enemy missile.

A Computer Aided Design representation of a component cannot be immediately identified as a transformed representation of another identical component. The transformations in question are scaling, rotation, and translation, a rigid affine transform. 

There is no universal representation of objects within abstract numerical systems that allows comparison of these geometric representations between different systems. 
As a result, there is no reliable method to test similar shapes represented in different CAD applications. The method used here was devised to support machine shape comparison, in turn allowing automated testing and mapping of CAD API functionality between different CAD systems.

## Motivation

Modern parametric feature CAD systems enable designers to create shape geometries from a set of ordered geometry concepts or features.   
These features might be something like a hole or a reference plane, each of which are defined relative to one another with a set of parameters that specify their form. 
The main advantage of defining an object as a structured collection of features is that it allows the geometry to be automatically recreated once any of these feature parameters are modified. 
Before this innovation, the surface of a CAD would have to be edited to accommodate changes of design features. 
While these systems represent labour-saving innovation, there is no universal definition of parametric geometric features and their associated parameters. 
This has meant that designs are not readily moved between different commercial systems, a surface model may be transferred but much of the parametric feature definition that gives these models their utility is lost. 

It is possible to take a surface model, and attempt to guess the associated parametric features used by a particular CAD software to recreate the same surface. 
This approach requires significant labour in identifying and mapping individual CAD features as commercial systems typically have hundreds of API functions dealing with their feature representations. 
Another strategy is to attempt to create a mapping between parametric features within different systems, so that the set of API function calls to generate and modify the features of a models could be mapped to a set of API calls in a different CAD system which would still generate a geometric model with an identical surface. 
Mapping the similarity between CAD API function calls to model parametric features and their derived surface geometry would be more economic and feasible if the process could be automated to some extent.   
The barrier to automating this process is the ease with which one surface geometry in one CAD system can be tested with a comparable surface geometry in a second CAD system. 
If the similarity between surface geometries represented within heterogeneous CAD systems could be determined through a universal algorithm, the task of mapping CAD feature API might be partially or fully automated.   

This proof-of-concept package demonstrates and tests an approach to measuring the similarity between CAD surface geometries, independent of model orientation, position or scale. 
It can be shown that the method may be extended to determine a numerical similarity value between all affine transformations of surface boundary objects. 
FreeCAD is chosen because of its open code base, active community and convenient Python interfaces. It also allows cheaper reproduction of results than the use of commerical CAD products.

The research also demonstrated a Genetic Algorithm using this geometry similarity test to map parameters between select feature geometry API, see published documentation. 
The numerical similarity value between API generated shape models is used as the fitness function to minimise.



### Feature Point Representation for geometry comparison

The important concepts of this project were to firstly test a *minimal set of features that could identify geometric similarity between 3D shapes*, allowing reliable identification despite rotation, translation and scaling. 

The second requirement was to determine a *minimal routine for identifying these feature points*, to extract these points with a minimum knowledge of any particular CAD system.

If unique ordered feature points can be assigned to an object surface geometry, then SVD transformation will enable rapid and reliable mapping between objects with similar point sets, independent of the CAD environment. This minimal method relies on Cartensian points returned from a point projection to the boundary surfaces of the model undergoing testing.

An example of a geometry feature is a corner, represented by the maximum local displacement from a local geometric centroid of the shape. This corner feature can be found from an iterative hill-searching algorithm, converging on the furthest point relative to a centroid using only the intersections of centroid projections to the boundary surface.

Extending this simple routine, there are local minima, then radially symmetric features (e.g. the ends of cylinders), which can be also subdivided into maxima such as a ridge represented by a point at the feature centre, and minima feature, such as the centre of an hourglass. Finally, spheres have their own central point. With these 5 distinguishing features, unique feature point representations have been created from all surface geometries tested.

<img src=./docs/images/FeatureTypes.png alt= “” width=60% height=60%>

*Geometric surface showing feature points, ordered from top to bottom as,*
- *yellow: feature maxima centre,* 
- *magenta:feature minima point,*
- *green: feature minima centre,*
- *orange: sphere centre,*
- *green: feature minima centre,*
- *blue: feature maxima centre*


Increasing geometric complexity of a shape provides a more unique model representation, with more individual feature points and consequently a more reliable comparison. This is convenient as it allows a limited model set to test the applicability of the method. The original research used the primitive shape benchmark library published by the US National Design Repository to allow direct comparison with other techniques (see below).

The points in this feature point representation are preserved through object rotation, translation and uniform scaling operations. A unique helical ordering of these points allows the rotational matrix and translation vector to be derived from a Singular Value Decomposition operation, this represents a significant improvement in accuracy and speed over comparable techniques. 

<img src=./docs/images/ChiralityOrder1.png alt= “” width=50% height=50%>

*Find the most unique point, create an axis from this point to the centroid, then organise all other points in a spiral around this axis.*

An SVD decomposition of feature points in a source object and a target object returns a rotation and translation transform. By recording the deviation of points sampled from the source surface and transformed to the target surface, it is possible to generate an accurate statistical probability of shape similarity.

<img src=./docs/images/Cartoon5.png alt= “” width=50% height=50%>

*Transform random source points (stars) to target model, and test deviation from model surface.*

As the displacement of feature points from the local model centroid are preserved under scaling, rotation and translation (footnote affine transformation also includes shearing along various axes, and extra processing step), these values can be formed into a histogram to make rapid comparison with similar histograms of other geometries. These feature points are further ordered by type, whether representing surface maxima, surface minima, centrepoints of rotationally-symmetric features or spheres.

<img src=./docs/images/AlgFlowchart.png alt= “” width=60% height=60%>

*Shape matching algorithm*

Further details available at,

## Documentation

[Borland, T.D., 2019. An automated method mapping parametric features between computer aided design software (Doctoral dissertation, Brunel University London).](https://bura.brunel.ac.uk/bitstream/2438/19184/1/FulltextThesis.pdf)

## Installation

The standalone headless release runs on Linux x86-64 with Python 3.11 or 3.12.
It uses Open CASCADE through `cadquery-ocp==8.0.1.1.0`; FreeCAD, Qt, Pivy,
and a display server are not required. OCP installs native binary wheels and
brings substantial dependencies, including VTK. Use a Linux distribution
compatible with its manylinux_2_28 wheels (glibc 2.28 or newer).

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
```

For development, install `python -m pip install -e '.[test]'` and run
`python -m pytest`. Windows, macOS, and other Python versions are not yet
validated for this release. Benchmark STEP files stay in the repository;
they are not bundled into the installed wheel.

## Usage

Run these commands from the repository checkout after installation:

```bash
shapematch --help
shapematch singlePRtest -I data/primitives -O /tmp/shapematch/features --seed 0
shapematch multiPRtest -I /tmp/shapematch/features -O /tmp/shapematch/results --seed 0
```

`python -m shapematch` accepts the same arguments. Without installation, use
`PYTHONPATH=src python -m shapematch ...`. `multitest` is an alias for
`multiPRtest`. The default seed is `0`; input discovery is sorted, and random
model selection tries at most 20 candidates before reporting failure.

`singlePRtest` extracts three feature representations: a source, a randomly
transformed positive model of the same STEP geometry, and a negative model
from a different STEP source. `-I` searches STEP files recursively. For an
explicit comparison, supply STEP files or new JSON caches:

```bash
shapematch singlePRtest \
  -sf data/primitives/miscellanea/tetrahedron.scaled.stp \
  -nf data/primitives/cube/unit_cube.stp \
  -O /tmp/shapematch/features --seed 0
```

Use `-pf` to provide the positive model explicitly. Cache inputs preserve their
stored transforms; STEP inputs receive seeded random transforms. `-v` enables
research diagnostics. Output directories are created automatically.

Each single run writes three `*.features.json` caches, `comparisons.csv`, and
`comparisons.json`. `-of filename.csv` changes the CSV and companion JSON name.
`multiPRtest` compares all JSON feature caches under `-I`, writing a CSV error
matrix and `rankings.json`; `--json_file` (or the historical `-obf`) changes the
ranked JSON filename. Result documents in input folders are ignored.

Results report histogram similarity and **bidirectional closest-surface RMS
error in millimeters**. `forward_error` and `reverse_error` report the two
directions separately; both include hole walls and interior surfaces.
`normalized_alignment_error` divides the placed-frame error by the target's
bounding-box diagonal in the same frame. Random benchmark scaling is undone
when reporting millimeters.

Histogram thresholds (`0.5` single, `0.9` batch) now provide the informational
`histogram_passed` field. They do not prevent geometric alignment: sparse or
unstable feature signatures can miss genuinely similar parts. Registration
tests proper rotations and positive uniform scales, with geometry-based
candidates and closest-surface refinement when features are insufficient.
`alignment_found` means a finite fit was computed, not that the parts are
identical. The distances are sampled estimates, not maximum deviation bounds
or manufacturing pass/fail decisions. See [alignment details](docs/alignment.md).

### Cache migration and visualization

Old dill `.obj` files are not supported. Regenerate them by running
`singlePRtest` against the original STEP files. New caches contain only
versioned JSON feature data, transforms, a relative STEP path, and a SHA-256
source hash. Geometry is rebuilt from the STEP source when loading a cache.
Move caches and STEP files together while preserving relative paths; missing
or changed sources require regeneration.

Interactive HTML overlays are available with the optional plotting dependency:

```bash
python -m pip install '.[html]'
shapematch visualize /tmp/comparison/comparisons.json -o /tmp/comparison/comparison.html
```

Open the HTML file in a WebGL-capable browser. Both meshes and the viewer are
embedded; no server or internet connection is needed. Orbit, zoom, hide either
part, adjust transparency, and switch between recovered alignment and original
STEP placement. The original-placement view applies no recovered scaling.
RMS measurements describe recovered alignment and are hidden in the original view.

Select **Display → Boolean regions** to color and toggle the common volume,
material only in A, and material only in B. A is the source; B is the compared
part. The viewer includes CAD volumes in mm³, a **Differences only** button,
separate opacity controls, and three color schemes. Boolean regions are computed
for both original placement and recovered alignment before export; no server is
needed to switch between them.

The Boolean view also has a positional tolerance selector in millimeters, with
precomputed numerical-baseline, 0.001, 0.01, and 0.1 mm levels. Use
`--boolean-tolerance 0.01` with `visualize` or `singlePRtest --html` to select a
custom initial value (also included in the file). The default is the numerical
baseline. This can suppress skins from near-coincident geometry; it does not
change the fit or RMS score. Use original placement for already aligned parts.

To export while running a new comparison, append `--html` to `singlePRtest`.
It writes `comparison.html` under `-O`; `--html filename.html` changes the name.
Use `--placement original` to start in the original STEP coordinates. By default,
`visualize` shows your two input files; `--pair positive` shows the self-control.
Old reports without a saved transform are refitted from their feature caches
once. See [HTML export details](docs/html.md).

Legacy scenegraph flags produce an explanatory error, and the
`iv_viewer` entry point is no longer installed. Original FreeCAD-based source
and viewer code are archived under `legacy/`; bundled `.iv` and `.obj` examples
are preserved as historical artifacts. See [migration notes](docs/standalone.md)
for intentional corrections and reference validation.

### CAD source data 

The NIST/Drexel CAD primitive shape benchmark library was used in the published research attached to these routines as it was designed for shape matching algorithm testing, enabling direct comparison against other published methods. Unfortunately this Primitive Models Classification archive disappeared from its hosted site at 

http://web.archive.org/web/20181105065515/http://edge.cs.drexel.edu/repository/

https://www.nist.gov/publications/nist-design-repository-project-project-overview-and-implementation-design

It is supplied for reference within the local */data/primitives* subfolder, under the [NIST license](https://www.nist.gov/open/copyright-fair-use-and-licensing-statements-srd-data-software-and-technical-series-publications#software)

https://github.com/usnistgov/engineering-design-models/blob/master/LICENSE.md

https://github.com/usnistgov/engineering-design-models

A number of other CAD libraries exist for similar benchmark comparative purposes, e.g.

https://www.nist.gov/publications/repository-design-process-planning-and-assembly

https://github.com/bharadwaj-manda/CADNET-Dataset

FreeCAD, the CAD program used, will import a wide variety of CAD models. The only requirement is that a point projected to the imported geometry will register an intersection. See,

https://wiki.freecad.org/Import_Export

Note that the native STEP CAD format allows rapid extraction of the feature points used in this schema, however the intent of the project was to demonstrate that a minimal set of API features could be used within any CAD program to query any CAD shape, namely the projection 

## Screenshots

<img src="./docs/images/Screenshot1.png" alt= "“”" width="60%" height="60%">

*Screenshot showing iv_viewer displaying matching between two similar shapes. The marked points show tests for deviation between transformed random surface points from the source shape, projected to the surface of the target shape.* 

<img src="./docs/images/Screenshot2.png" alt= "“”" width="60%" height="60%">

*Representation of a source shape (oblong - green), transformed to a positive similar shape (oblong - green), and a negative dissimilar shape (oblate spheroid - red).*

## Project Status

This code supported doctoral research into automated CAD API mapping. 
It has been ported from the original CAD geometry engine, McNeel Rhinoceros 5.0 which had a convenient COM Rhinoscript interface to an actively maintained FLOSS 3D CAD.

## Contributing

To contribute to **shapematch** create a fork on GitHub. Clone your fork, make some changes, and submit a pull request.

## License

[LGPLv3](https://choosealicense.com/licenses/lgpl-3.0/)

[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

![Python](https://img.shields.io/badge/python-3670A0?style=for-the-badge&logo=python&logoColor=ffdd54)

![Ubuntu](https://img.shields.io/badge/Ubuntu-E95420?style=for-the-badge&logo=ubuntu&logoColor=white)

![Windows](https://img.shields.io/badge/Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white)
