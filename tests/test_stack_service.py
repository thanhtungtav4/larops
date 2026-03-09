from pathlib import Path

import pytest

from larops.services.stack_service import (
    StackServiceError,
    build_install_commands,
    build_stack_plan,
    detect_stack_platform,
)


def write_os_release(tmp_path: Path, *, os_id: str, version_id: str) -> Path:
    path = tmp_path / "os-release"
    path.write_text(f'ID="{os_id}"\nVERSION_ID="{version_id}"\n', encoding="utf-8")
    return path


def test_detect_stack_platform_supports_ubuntu_2404(tmp_path: Path) -> None:
    os_release = write_os_release(tmp_path, os_id="ubuntu", version_id="24.04")
    platform = detect_stack_platform(os_release_path=os_release)
    assert platform.os_id == "ubuntu"
    assert platform.version_id == "24.04"
    assert platform.package_manager == "apt"
    assert platform.support_level == "ga"


def test_detect_stack_platform_marks_debian_13_experimental(tmp_path: Path) -> None:
    os_release = write_os_release(tmp_path, os_id="debian", version_id="13")
    platform = detect_stack_platform(os_release_path=os_release)
    assert platform.os_id == "debian"
    assert platform.version_id == "13"
    assert platform.support_level == "experimental"


def test_detect_stack_platform_rejects_unknown_os(tmp_path: Path) -> None:
    os_release = write_os_release(tmp_path, os_id="opensuse-leap", version_id="15.6")
    with pytest.raises(StackServiceError, match="Unsupported host OS"):
        detect_stack_platform(os_release_path=os_release)


def test_build_install_commands_use_platform_package_manager(tmp_path: Path) -> None:
    os_release = write_os_release(tmp_path, os_id="ubuntu", version_id="22.04")
    platform = detect_stack_platform(os_release_path=os_release)
    commands = build_install_commands(["web", "ops"], platform=platform)
    assert commands[0] == ["apt-get", "update"]
    assert commands[1][0:3] == ["apt-get", "install", "-y"]
    assert "certbot" in commands[1]
    assert "composer" in commands[1]
    assert "nginx" in commands[1]
    assert "nodejs" in commands[1]
    assert "npm" in commands[1]
    assert "ufw" in commands[1]
    assert "php8.3-fpm" in commands[1]
    assert "php8.3-mysql" in commands[1]
    assert "php8.3-pgsql" in commands[1]


def test_build_install_commands_allow_explicit_php_version_on_debian(tmp_path: Path) -> None:
    os_release = write_os_release(tmp_path, os_id="ubuntu", version_id="24.04")
    platform = detect_stack_platform(os_release_path=os_release)
    commands = build_install_commands(["web"], platform=platform, php_version="8.4")
    assert commands[0] == ["apt-get", "update"]
    assert commands[1][0:3] == ["apt-get", "install", "-y"]
    assert "software-properties-common" in commands[1]
    assert commands[2] == ["add-apt-repository", "-y", "ppa:ondrej/php"]
    assert commands[3] == ["apt-get", "update"]
    assert commands[4][0:3] == ["apt-get", "install", "-y"]
    assert "nodejs" in commands[4]
    assert "npm" in commands[4]
    assert "php8.4-fpm" in commands[4]
    assert "php8.4-cli" in commands[4]
    assert "php8.4-mysql" in commands[4]
    assert "php8.4-pgsql" in commands[4]
    assert "php8.3-fpm" not in commands[4]


def test_build_install_commands_adds_sury_repo_for_debian_php_84(tmp_path: Path) -> None:
    os_release = write_os_release(tmp_path, os_id="debian", version_id="12")
    platform = detect_stack_platform(os_release_path=os_release)
    commands = build_install_commands(["web"], platform=platform, php_version="8.4")
    assert commands[0] == ["apt-get", "update"]
    assert commands[1][0:3] == ["apt-get", "install", "-y"]
    assert "curl" in commands[1]
    assert commands[2][0:2] == ["curl", "-fsSLo"]
    assert commands[3][0:2] == ["dpkg", "-i"]
    assert commands[4][0:2] == ["bash", "-lc"]
    assert "packages.sury.org/php/" in commands[4][2]
    assert commands[5] == ["apt-get", "update"]
    assert commands[6][0:3] == ["apt-get", "install", "-y"]
    assert "nodejs" in commands[6]
    assert "npm" in commands[6]
    assert "php8.4-fpm" in commands[6]
    assert "php8.4-mysql" in commands[6]
    assert "php8.4-pgsql" in commands[6]


def test_build_install_commands_use_dnf_for_rocky_9_and_enable_epel_for_ops(tmp_path: Path) -> None:
    os_release = write_os_release(tmp_path, os_id="rocky", version_id="9.4")
    platform = detect_stack_platform(os_release_path=os_release)
    commands = build_install_commands(["web", "ops"], platform=platform)
    assert commands[0] == ["dnf", "makecache", "-y"]
    assert commands[1] == ["dnf", "install", "-y", "epel-release"]
    assert commands[2] == ["dnf", "makecache", "-y"]
    assert commands[3][0:3] == ["dnf", "install", "-y"]
    assert "certbot" in commands[3]
    assert "composer" in commands[3]
    assert "nginx" in commands[3]
    assert "nodejs" in commands[3]
    assert "firewalld" in commands[3]
    assert "fail2ban" in commands[3]
    assert "php-mysqlnd" in commands[3]
    assert "php-pgsql" in commands[3]


def test_build_install_commands_reject_explicit_php_version_on_el9(tmp_path: Path) -> None:
    os_release = write_os_release(tmp_path, os_id="rocky", version_id="9.4")
    platform = detect_stack_platform(os_release_path=os_release)
    with pytest.raises(StackServiceError, match="Explicit PHP version pinning is not supported"):
        build_install_commands(["web"], platform=platform, php_version="8.4")


def test_detect_stack_platform_supports_rhel_9_as_experimental(tmp_path: Path) -> None:
    os_release = write_os_release(tmp_path, os_id="rhel", version_id="9.3")
    platform = detect_stack_platform(os_release_path=os_release)
    assert platform.os_id == "rhel"
    assert platform.support_level == "experimental"


def test_build_install_commands_use_dnf_for_rhel_9_and_attempt_fail2ban_install(tmp_path: Path) -> None:
    os_release = write_os_release(tmp_path, os_id="rhel", version_id="9.3")
    platform = detect_stack_platform(os_release_path=os_release)
    commands = build_install_commands(["ops"], platform=platform)
    assert commands[0] == ["dnf", "makecache", "-y"]
    assert commands[1] == ["dnf", "install", "-y", "epel-release"]
    assert commands[2] == ["dnf", "makecache", "-y"]
    assert commands[3][0:3] == ["dnf", "install", "-y"]
    assert "fail2ban" in commands[3]
    assert "firewalld" in commands[3]


def test_build_stack_plan_includes_platform_metadata(tmp_path: Path) -> None:
    os_release = write_os_release(tmp_path, os_id="debian", version_id="12")
    plan = build_stack_plan(["web"], os_release_path=os_release)
    assert plan.platform.label == "debian 12"
    assert plan.platform.support_level == "ga"
    assert plan.php_version is None
    assert plan.php_repo_provider is None


def test_build_stack_plan_carries_php_version_for_web_group(tmp_path: Path) -> None:
    os_release = write_os_release(tmp_path, os_id="debian", version_id="12")
    plan = build_stack_plan(["web"], os_release_path=os_release, php_version="8.4")
    assert plan.php_version == "8.4"
    assert plan.php_repo_provider == "sury"
    assert "php8.4-fpm" in plan.commands[-1]
