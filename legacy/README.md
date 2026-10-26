# Historical FreeCAD implementation

`shapematch.py` and `iv_viewer.py` preserve the original research implementation
before the standalone migration. They are excluded from the installed wheel.
They retain the original dependency and CLI limitations and are not the
standalone runtime or a supported alternative backend.

`tools/reference.py --backend freecad` imports this copy directly, disables
visualization and sound, and supplies NumPy compatibility aliases. This requires
a separately installed FreeCAD with its compatible native libraries, Pivy, and
the optional `reference` Python dependencies. The harness currently knows the
Debian/Ubuntu FreeCAD paths. No runtime code imports this archive.
