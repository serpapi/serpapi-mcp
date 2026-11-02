"""Small, fixed catalogs for offline protocol and generation tests."""

import hashlib
import json
from datetime import UTC, datetime


PARAMETERS = {
    "search_query": {
        "parameters": {
            "q": {"type": "string", "required": True, "html": "<b>Query</b>"}
        }
    }
}
# Exercise the production minimum without weakening validation in test processes.
PROPS = {
    "parameters": {
        name: PARAMETERS
        for name in ["google", "google_light", "fixture_engine"]
        + [f"fixture_{index:03}" for index in range(97)]
    }
}


def write_catalog(directory, schemas, generated_at=None):
    directory.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name, schema in schemas.items():
        content = json.dumps(schema, ensure_ascii=False).encode("utf-8")
        filename = f"{name}.json"
        (directory / filename).write_bytes(content)
        hashes[filename] = hashlib.sha256(content).hexdigest()
    (directory / ".catalog").write_text(
        json.dumps(
            {
                "generated_at": generated_at or datetime.now(UTC).isoformat(),
                "files": hashes,
            }
        )
    )


def snapshot(directory):
    return {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in directory.iterdir()
        if path.is_file()
    }
