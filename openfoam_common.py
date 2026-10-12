#
# PartCAD, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""Finding OpenFOAM, running it, and reading what it wrote.

Everything in `cfd_openfoam.py` that is about OpenFOAM rather than about the
flow: where an installation is, how its environment is entered, how a case
directory is laid out, and how its ASCII output is read back without OpenFOAM
being importable from Python at all.

**Which OpenFOAM.** The OpenCFD/ESI lineage (openfoam.com, versions named like
`v2412`), which is the one Debian, conda-forge and the official `opencfd/*`
images ship, and which still has `simpleFoam`. The openfoam.org lineage replaced
its solvers with `foamRun` modules in version 11, takes a different set of
dictionaries, and is not supported -- finding only `foamRun` is reported as
that, rather than as no OpenFOAM at all.

**Where it is.** An OpenFOAM installation is not a program on `PATH` so much as
an environment: its `etc/bashrc` sets two dozen variables and only then are the
applications runnable. So what is looked for is that file -- named by
`PARTCAD_OPENFOAM_BASHRC`, or in the places each distribution puts it -- and the
environment it produces is captured once and handed to every command. A shell
that has already sourced it (`WM_PROJECT_DIR` set, `simpleFoam` on `PATH`) is
used as it stands.
"""

import glob
import os
import platform
import re
import shutil
import subprocess
import sys

# The meta-wrapper's directory is on 'sys.path' when PartCAD runs a script, and
# 'cae_support' lives there: what every implementation does around its solver.
import cae_support as support  # noqa: E402

BASHRC_ENV = "PARTCAD_OPENFOAM_BASHRC"

# Where each way of installing OpenFOAM puts its 'etc/bashrc', most specific
# first. Globs, because the version is in the directory name.
BASHRC_CANDIDATES = (
    # Debian and Ubuntu's own 'openfoam' package.
    "/usr/share/openfoam/etc/bashrc",
    # OpenCFD's .deb packages ('openfoam2412') and their Docker images.
    "/usr/lib/openfoam/openfoam*/etc/bashrc",
    # A source build, or an unpacked binary release.
    "/opt/openfoam*/etc/bashrc",
    "/opt/OpenFOAM*/etc/bashrc",
    os.path.expanduser("~/openfoam/openfoam*/etc/bashrc"),
    os.path.expanduser("~/OpenFOAM/OpenFOAM-*/etc/bashrc"),
)

# What the analysis runs. Checked for together, so that a broken installation
# is reported as the application it is missing.
APPLICATIONS = ("gmshToFoam", "polyDualMesh", "simpleFoam")

_environment = None


def _conda_bashrc():
    """The 'etc/bashrc' of an OpenFOAM conda-forge installed into this environment."""
    prefix = os.environ.get("CONDA_PREFIX") or sys.prefix
    candidate = os.path.join(prefix, "etc", "bashrc")
    return (
        candidate
        if os.path.isfile(candidate) and "openfoam" in open(candidate, errors="ignore").read().lower()
        else None
    )


def find_bashrc():
    """The 'etc/bashrc' of the OpenFOAM to run, or None when the shell already has one."""
    named = os.environ.get(BASHRC_ENV)
    if named:
        if os.path.isfile(named):
            return named
        raise support.ToolMissing("%s points at something that is not a file: %s" % (BASHRC_ENV, named))
    if os.environ.get("WM_PROJECT_DIR") and shutil.which("simpleFoam"):
        return None
    conda = _conda_bashrc()
    if conda:
        return conda
    for pattern in BASHRC_CANDIDATES:
        found = sorted(glob.glob(pattern))
        if found:
            # The newest of several side by side: names sort by version.
            return found[-1]
    raise support.ToolMissing(
        "OpenFOAM is not available on this %s-%s machine. Two ways to have it: run with a container "
        "runtime available, which gets the image named by 'dockerImage:' in partcad.yaml and carries "
        "OpenFOAM -- or install it here: 'apt install openfoam' on Debian and Ubuntu, OpenCFD's packages "
        "from openfoam.com, or 'conda install -c conda-forge openfoam' on x86_64 Linux. Then either source "
        "its etc/bashrc before starting PartCAD, or point %s at it. Searched: %s"
        % (platform.system(), platform.machine(), BASHRC_ENV, ", ".join(BASHRC_CANDIDATES))
    )


def environment():
    """The environment OpenFOAM's applications run in, captured once."""
    global _environment
    if _environment is not None:
        return _environment

    bashrc = find_bashrc()
    if bashrc is None:
        env = dict(os.environ)
    else:
        bash = shutil.which("bash")
        if not bash:
            raise support.ToolMissing("OpenFOAM's environment is a bash script, and there is no bash here")
        # Sourced in a shell of its own and read back as NUL-separated pairs,
        # so a variable holding a newline cannot split into two. The bashrc
        # prints nothing worth keeping and may return non-zero over an optional
        # component it could not find; whether it worked is whether the
        # applications are found afterwards.
        #
        # The path goes in through the environment, and the positional
        # parameters are emptied before the source: OpenFOAM's bashrc reads
        # its arguments as settings to apply ('WM_COMPILER=Clang'), and handed
        # its own path as one it does not finish.
        completed = subprocess.run(
            [bash, "-c", 'set --; source "$PARTCAD_OPENFOAM_SOURCE" >/dev/null 2>&1; env -0'],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            env=dict(os.environ, PARTCAD_OPENFOAM_SOURCE=bashrc),
            timeout=120,
        )
        env = {}
        for pair in completed.stdout.split(b"\0"):
            if b"=" in pair:
                key, _, value = pair.partition(b"=")
                env[key.decode(errors="replace")] = value.decode(errors="replace")
        if not env:
            raise support.ToolMissing("Sourcing %s produced no environment: %s" % (bashrc, completed.stderr[-500:]))

    path = env.get("PATH", "")
    missing = [name for name in APPLICATIONS if not shutil.which(name, path=path)]
    if missing:
        if shutil.which("foamRun", path=path):
            raise support.ToolMissing(
                "The OpenFOAM found here is the openfoam.org lineage, whose solvers are 'foamRun' modules: "
                "this package drives the OpenCFD lineage (openfoam.com), which has 'simpleFoam'. Install that "
                "one, or run with a container runtime, which gets the image this package names"
            )
        raise support.ToolMissing(
            "The OpenFOAM found here (%s) has no %s on its PATH" % (bashrc or "this shell's", ", ".join(missing))
        )
    _environment = env
    return env


