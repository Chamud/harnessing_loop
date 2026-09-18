"""A command hook. Reads the tool call as JSON on stdin.

Exit code 2 blocks the call; whatever is on stderr becomes the reason the
model sees. Exit 0 with JSON on stdout can add context or rewrite input.
"""

import json
import re
import sys

payload = json.load(sys.stdin)
command = payload.get("input", {}).get("command", "")
if re.search(r"\b(rm|del|rmdir|format|mkfs)\b", command):
    sys.stderr.write("destructive commands are not allowed in this workspace")
    sys.exit(2)
print(json.dumps({"additional_context": "hook: command reviewed"}))
