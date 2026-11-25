"""Value simulation behaviors and the restricted expression evaluator.

Re-exported from ``sme_sim_common.behaviors`` (taken from this module @ 085fe59: ``discrete`` / ``coerce``
replay hooks, ``ramp`` kind, ``reserved_names`` / ``check_point_names``). Kept so that existing imports
``sme_modbus_sim.behaviors`` keep working; new code should import from ``sme_sim_common.behaviors``.
"""

from sme_sim_common.behaviors import *  # noqa: F401,F403
from sme_sim_common.behaviors import (  # noqa: F401  (explicit names used across the product)
    BEHAVIORS,
    ENGINE_NAMES,
    FUNCTIONS,
    Behavior,
    ExpressionError,
    check_point_names,
    compile_expr,
    ident,
    load_table,
    make_behavior,
    reserved_names,
)
