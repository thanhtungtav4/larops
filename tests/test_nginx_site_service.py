from pathlib import Path

from larops.services.nginx_site_service import render_nginx_site_config, resolve_nginx_site_paths


def test_resolve_nginx_site_paths_defaults_to_debian_layout(monkeypatch) -> None:
    monkeypatch.setattr("larops.services.nginx_site_service._platform_family", lambda: "debian")
    paths = resolve_nginx_site_paths("example.test")
    assert str(paths.server_config_file) == "/etc/nginx/sites-available/example.test.conf"
    assert str(paths.enabled_site_file) == "/etc/nginx/sites-enabled/example.test.conf"
    assert paths.activation_mode == "symlink"


def test_resolve_nginx_site_paths_uses_el9_layout(monkeypatch) -> None:
    monkeypatch.setattr("larops.services.nginx_site_service._platform_family", lambda: "el9")
    paths = resolve_nginx_site_paths("example.test")
    assert str(paths.server_config_file) == "/etc/nginx/conf.d/example.test.conf"
    assert str(paths.enabled_site_file) == "/etc/nginx/conf.d/example.test.conf"
    assert paths.activation_mode == "direct"


def test_render_nginx_site_config_http_only() -> None:
    body = render_nginx_site_config(
        domain="example.test",
        document_root=Path("/var/www/example.test/current/public"),
        fastcgi_pass="unix:/run/php/php8.3-fpm.sock",
        family="debian",
        https_enabled=False,
    )
    assert "listen 80;" in body
    assert "listen 443 ssl http2;" not in body
    assert "try_files $uri $uri/ /index.php?$query_string;" in body
    assert "fastcgi_pass unix:/run/php/php8.3-fpm.sock;" in body


def test_render_nginx_site_config_https_redirects_and_loads_cert_paths() -> None:
    body = render_nginx_site_config(
        domain="example.test",
        document_root=Path("/var/www/example.test/current/public"),
        fastcgi_pass="unix:/run/php/php8.3-fpm.sock",
        family="debian",
        https_enabled=True,
    )
    assert "return 301 https://$host$request_uri;" in body
    assert "listen 443 ssl http2;" in body
    assert "/etc/letsencrypt/live/example.test/fullchain.pem" in body
    assert "/etc/letsencrypt/live/example.test/privkey.pem" in body
