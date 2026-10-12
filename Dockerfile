# The runtime the flow analysis runs best in.
#
# It carries what pip cannot: OpenFOAM, which is a native toolchain, and a gmsh
# for 64-bit ARM Linux, for which pip has no wheel. Everything else the script
# imports is declared in `partcad.yaml` as `pythonRequirements` and installed by
# PartCAD into the environment it creates -- this image is a *runtime*, and the
# package has to work in a conda or venv sandbox on a host that already has
# OpenFOAM too.
#
# It does NOT carry this package's code. PartCAD mounts the package at run time,
# so a user who fetches a newer version gets that version.
ARG PYTHON_VERSION=3.11
ARG ARCH=amd64
FROM ghcr.io/partcad/partcad-container-python:py${PYTHON_VERSION}-${ARCH}

LABEL partcad.image="1"
LABEL org.opencontainers.image.source="https://github.com/partcad/partcad-cae-openfoam"

USER root
ENV DEBIAN_FRONTEND=noninteractive

# OpenFOAM and the mesher, both from Debian, which builds them for amd64 and
# arm64 alike. Debian's `openfoam` is OpenCFD's v1912: old, and every
# application this package runs -- gmshToFoam, polyDualMesh, simpleFoam -- is
# there and does what it did then. It puts the applications on /usr/bin and its
# environment in /usr/share/openfoam/etc/bashrc, which is where
# `openfoam_common.find_bashrc()` looks first.
#
# A package that cannot be installed says what this Debian *does* offer before
# it fails, so a renamed package and a dropped one can be told apart in the log.
RUN apt-get update \
  && { apt-get install --yes --no-install-recommends openfoam python3-gmsh \
       || { echo "=== apt cannot install what this image is for ==="; \
            . /etc/os-release && echo "base: ${PRETTY_NAME}"; \
            apt-cache policy openfoam python3-gmsh || true; \
            exit 1; }; } \
  && rm -rf /var/lib/apt/lists/*

# gmsh's Python API is one ctypes module wrapping `libgmsh.so`, installed by
# Debian for Debian's interpreter; the one in this image is python.org's. A
# ctypes wrapper works from either, so the one file is copied where both can see
# it and named on PYTHONPATH -- PYTHONPATH rather than site-packages, because
# PartCAD runs the script in a virtual environment it creates at run time.
RUN mkdir -p /opt/pc-site \
  && cp /usr/lib/python3/dist-packages/gmsh.py /opt/pc-site/gmsh.py
ENV PYTHONPATH=/opt/pc-site

# What a caller may run, extending the base image's allowlist rather than
# replacing it: the interpreter stays reachable, and so does the solver for
# whoever is debugging a case by hand.
ENV PC_CONTAINER_ALLOWED_COMMANDS='{"python": "/usr/local/bin/python3", "simpleFoam": "/usr/bin/simpleFoam"}'

# Two checks. The base image's own, for the contract PartCAD talks to a
# container through; and this package's, which proves that OpenFOAM's
# environment can be entered and its applications run *from this interpreter*,
# the way the analysis will.
COPY verify_image.py /tmp/verify_image.py
RUN python3 /pc/verify.py \
  && python3 /tmp/verify_image.py \
  && rm /tmp/verify_image.py

USER pc
