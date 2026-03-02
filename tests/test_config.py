from pathlib import Path

from larops.config import load_config


def test_load_config_defaults_when_missing(tmp_path: Path) -> None:
    config = load_config(tmp_path / "missing.yaml")
    assert config.environment == "production"
    assert config.events.path == ".larops/events.jsonl"


def test_load_config_reads_values(tmp_path: Path) -> None:
    file = tmp_path / "larops.yaml"
    file.write_text(
        "\n".join(
            [
                "environment: staging",
                "events:",
                "  sink: jsonl",
                "  path: /tmp/custom-events.jsonl",
            ]
        ),
        encoding="utf-8",
    )
    config = load_config(file)
    assert config.environment == "staging"
    assert config.events.path == "/tmp/custom-events.jsonl"

