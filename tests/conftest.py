import pytest


@pytest.fixture(autouse=True)
def stub_managed_nginx_provision(monkeypatch):
    monkeypatch.setattr(
        "larops.commands.create.capture_nginx_site_snapshot",
        lambda _domain: {"stub": True},
    )
    monkeypatch.setattr(
        "larops.commands.create.restore_nginx_site_snapshot",
        lambda _snapshot: None,
    )
    monkeypatch.setattr(
        "larops.commands.create.apply_nginx_site_config",
        lambda **kwargs: {"managed": True, **kwargs},
    )
