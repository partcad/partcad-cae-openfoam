#
# PartCAD, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""Make PartCAD's sandbox helpers importable, as they are when PartCAD runs the script.

The analysis script imports `cae_support`, which lives beside PartCAD's
meta-wrapper and is on `sys.path` whenever PartCAD runs an implementation. Under
pytest nothing put it there, so this does -- from the `partcad` installed beside
the tests. A `partcad` too old to have it skips the tests that need it rather
than failing them, with the version it needs said in the reason.
"""

import os
import sys

try:
    import partcad

    WRAPPERS = os.path.join(os.path.dirname(partcad.__file__), "wrappers")
    if os.path.isfile(os.path.join(WRAPPERS, "cae_support.py")):
        sys.path.append(WRAPPERS)
except ImportError:
    pass

sys.path.insert(0, os.path.dirname(__file__))
