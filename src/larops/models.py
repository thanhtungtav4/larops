from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class EventRecord(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    severity: str
    event_type: str
    host: str
    app: str | None = None
    actor: str = "system"
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)

