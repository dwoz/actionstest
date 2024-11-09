import os
import json

config = {
    "foo": ["bar", "bang",]
    "baz": True,
    "buck": {
        "meh": "bah",
    },
}
github_output = os.environ.get("GITHUB_OUTPUT")
with open(github_output, "a", encoding="utf-8") as wfh:
    wfh.write(f"jobs={json.dumps(config)}\n")
