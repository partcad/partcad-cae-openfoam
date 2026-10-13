# partcad-cae-openfoam

Steady, laminar, incompressible flow analysis for [PartCAD](https://github.com/partcad/partcad), by
[OpenFOAM](https://www.openfoam.com/). It implements `pc cae cfd`.

```shell
pc cae cfd :pipe
pc cae cfd -i //pub/feature/cae/openfoam:cfd :pipe   # this implementation, for one run
pc test -f cfd                                      # as a check that fails on any finding
```

## What it computes

The part is read as the **fluid volume**: a duct is analysed by declaring its bore as a part, not the
casting around it. Its `cfd:` section says where the flow goes in and out:

```yaml
sketches:
  bore:
    type: basic
    circle: 5                # the bore, seen end on

interfaces:
  pipe-inlet:
    ports:
      inlet:
        sketch: bore         # the surface this port stands for
  pipe-outlet:
    ports:
      outlet:
        sketch: bore

parts:
  pipe:
    type: build123d
    implements:
      pipe-inlet: [[0, 0, -50], [0, 0, 1], 0]   # Z out of the end face
      pipe-outlet: [[0, 0, 50], [0, 0, 1], 0]
    cfd:
      load:
        pipe-inlet: 0.005 N  # what drives the flow in
      outlet:
        - pipe-outlet        # where it leaves, at the reference pressure
```

* **Inlets** are the ports under `load:`. The force over the area the port's sketch covers is the
  pressure the inlet is held at, above the outlets: 5 mN over a 10 mm bore is 63.66 Pa.
* **Outlets** are the ports under `outlet:`, held at the reference pressure.
* **Every other surface is a wall**, no-slip. `fix:` may name walls too; it changes nothing.

A port is a coordinate frame; its sketch is the surface. PartCAD imprints each sketch on the part before
this package runs and hands it the faces each port covers, so an inlet smaller than the face it sits on
is exactly as large as its sketch. A port with no sketch covers the face it lies on.

What comes back:

* `<part>.cfd.glb`, the part's boundary coloured by pressure, which the Digital Thread's CFD tab shows;
* a measurement line in the log -- flow rate, mean outlet speed, peak speed, Reynolds number, the
  pressure each inlet was driven at, cell count, iterations to a steady state;
* **findings**, which fail `pc test`: a flow that did not settle, a flow fast enough to be turbulent
  (Reynolds number past 2300, where this laminar analysis stops applying), a speed past `max_velocity`,
  and a flow rate short of `min_flow_rate` when that is set.

### The pipeline

1. gmsh meshes the part into tetrahedra, each condition's faces a named group and the rest `walls`
   (PartCAD's `cae_support.mesh()`).
2. `gmshToFoam` reads the mesh; the walls are retyped as walls.
3. `polyDualMesh` turns the tetrahedra into their dual polyhedra. A cell-centred solver on raw
   tetrahedra is poor: 13% low on the example pipe's flow rate, against 0.4% on the dual of the same
   mesh.
4. `simpleFoam` marches to a steady state (SIMPLEC, laminar).
5. The result is read back from the case's ASCII files -- no OpenFOAM library in Python.

### Measured

`examples/feature_cae` in the PartCAD repository has a pipe whose answer is a formula. Through
`pc cae cfd :pipe`:

| | Hagen-Poiseuille | this package |
| --- | --- | --- |
| Flow rate | 1.5625 mL/s | 1.556 mL/s (-0.4%) |
| Centreline (peak) speed | 0.03979 m/s | 0.0398 m/s |
| Reynolds number | 1.79 | 1.78 |

The same numbers from the image (Debian's OpenFOAM v1912, 14,643 cells, 19 s) and from a workstation's
conda-forge OpenFOAM v2412 (14,704 cells).

## Parameters

Set in a `cae: cfd:` section of the package that uses this one, or of the part:

| parameter | default | meaning |
| --- | --- | --- |
| `mesh_size` | 0.05 | largest tetrahedron, as a fraction of the part's largest dimension |
| `density` | 1.204 | kg/m^3 (air) |
| `viscosity` | 1.82e-5 | dynamic viscosity, Pa*s (air) |
| `iterations` | 2000 | SIMPLE steps allowed to reach a steady state |
| `residual` | 1e-6 | initial residual below which it counts as steady |
| `poly_dual` | true | solve on the dual polyhedra of gmsh's tetrahedra |
| `feature_angle` | 70 | degrees past which an edge stays sharp in the dual |
| `max_velocity` | 50 | m/s; a faster flow is a finding |
| `min_flow_rate` | unset | m^3/s; a smaller flow is a finding |

## What it needs

**A container runtime, or OpenFOAM installed.**

With a container runtime PartCAD runs the analysis in the image `partcad.yaml` names: PartCAD's Python
base image plus Debian's `openfoam` and `python3-gmsh`, for amd64 and arm64. Nothing to install.

Without one, PartCAD runs it in a conda or venv sandbox on the host, and OpenFOAM has to be there. It is
found by its `etc/bashrc`, in this order:

1. `PARTCAD_OPENFOAM_BASHRC`, naming the file;
2. the shell PartCAD's daemon was started from, if it has sourced one already (`WM_PROJECT_DIR` set and
   `simpleFoam` on `PATH`);
3. an OpenFOAM installed into the sandbox's own conda environment;
4. `/usr/share/openfoam/etc/bashrc` (Debian, Ubuntu), `/usr/lib/openfoam/openfoam*/etc/bashrc`
   (OpenCFD's packages), `/opt/openfoam*/`, `~/openfoam/openfoam*/`, `~/OpenFOAM/OpenFOAM-*/`.

The daemon is what runs the analysis, so a variable set in your shell reaches it only when the daemon is
started from that shell: `pc daemon stop` first.

**Which OpenFOAM.** The OpenCFD lineage (openfoam.com, `v2412` and the like) -- the one Debian,
conda-forge and the `opencfd/*` images ship. The openfoam.org lineage replaced `simpleFoam` with
`foamRun` modules in version 11 and is not supported; finding only that one is reported as such.

gmsh comes from pip everywhere pip has a wheel, which is everywhere except 64-bit ARM Linux. There, use
the image, or install `python3-gmsh` or conda-forge's `gmsh` and make it visible to the sandbox.

### The pin

`dockerImage:` names the image by a hash of what it is built from -- the Dockerfile, `verify_image.py`
and the Python version -- not by a version. Run `./image-tag.sh` after changing any of them and update the
pin; the `Image` workflow refuses to build if the two disagree, and publishes `<tag>-amd64` and
`<tag>-arm64` on a push to `main`.

## Tests

```shell
pytest
```

beside an installed `partcad` (the script imports PartCAD's sandbox helpers; `conftest.py` finds them).
The readers, the case writer and the messages a machine without OpenFOAM gets are checked everywhere; the
example pipe is solved and compared with Hagen-Poiseuille where OpenFOAM, gmsh and build123d are all
present, and skipped elsewhere.

To keep a case for inspection, set `PARTCAD_OPENFOAM_CASE` to an empty directory before the daemon
starts: the analysis writes its case there instead of into a temporary directory.

## License

Apache License 2.0. OpenFOAM is GPL-3.0 and is not part of this package: the image installs Debian's
build of it, whose sources Debian publishes.
