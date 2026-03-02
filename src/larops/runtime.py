from dataclasses import dataclass
from pathlib import Path
from typing import Any

from larops.config import AppConfig
from larops.core.events import EventEmitter


@dataclass(slots=True)
class AppContext:
    config: AppConfig
    json_output: bool
    dry_run: bool
    verbose: bool
    event_emitter: EventEmitter

    def emit_output(self, status: str, message: str, **extra: Any) -> None:
        payload: dict[str, Any] = {"status": status, "message": message, **extra}
        if self.json_output:
            import json

            print(json.dumps(payload, ensure_ascii=True))
            return

        print(message)

    @classmethod
    def from_config(
        cls,
        config: AppConfig,
        *,
        json_output: bool,
        dry_run: bool,
        verbose: bool,
    ) -> "AppContext":
        return cls(
            config=config,
            json_output=json_output,
            dry_run=dry_run,
            verbose=verbose,
            event_emitter=EventEmitter(Path(config.events.path)),
        )

