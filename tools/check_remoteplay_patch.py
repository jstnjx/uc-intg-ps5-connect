"""Fail the packaging build if an unpatched pyremoteplay slips into the bundle.

The 2023+ PS5 test stream requires client_version=12, not upstream's 7.
This script executes only in the packaging environment *after pip install*.
"""
from __future__ import annotations

import inspect

from pyremoteplay.stream import RPStream

source = inspect.getsource(RPStream._send_big)
# The fork's test branch must choose PS5 client version 12.
test_branch = source.split("else:", 1)[-1].split("data = ProtoHandler.big_payload", 1)[0]
expected = "client_version = 12 if self._session.type == TYPE_PS5 else 9"
if expected not in test_branch:
    raise SystemExit("Unpatched pyremoteplay PS5 test handshake; refusing to build")
print("pyremoteplay PS5 network test uses client version 12")