def run(case, application, *arguments, log=None):
    """Run one OpenFOAM application on a case and return what it printed.

    Every application is run with '-case', and its output kept in the case as
    'log.<application>', which is where anybody debugging one would look first.
    A non-zero exit is a 'ToolFailed' carrying the end of that log: OpenFOAM's
    errors are a 'FOAM FATAL ERROR' block a few lines from the bottom, and
    that block is the whole of what a user needs to read.
    """
    env = environment()
    log = os.path.join(case, log or "log." + application)
    with open(log, "w") as f:
        completed = subprocess.run(
            [application, *arguments, "-case", case],
            stdout=f,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=case,
        )
    text = open(log, errors="replace").read()
    if completed.returncode != 0:
        tail = "\n".join(text.strip().splitlines()[-25:])
        raise support.ToolFailed("%s failed (exit %d):\n%s" % (application, completed.returncode, tail))
    return text


# --------------------------------------------------------------------------- #
# Writing a case                                                              #
# --------------------------------------------------------------------------- #

_HEADER = """FoamFile
{
    version     2.0;
    format      ascii;
    class       %s;
    object      %s;
}
"""


def write(case, relative, cls, body):
    """One dictionary of a case, with the header every OpenFOAM file starts with."""
    path = os.path.join(case, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(_HEADER % (cls, os.path.basename(relative)))
        f.write(body)
    return path


def retype_patches(case, walls):
    """Make the named patches walls: gmshToFoam makes every patch a plain 'patch'.

    A wall is not a patch with a no-slip condition on it: OpenFOAM computes wall
    distances and wall functions from the type, and 'noSlip' refuses a patch
    that is not one.
    """
    path = os.path.join(case, "constant", "polyMesh", "boundary")
    text = open(path).read()
    for name in walls:
        text = re.sub(
            r"(\n\s*%s\s*\{[^}]*?\btype\s+)patch;" % re.escape(name),
            r"\1wall;",
            text,
            count=1,
        )
    with open(path, "w") as f:
        f.write(text)


# --------------------------------------------------------------------------- #
# Reading what it wrote                                                       #
# --------------------------------------------------------------------------- #

_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


def _content(path):
    """A file's text without comments and without its FoamFile header."""
    text = open(path, errors="replace").read()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r"FoamFile\s*\{[^}]*\}", "", text, count=1)


def _class_of(path):
    """The 'class' a file's FoamFile header declares, after OpenFOAM's banner."""
    with open(path, errors="replace") as f:
        match = re.search(r"FoamFile\s*\{[^}]*?\bclass\s+(\w+)\s*;", f.read(4096))
    return match.group(1) if match else None


def _list_after(text, start=0):
    """The count and the body of the first 'N ( ... )' list at or after 'start'."""
    match = re.compile(r"(\d+)\s*\(").search(text, start)
    if not match:
        raise support.ToolFailed("OpenFOAM wrote a list this cannot read")
    count = int(match.group(1))
    depth, index = 1, match.end()
    while depth and index < len(text):
        character = text[index]
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        index += 1
    return count, text[match.end() : index - 1], index


def read_points(case):
    """The mesh's points, in metres, as a list of (x, y, z)."""
    count, body, _ = _list_after(_content(os.path.join(case, "constant", "polyMesh", "points")))
    points = [tuple(float(v) for v in m) for m in re.findall(r"\((%s)\s+(%s)\s+(%s)\)" % ((_NUMBER,) * 3), body)]
    return points[:count]


