import os
import json

config = {
    "foo": ["bar", "bang",],
    "baz": True,
    "buck": {
        "meh": "bah",
    },
    "moo": "bahbah"
}
github_output = os.environ.get("GITHUB_OUTPUT")
with open(github_output, "a", encoding="utf-8") as wfh:
    wfh.write(f"config={json.dumps(config)}\n")
