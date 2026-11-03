import json
import sys
from datetime import UTC, datetime
from uuid import UUID, uuid4


def request_id(value: str | None) -> str:
    try:
        return str(UUID(value)) if value else str(uuid4())
    except ValueError:
        return str(uuid4())


def event(name: str, **fields: object) -> None:
    print(json.dumps({"time": datetime.now(UTC).isoformat(), "event": name, **fields}),
          file=sys.stderr, flush=True)
