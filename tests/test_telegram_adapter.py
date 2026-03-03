import json
from pathlib import Path

from larops.services.telegram_adapter import TelegramAdapterConfig, dispatch_once


def test_dispatch_once_filters_and_tracks_state(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    state_file = tmp_path / "telegram_state.json"
    events = [
        {
            "event_id": "evt-1",
            "severity": "info",
            "event_type": "deploy.started",
            "host": "node-a",
            "message": "started",
        },
        {
            "event_id": "evt-2",
            "severity": "error",
            "event_type": "deploy.failed",
            "host": "node-a",
            "message": "failed",
        },
    ]
    events_path.write_text("\n".join(json.dumps(item) for item in events) + "\n", encoding="utf-8")

    sent: list[str] = []

    def fake_sender(_token: str, _chat_id: str, text: str) -> None:
        sent.append(text)

    config = TelegramAdapterConfig(
        events_path=events_path,
        state_file=state_file,
        bot_token="token",
        chat_id="chat",
        min_severity="error",
        batch_size=10,
    )
    report = dispatch_once(config, sender=fake_sender, apply=True)
    assert report["considered"] == 2
    assert report["delivered"] == 1
    assert len(sent) == 1
    assert "deploy.failed" in sent[0]

    second = dispatch_once(config, sender=fake_sender, apply=True)
    assert second["considered"] == 0
    assert second["delivered"] == 0
    assert len(sent) == 1


def test_dispatch_once_plan_mode_marks_as_seen(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    state_file = tmp_path / "telegram_state.json"
    events_path.write_text(
        json.dumps(
            {
                "event_id": "evt-3",
                "severity": "critical",
                "event_type": "host.disk_high",
                "host": "node-b",
                "message": "disk high",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    called = {"count": 0}

    def fake_sender(_token: str, _chat_id: str, _text: str) -> None:
        called["count"] += 1

    config = TelegramAdapterConfig(
        events_path=events_path,
        state_file=state_file,
        bot_token="token",
        chat_id="chat",
        min_severity="warn",
        batch_size=10,
    )
    report = dispatch_once(config, sender=fake_sender, apply=False)
    assert report["delivered"] == 1
    assert called["count"] == 0


def test_dispatch_once_accepts_warning_alias(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    state_file = tmp_path / "telegram_state.json"
    events_path.write_text(
        json.dumps(
            {
                "event_id": "evt-4",
                "severity": "warning",
                "event_type": "security.install.started",
                "host": "node-c",
                "message": "security started",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    sent: list[str] = []

    def fake_sender(_token: str, _chat_id: str, text: str) -> None:
        sent.append(text)

    config = TelegramAdapterConfig(
        events_path=events_path,
        state_file=state_file,
        bot_token="token",
        chat_id="chat",
        min_severity="warn",
        batch_size=10,
    )
    report = dispatch_once(config, sender=fake_sender, apply=True)
    assert report["delivered"] == 1
    assert sent
    assert "[WARN]" in sent[0]
