#
# PartCAD, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""What can be checked about this package, with and without OpenFOAM in the room.

Most of it can be checked without: the readers are ASCII parsing of a format
with two spellings of the same list, which is the part most likely to be quietly
wrong; the case writer is text; and a machine with no OpenFOAM, or the other
lineage of it, has to be told what to do rather than shown a traceback.

The last test is the analysis itself, on the example's pipe, against
Hagen-Poiseuille. It runs where OpenFOAM, gmsh and build123d all are -- in this
package's image, or on a workstation that has them -- and is skipped elsewhere.

Run with `pytest` from this directory, beside an installed `partcad` (for the
sandbox helpers the script imports; see `conftest.py`).
"""

import math
import os

import pytest

support = pytest.importorskip("cae_support", reason="needs a partcad with wrappers/cae_support.py")

import cfd_openfoam  # noqa: E402
import openfoam_common as foam  # noqa: E402

HEADER = "FoamFile\n{\n    version 2.0;\n    format ascii;\n    class %s;\n    object %s;\n}\n"
BANNER = (
    "/*--------------------------------*- C++ -*----------------------------------*\\\n"
    + "| banner |\n" * 8
    + "\\*---------------------------------------------------------------------------*/\n"
)


def _file(case, relative, cls, body):
    path = os.path.join(case, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(BANNER + HEADER % (cls, os.path.basename(relative)) + body)


@pytest.fixture
def case(tmp_path):
    """Two cells sharing one face, every boundary face in one of two patches."""
    root = str(tmp_path)
    _file(root, "constant/polyMesh/points", "vectorField", "4\n(\n(0 0 0)\n(1 0 0)\n(0 1 0)\n(0 0 1)\n)\n")
    # The first face on one line, the second written long, the way OpenFOAM
    # writes a face of more than ten points.
    _file(
        root,
        "constant/polyMesh/faces",
        "faceList",
        "3\n(\n3(0 1 2)\n4\n(\n0\n1\n3\n2\n)\n3(1 2 3)\n)\n",
    )
    _file(root, "constant/polyMesh/owner", "labelList", "3\n(\n0\n0\n1\n)\n")
    _file(
        root,
        "constant/polyMesh/boundary",
        "polyBoundaryMesh",
        "2\n(\n    walls\n    {\n        type patch;\n        nFaces 2;\n        startFace 1;\n    }\n"
        "    outlet0\n    {\n        type patch;\n        nFaces 0;\n        startFace 3;\n    }\n)\n",
    )
    _file(
        root,
        "7/U",
        "volVectorField",
        "dimensions [0 1 -1 0 0 0 0];\ninternalField nonuniform List<vector> 2((1 0 0) (0 2 0));\n"
        "boundaryField\n{\n    walls { type noSlip; }\n}\n",
    )
    _file(
        root,
        "7/phi",
        "surfaceScalarField",
        "dimensions [0 3 -1 0 0 0 0];\ninternalField nonuniform List<scalar> 1(0.5);\nboundaryField\n{\n"
        "    walls { type calculated; value uniform 0; }\n"
        "    outlet0 { type calculated; value nonuniform List<scalar> 3(1e-3 2e-3 -5e-4); }\n}\n",
    )
    _file(
        root, "7/p", "volScalarField", "dimensions [0 2 -2 0 0 0 0];\ninternalField uniform 4;\nboundaryField\n{\n}\n"
    )
    os.makedirs(os.path.join(root, "0"), exist_ok=True)
    os.makedirs(os.path.join(root, "3"), exist_ok=True)
    return root


def test_faces_are_read_whichever_way_each_was_written(case):
    assert foam.read_faces(case) == [[0, 1, 2], [0, 1, 3, 2], [1, 2, 3]]


def test_a_compact_face_list_is_read_too(tmp_path):
    root = str(tmp_path)
    _file(root, "constant/polyMesh/faces", "faceCompactList", "3\n(\n0\n3\n7\n)\n7\n(\n0 1 2 0 1 3 2\n)\n")
    assert foam.read_faces(root) == [[0, 1, 2], [0, 1, 3, 2]]


def test_points_owner_and_boundary(case):
    assert foam.read_points(case)[3] == (0.0, 0.0, 1.0)
    assert foam.read_owner(case) == [0, 0, 1]
    assert foam.read_boundary(case) == {"walls": ("patch", 1, 2), "outlet0": ("patch", 3, 0)}


def test_fields_uniform_and_not(case):
    assert foam.latest_time(case) == "7"
    assert foam.read_cells(case, "7", "U", 2) == [(1.0, 0.0, 0.0), (0.0, 2.0, 0.0)]
    assert foam.read_cells(case, "7", "p", 2) == [4.0, 4.0]


def test_patch_values_and_what_has_none(case):
    assert foam.read_patch(case, "7", "phi", "outlet0") == pytest.approx([1e-3, 2e-3, -5e-4])
    assert foam.read_patch(case, "7", "phi", "walls") == [0.0]
    assert foam.read_patch(case, "7", "U", "walls") is None
    assert foam.read_patch(case, "7", "phi", "nowhere") is None


def test_boundary_surface_paints_each_face_in_its_cell(case):
    vertices, triangles, values = foam.boundary_surface(case, [10.0, 20.0])
    # Face 1 (a quad, cell 0) is two triangles, face 2 (cell 1) one.
    assert len(triangles) == 3
    assert values == [10.0] * 4 + [20.0] * 3
    assert len(vertices) == 7


def test_walls_are_retyped_and_nothing_else(case):
    foam.retype_patches(case, ["walls"])
    assert foam.read_boundary(case)["walls"][0] == "wall"
    assert foam.read_boundary(case)["outlet0"][0] == "patch"


def test_a_machine_with_no_openfoam_is_told_what_to_install(monkeypatch):
    monkeypatch.delenv(foam.BASHRC_ENV, raising=False)
    monkeypatch.delenv("WM_PROJECT_DIR", raising=False)
    monkeypatch.setattr(foam, "BASHRC_CANDIDATES", ())
    monkeypatch.setattr(foam, "_conda_bashrc", lambda: None)
    with pytest.raises(support.ToolMissing) as raised:
        foam.find_bashrc()
    message = str(raised.value)
    # Both ways to have it, because a reader with a container runtime and one
    # without need different answers and the message cannot tell which it is
    # talking to.
    assert "dockerImage:" in message
    assert "apt install openfoam" in message
    assert foam.BASHRC_ENV in message


def test_a_bashrc_that_is_not_there_is_named(monkeypatch, tmp_path):
    monkeypatch.setenv(foam.BASHRC_ENV, str(tmp_path / "nowhere"))
    with pytest.raises(support.ToolMissing, match="nowhere"):
        foam.find_bashrc()


def test_the_case_holds_the_inlets_at_their_pressure(tmp_path):
    case = str(tmp_path)
    cfd_openfoam._write_case(case, {"iterations": 10}, {"inlet0": 63.66}, ["outlet0"], ["walls"], 900.0, 0.1)
    p = open(os.path.join(case, "0", "p")).read()
    # Kinematic: 63.66 Pa over 900 kg/m^3.
    assert "inlet0 { type fixedValue; value uniform 0.07073333333;" in p
    assert "outlet0 { type fixedValue; value uniform 0;" in p
    assert "walls { type zeroGradient; }" in p
    assert "walls { type noSlip; }" in open(os.path.join(case, "0", "U")).read()
    assert "nu 0.0001111111111;" in open(os.path.join(case, "constant", "transportProperties")).read()


def test_an_analysis_with_no_outlet_says_what_is_missing():
    request = {"boundary": [{"load": 1.0, "faces": [{"index": 0}], "area": 1.0}]}
    result = cfd_openfoam.process("/nonexistent.glb", request)
    assert result["success"] is False
    assert "outlet" in result["exception"]


def _openfoam_here():
    try:
        foam.environment()
        return True
    except support.ToolMissing:
        return False


@pytest.mark.skipif(not _openfoam_here(), reason="needs OpenFOAM")
def test_the_example_pipe_reads_hagen_poiseuille(tmp_path):
    """5 mN through a 10x100 mm bore of a light oil: 1.5625 mL/s, laminar."""
    build123d = pytest.importorskip("build123d")
    pytest.importorskip("gmsh")

    def disc(z):
        plane = build123d.Plane(origin=(0, 0, z), z_dir=(0, 0, 1))
        return [(plane * build123d.make_face(build123d.Circle(5))).wrapped]

    request = {
        "analysis": "cfd",
        "wrapped": build123d.Cylinder(5, 100).wrapped,
        "boundary": [
            {"port": "inlet", "location": [[0, 0, -50], [0, 0, 1], 0], "sketch": disc(-50), "load": 0.005},
            {"port": "outlet", "location": [[0, 0, 50], [0, 0, 1], 0], "sketch": disc(50), "outlet": True},
        ],
        "density": 900,
        "viscosity": 0.1,
        "mesh_size": 0.008,
    }
    assert support.prepare(request) == []
    path = str(tmp_path / "pipe.cfd.glb")
    result = cfd_openfoam.process(path, request)
    assert result["success"], result.get("exception")
    assert result["findings"] == []
    flow = float(result["warnings"][0].split("Flow rate ")[1].split(" ")[0])
    expected = 0.005 / (8 * math.pi * 0.1 * 0.1) * math.pi * 0.005**2
    assert flow == pytest.approx(expected, rel=0.02)
    assert os.path.getsize(path) > 0
