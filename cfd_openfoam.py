#
# PartCAD, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""`pc cae cfd`, by OpenFOAM: steady, laminar, incompressible flow through one part.

The part is read as the **fluid volume** -- the space the fluid is in, not the
wall around it: a duct is analysed by declaring its bore as a part.

The boundary conditions are the `cfd:` keys, applied to the surfaces their ports
cover (PartCAD has turned each port's sketch into faces before this runs):

* `load:` names where the flow is **driven** in, as a force. A force over the
  surface it acts on is a pressure, and a pressure difference is what drives an
  incompressible flow -- so a `load:` of 5 mN on an inlet of 78.5 mm^2 holds
  that inlet at 63.7 Pa above the outlet.
* `outlet:` names where it **leaves**, held at the reference pressure.
* Every other surface is a **wall**: no slip, the fluid is held still against
  it. `fix:` may name walls as well; it changes nothing, since everything that
  is not an inlet or an outlet is one already.

The pipeline: gmsh meshes the part into tetrahedra with each condition's faces
named; `gmshToFoam` reads that; `polyDualMesh` turns the tetrahedra into their
dual polyhedra, because a cell-centred solver on raw tetrahedra is a poor
solver (13% low on the example's pipe, against 0.4% on the same mesh's dual);
`simpleFoam` marches to a steady state; and the result is read back from the
case's ASCII files, with no OpenFOAM library in Python at all.
"""

import math
import os
import re
import sys
import tempfile

# `runpy.run_path()` leaves the script's directory off `sys.path`, so the
# sibling module below is not importable without saying where it is.
sys.path.append(os.path.dirname(__file__))
import openfoam_common as foam  # noqa: E402
from openfoam_common import support  # noqa: E402

ERROR = "error"
WARNING = "warning"
INFO = "info"

M2_PER_MM2 = 1e-6

# Where laminar flow ends in a pipe, and the analysis with it: past this the
# answer is a laminar solution to a problem that is not laminar.
TURBULENT_REYNOLDS = 2300.0


def process(path, request):
    try:
        return _analyse(path, request)
    except (support.ToolMissing, support.ToolFailed) as e:
        return support.failed(e)


def _analyse(path, request):
    findings = []
    density = float(request.get("density", 1.204))
    viscosity = float(request.get("viscosity", 1.82e-5))

    inlets, inlet_records, inlet_empty = support.condition_groups(request, "load", "inlet")
    outlets, outlet_records, outlet_empty = support.condition_groups(request, "outlet", "outlet")
    for record in inlet_empty + outlet_empty:
        findings.append(_nothing_covered(record))
    if not inlet_records:
        raise support.ToolFailed(
            "'cfd:' drives nothing: name an inlet under 'load:', with the force behind the flow, on a port "
            "whose sketch lies on a surface of the part"
        )
    if not outlet_records:
        raise support.ToolFailed(
            "'cfd:' names no outlet, so the flow has nowhere to go and the problem has no downstream pressure "
            "reference: name where it leaves under 'outlet:', on a port whose sketch lies on a surface of the part"
        )

    with tempfile.TemporaryDirectory() as case:
        # A directory to keep the case in, for whoever is debugging one by hand.
        case = os.environ.get("PARTCAD_OPENFOAM_CASE") or case
        msh = os.path.join(case, "part.msh")
        mesh = support.mesh(
            request,
            {**inlets, **outlets},
            size=float(request.get("mesh_size", 0.05)),
            order=1,
            rest="walls",
            files=[msh],
        )
        if mesh.unmatched:
            findings.append(
                {
                    "severity": INFO,
                    "message": "%d surface(s) of the mesh match no face of the part and are treated as walls"
                    % len(mesh.unmatched),
                }
            )

        walls = ["walls"]
        pressures = {}
        for index, record in enumerate(inlet_records):
            area = float(record["area"]) * M2_PER_MM2
            pressures["inlet%d" % index] = float(record["load"]) / area
        # The dictionaries first: every OpenFOAM application, the mesh
        # converter included, starts by reading the case's controlDict.
        _write_case(case, request, pressures, list(outlets), walls, density, viscosity)
        foam.run(case, "gmshToFoam", msh)
        foam.retype_patches(case, walls)
        if request.get("poly_dual", True):
            foam.run(
                case, "polyDualMesh", "%g" % float(request.get("feature_angle", 70)), "-overwrite", "-concaveMultiCells"
            )

        log = foam.run(case, "simpleFoam")
        iterations = _iterations(log)
        time = foam.latest_time(case)
        cell_count = max(foam.read_owner(case)) + 1
        speeds = [math.sqrt(x * x + y * y + z * z) for x, y, z in foam.read_cells(case, time, "U", cell_count)]
        kinematic = foam.read_cells(case, time, "p", len(speeds))
        pressure = [value * density for value in kinematic]
        flow = 0.0
        for name in outlets:
            flow += sum(foam.read_patch(case, time, "phi", name) or [0.0])

        vertices, triangles, values = foam.boundary_surface(case, pressure)
        support.write_glb(path, vertices, triangles, values)

    outlet_area = sum(float(record["area"]) for record in outlet_records) * M2_PER_MM2
    mean_speed = flow / outlet_area if outlet_area else 0.0
    hydraulic_diameter = math.sqrt(4.0 * outlet_area / math.pi) if outlet_area else 0.0
    reynolds = density * abs(mean_speed) * hydraulic_diameter / viscosity
    peak_speed = max(speeds, default=0.0)

    if iterations is None:
        findings.append(
            {
                "severity": WARNING,
                "message": (
                    "The flow did not settle within %d iterations, so these numbers are not a steady state. "
                    "Raise 'iterations', or refine the mesh where the flow turns."
                    % int(request.get("iterations", 2000))
                ),
            }
        )
    if reynolds > TURBULENT_REYNOLDS:
        findings.append(
            {
                "severity": WARNING,
                "message": (
                    "The flow is likely turbulent (Reynolds number about %.0f, past %.0f), and this analysis is "
                    "laminar: the flow rate it reports is not what a turbulent flow would carry."
                    % (reynolds, TURBULENT_REYNOLDS)
                ),
            }
        )
    findings.extend(_verdict(peak_speed, flow, request))

    applied = ", ".join("%.4g Pa" % value for value in pressures.values())
    return {
        "success": True,
        "findings": findings,
        "warnings": [
            "Flow rate %.4g m^3/s (%.4g L/min), mean outlet speed %.4g m/s, peak speed %.4g m/s, Reynolds number "
            "%.3g, driven by %s, over %d cells%s"
            % (
                flow,
                flow * 60000.0,
                mean_speed,
                peak_speed,
                reynolds,
                applied,
                len(speeds),
                ", steady after %d iterations" % iterations if iterations is not None else "",
            )
        ],
    }


def _nothing_covered(record):
    return {
        "severity": WARNING,
        "message": (
            "The port covers no surface of the part, so the condition on it did nothing. Draw its interface "
            "with a sketch that lies on a face -- a circle of the bore's radius for a pipe's end -- or place "
            "the port on the face."
        ),
        "where": record.get("port") or record.get("interface_label") or "a port",
    }


def _verdict(peak_speed, flow, request):
    """What the numbers mean, as findings: thresholds the package configured."""
    findings = []
    limit = request.get("max_velocity")
    if limit is not None and peak_speed > float(limit):
        findings.append(
            {
                "severity": WARNING,
                "message": "The flow reaches %.3g m/s, past the %.3g m/s it is allowed." % (peak_speed, float(limit)),
                "where": "peak speed",
            }
        )
    minimum = request.get("min_flow_rate")
    if minimum is not None and flow < float(minimum):
        findings.append(
            {
                "severity": WARNING,
                "message": "The part passes %.4g m^3/s, short of the %.4g m^3/s it has to." % (flow, float(minimum)),
                "where": "flow rate",
            }
        )
    return findings


def _iterations(log):
    match = re.search(r"SIMPLE solution converged in (\d+) iterations", log)
    return int(match.group(1)) if match else None


def _write_case(case, request, pressures, outlets, walls, density, viscosity):
    """The dictionaries of a steady laminar incompressible case.

    Pressures are kinematic -- divided by the density -- because that is what an
    incompressible solver in OpenFOAM solves for. The inlets are held at their
    pressure and the outlets at zero, and the velocity on both is left to follow
    from that ('pressureInletOutletVelocity'), which is how a flow driven by a
    pressure difference is posed. Walls are no-slip.
    """
    iterations = int(request.get("iterations", 2000))
    residual = float(request.get("residual", 1e-6))
    foam.write(
        case,
        "system/controlDict",
        "dictionary",
        """
application     simpleFoam;
startFrom       latestTime;
stopAt          endTime;
endTime         %d;
deltaT          1;
writeControl    timeStep;
writeInterval   %d;
writeFormat     ascii;
writePrecision  10;
writeCompression off;
runTimeModifiable false;
""" % (iterations, iterations),
    )
    foam.write(
        case,
        "system/fvSchemes",
        "dictionary",
        """
ddtSchemes { default steadyState; }
gradSchemes { default Gauss linear; }
divSchemes
{
    default none;
    div(phi,U) bounded Gauss linearUpwind grad(U);
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
""",
    )
    foam.write(
        case,
        "system/fvSolution",
        "dictionary",
        """
solvers
{
    p { solver GAMG; smoother GaussSeidel; tolerance %g; relTol 0.01; }
    U { solver smoothSolver; smoother symGaussSeidel; tolerance %g; relTol 0.01; }
}
SIMPLE
{
    nNonOrthogonalCorrectors 2;
    consistent yes;
    residualControl { p %g; U %g; }
}
relaxationFactors { equations { U 0.9; ".*" 0.9; } fields { p 1.0; } }
""" % (residual * 1e-4, residual * 1e-4, residual, residual * 0.1),
    )
    nu = viscosity / density
    foam.write(case, "constant/transportProperties", "dictionary", "transportModel Newtonian;\nnu %.10g;\n" % nu)
    foam.write(case, "constant/turbulenceProperties", "dictionary", "simulationType laminar;\n")

    p_patches = []
    u_patches = []
    for name, value in pressures.items():
        p_patches.append("    %s { type fixedValue; value uniform %.10g; }" % (name, value / density))
        u_patches.append("    %s { type pressureInletOutletVelocity; value uniform (0 0 0); }" % name)
    for name in outlets:
        p_patches.append("    %s { type fixedValue; value uniform 0; }" % name)
        u_patches.append("    %s { type pressureInletOutletVelocity; value uniform (0 0 0); }" % name)
    for name in walls:
        p_patches.append("    %s { type zeroGradient; }" % name)
        u_patches.append("    %s { type noSlip; }" % name)
    foam.write(
        case,
        "0/p",
        "volScalarField",
        "dimensions [0 2 -2 0 0 0 0];\ninternalField uniform 0;\nboundaryField\n{\n%s\n}\n" % "\n".join(p_patches),
    )
    foam.write(
        case,
        "0/U",
        "volVectorField",
        "dimensions [0 1 -1 0 0 0 0];\ninternalField uniform (0 0 0);\nboundaryField\n{\n%s\n}\n"
        % "\n".join(u_patches),
    )


# Importable as a module by its tests, and executed by PartCAD's meta-wrapper
# under the run name below -- so nothing here runs on import.
if __name__ == "__partcad_export__":
    pass
