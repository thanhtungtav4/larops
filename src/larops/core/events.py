import json
from pathlib import Path

from larops.models import EventRecord


class EventEmitter:
    def __init__(self, sink_path: Path) -> None:
        self.sink_path = sink_path

    def emit(self, record: EventRecord) -> None:
        self.sink_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record.model_dump(mode="json"), ensure_ascii=True)
        with self.sink_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

