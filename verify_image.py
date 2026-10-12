#!/usr/bin/env python3
#
# PartCAD, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""Whether this image can actually run the flow analysis.

Run at build time, so that an image which cannot is never published. The base
image's own `verify.py` checks the contract PartCAD talks to a container
through; this checks the two things this image adds, the way the analysis uses
them: gmsh imported by *this* interpreter, and OpenFOAM's environment entered
and its applications run the way `openfoam_common` enters and runs them.
"""

import os
import shutil
import subprocess
import sys


def main() -> int:
    failures = []

    try:
        import gmsh

        gmsh.initialize()
        try:
            gmsh.model.add("verify")
            gmsh.model.occ.addCylinder(0, 0, 0, 0, 0, 1, 0.1)
            gmsh.model.occ.synchronize()
            gmsh.model.mesh.generate(3)
            nodes, _, _ = gmsh.model.mesh.getNodes()
            print("gmsh %s meshed a cylinder into %d nodes" % (gmsh.option.getString("General.Version"), len(nodes)))
            if len(nodes) == 0:
                failures.append("gmsh meshed a cylinder into no nodes at all")
        finally:
            gmsh.finalize()
    except Exception as e:
        failures.append("gmsh could not mesh anything from this interpreter (%s)" % e)

    bashrc = "/usr/share/openfoam/etc/bashrc"
    if not os.path.isfile(bashrc):
        failures.append("OpenFOAM's environment is not at %s" % bashrc)
    else:
        completed = subprocess.run(
            ["bash", "-c", 'set --; source "$PARTCAD_OPENFOAM_SOURCE" >/dev/null 2>&1; simpleFoam -help'],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=dict(os.environ, PARTCAD_OPENFOAM_SOURCE=bashrc),
            timeout=120,
        )
        if completed.returncode != 0:
            failures.append("simpleFoam -help failed: %s" % (completed.stdout + completed.stderr)[-500:])
        else:
            print("simpleFoam runs: %s" % completed.stdout.strip().splitlines()[0])
    for application in ("gmshToFoam", "polyDualMesh", "simpleFoam"):
        if not shutil.which(application):
            failures.append("%s is not on PATH" % application)

    if failures:
        print("\nThis image cannot run the flow analysis:")
        for failure in failures:
            print("  * %s" % failure)
        return 1
    print("\nOpenFOAM and the mesher are both usable from this interpreter.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
