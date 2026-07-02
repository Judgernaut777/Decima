# Importing decima arms the WIRE1 egress guard: from this point on, no code in
# the process can open an http/https connection through `urllib` without
# passing the egress gate (wire.real_transport). See decima/wire.py — Phase 1's
# "network egress boundary at the wire". Offline test transports are unaffected.
from decima import wire as _wire  # noqa: F401  (imported for its arming side effect)
