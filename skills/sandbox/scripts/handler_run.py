"""Handler for `sandbox.run` - see ../SKILL.md. Reads one JSON argv element."""
import json
import os
import sys

_SKILL = os.environ.get("SKELETONKEY_SKILL_DIR", "")
if _SKILL:
    sys.path.insert(0, os.path.join(_SKILL, "scripts"))
import sandboxlib as _s  # noqa: E402

raw = sys.argv[1] if len(sys.argv) > 1 else "{}"
try:
    args = json.loads(raw)
except ValueError:
    args = {}
print(json.dumps(_s.cmd_run(args), sort_keys=True, separators=(",", ":")))