def read_faces(case):
    """The mesh's faces, each a list of point indices."""
    text = _content(os.path.join(case, "constant", "polyMesh", "faces"))
    if _class_of(os.path.join(case, "constant", "polyMesh", "faces")) == "faceCompactList":
        # Two lists: where each face starts in the second, then every face's
        # points end to end.
        count, offsets, end = _list_after(text)
        _, labels, _ = _list_after(text, end)
        offsets = [int(v) for v in offsets.split()]
        labels = [int(v) for v in labels.split()]
        return [labels[offsets[k] : offsets[k + 1]] for k in range(count - 1)]
    count, body, _ = _list_after(text)
    # By token rather than by line: a face of up to ten points is written as
    # '4(1 2 3 4)' on one line, and a longer one -- which a polyhedral dual
    # mesh has plenty of -- as its count, then '(' and its points on lines of
    # their own.
    tokens = re.findall(r"\(|\)|[^\s()]+", body)
    faces, index = [], 0
    while index < len(tokens) and len(faces) < count:
        size = int(tokens[index])
        faces.append([int(token) for token in tokens[index + 2 : index + 2 + size]])
        index += size + 3
    return faces


def read_owner(case):
    """The cell each face belongs to."""
    count, body, _ = _list_after(_content(os.path.join(case, "constant", "polyMesh", "owner")))
    return [int(v) for v in body.split()][:count]


def read_boundary(case):
    """Patch name -> (type, first face, number of faces)."""
    text = _content(os.path.join(case, "constant", "polyMesh", "boundary"))
    patches = {}
    for name, body in re.findall(r"([A-Za-z_][\w.-]*)\s*\{([^}]*)\}", text):
        kind = re.search(r"\btype\s+(\w+);", body)
        faces = re.search(r"\bnFaces\s+(\d+);", body)
        start = re.search(r"\bstartFace\s+(\d+);", body)
        if kind and faces and start:
            patches[name] = (kind.group(1), int(start.group(1)), int(faces.group(1)))
    return patches


def _values(body, vector):
    if vector:
        return [tuple(float(v) for v in m) for m in re.findall(r"\((%s)\s+(%s)\s+(%s)\)" % ((_NUMBER,) * 3), body)]
    return [float(v) for v in re.findall(_NUMBER, body)]


def read_cells(case, time, name, count):
    """A field's value in every cell, as written at 'time'."""
    text = _content(os.path.join(case, time, name))
    vector = _class_of(os.path.join(case, time, name)) == "volVectorField"
    uniform = re.search(r"internalField\s+uniform\s+(\([^)]*\)|%s)\s*;" % _NUMBER, text)
    if uniform:
        value = _values(uniform.group(1), vector)
        return [value[0]] * count
    start = re.search(r"internalField\s+nonuniform\s+List<\w+>", text)
    if not start:
        raise support.ToolFailed("The field '%s' at time %s holds no values this can read" % (name, time))
    _, body, _ = _list_after(text, start.end())
    return _values(body, vector)[:count]


def read_patch(case, time, name, patch):
    """A field's values on one patch, as written at 'time', or None when it wrote none."""
    text = _content(os.path.join(case, time, name))
    block = re.search(r"\n\s*%s\s*\{" % re.escape(patch), text)
    if not block:
        return None
    rest = text[block.end() :]
    value = re.search(r"\bvalue\s+(uniform\s+(%s)|nonuniform\s+List<\w+>)" % _NUMBER, rest[: rest.find("}") + 1])
    if not value:
        return None
    if value.group(2) is not None:
        return [float(value.group(2))]
    _, body, _ = _list_after(rest, value.end())
    return _values(body, False)


def latest_time(case):
    """The newest time directory a solver wrote, as its name."""
    times = [entry for entry in os.listdir(case) if re.fullmatch(r"[0-9.eE+-]+", entry) and entry != "0"]
    if not times:
        raise support.ToolFailed("OpenFOAM wrote no result at all")
    return max(times, key=float)


def boundary_surface(case, per_cell, patches=None):
    """The boundary of the mesh as triangles, each face in the colour of its cell.

    A cell-centred solver has no value at a point of the boundary, only in the
    cell behind it -- so each boundary face is drawn in its owner cell's value,
    with points of its own, which is the honest picture of what was computed.
    """
    points = read_points(case)
    faces = read_faces(case)
    owner = read_owner(case)
    vertices, triangles, values = [], [], []
    for name, (_kind, start, count) in read_boundary(case).items():
        if patches is not None and name not in patches:
            continue
        for face_index in range(start, start + count):
            face = faces[face_index]
            value = per_cell[owner[face_index]]
            base = len(vertices)
            vertices.extend(points[k] for k in face)
            values.extend([value] * len(face))
            for k in range(1, len(face) - 1):
                triangles.append((base, base + k, base + k + 1))
    return vertices, triangles, values
