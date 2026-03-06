#!/usr/bin/env bash
set -euo pipefail

LAROPS_REPO_URL="${LAROPS_REPO_URL:-https://github.com/thanhtungtav4/larops.git}"
LAROPS_INSTALL_DIR="${LAROPS_INSTALL_DIR:-/opt/larops}"
LAROPS_CONFIG_PATH="${LAROPS_CONFIG_PATH:-/etc/larops/larops.yaml}"
LAROPS_VERSION="${LAROPS_VERSION:-0.1.0}"
LAROPS_RELEASE_BASE_URL="${LAROPS_RELEASE_BASE_URL:-https://github.com/thanhtungtav4/larops/releases/download}"
LAROPS_ALLOW_UNPINNED="${LAROPS_ALLOW_UNPINNED:-false}"
LAROPS_SKIP_CHECKSUM="${LAROPS_SKIP_CHECKSUM:-false}"

INSTALL_STAGING_DIR="${LAROPS_INSTALL_DIR}.new.$$"
INSTALL_BACKUP_DIR="${LAROPS_INSTALL_DIR}.bak.$$"
INSTALL_SUCCEEDED=false

is_true() {
  local raw
  raw="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
  case "${raw}" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

normalize_tag() {
  local raw="$1"
  if [[ "${raw}" == v* ]]; then
    printf '%s' "${raw}"
  else
    printf 'v%s' "${raw}"
  fi
}

assert_safe_install_dir() {
  case "${LAROPS_INSTALL_DIR}" in
    ""|"/"|"/opt"|"/usr"|"/var"|"/etc"|"/home")
      echo "[larops-install] Unsafe install dir: ${LAROPS_INSTALL_DIR}"
      exit 1
      ;;
  esac
}

cleanup_on_exit() {
  if [[ "${INSTALL_SUCCEEDED}" == "true" ]]; then
    return 0
  fi
  rm -rf "${INSTALL_STAGING_DIR}"
  if [[ -d "${INSTALL_BACKUP_DIR}" && ! -e "${LAROPS_INSTALL_DIR}" ]]; then
    mv "${INSTALL_BACKUP_DIR}" "${LAROPS_INSTALL_DIR}"
  fi
}

prepare_dir() {
  local target_dir="$1"
  rm -rf "${target_dir}"
  mkdir -p "${target_dir}"
}

stage_from_release_asset() {
  local tag="$1"
  local target_dir="$2"
  local tmp_dir archive_name archive_path checksum_path archive_url checksum_url expected actual

  tmp_dir="$(mktemp -d)"
  archive_name="larops-${tag}.tar.gz"
  archive_path="${tmp_dir}/${archive_name}"
  checksum_path="${tmp_dir}/SHA256SUMS"
  archive_url="${LAROPS_RELEASE_BASE_URL}/${tag}/${archive_name}"
  checksum_url="${LAROPS_RELEASE_BASE_URL}/${tag}/SHA256SUMS"

  echo "[larops-install] Downloading release archive ${archive_name}..."
  curl -fsSL "${archive_url}" -o "${archive_path}"

  if is_true "${LAROPS_SKIP_CHECKSUM}"; then
    echo "[larops-install] WARNING: checksum verification is disabled (LAROPS_SKIP_CHECKSUM=true)."
  else
    echo "[larops-install] Verifying checksum from ${checksum_url}..."
    curl -fsSL "${checksum_url}" -o "${checksum_path}"
    expected="$(awk -v target="${archive_name}" '$2==target {print $1}' "${checksum_path}")"
    if [[ -z "${expected}" ]]; then
      echo "[larops-install] Missing checksum entry for ${archive_name}."
      rm -rf "${tmp_dir}"
      exit 1
    fi
    actual="$(sha256sum "${archive_path}" | awk '{print $1}')"
    if [[ "${expected}" != "${actual}" ]]; then
      echo "[larops-install] Checksum mismatch for ${archive_name}."
      rm -rf "${tmp_dir}"
      exit 1
    fi
  fi

  prepare_dir "${target_dir}"
  tar -xzf "${archive_path}" -C "${target_dir}" --strip-components=1
  rm -rf "${tmp_dir}"
}

stage_from_latest() {
  local target_dir="$1"

  prepare_dir "${target_dir}"
  echo "[larops-install] Cloning source from ${LAROPS_REPO_URL}..."
  git clone "${LAROPS_REPO_URL}" "${target_dir}"
  echo "[larops-install] Using latest main branch..."
  git -C "${target_dir}" checkout main
  git -C "${target_dir}" pull --ff-only
}

setup_virtualenv() {
  local target_dir="$1"

  echo "[larops-install] Setting up Python virtual environment..."
  python3 -m venv "${target_dir}/.venv"
  "${target_dir}/.venv/bin/pip" install --upgrade pip
  "${target_dir}/.venv/bin/pip" install -e "${target_dir}"
}

activate_install() {
  local staged_dir="$1"

  if [[ -e "${LAROPS_INSTALL_DIR}" ]]; then
    mv "${LAROPS_INSTALL_DIR}" "${INSTALL_BACKUP_DIR}"
  fi

  mv "${staged_dir}" "${LAROPS_INSTALL_DIR}"
  ln -sf "${LAROPS_INSTALL_DIR}/.venv/bin/larops" /usr/local/bin/larops

  if [[ ! -f "${LAROPS_CONFIG_PATH}" ]]; then
    echo "[larops-install] Writing default config to ${LAROPS_CONFIG_PATH}..."
    mkdir -p "$(dirname "${LAROPS_CONFIG_PATH}")"
    cp "${LAROPS_INSTALL_DIR}/config/larops.example.yaml" "${LAROPS_CONFIG_PATH}"
  fi

  rm -rf "${INSTALL_BACKUP_DIR}"
  INSTALL_SUCCEEDED=true
}

trap cleanup_on_exit EXIT

if [[ "${EUID}" -ne 0 ]]; then
  echo "[larops-install] Please run as root."
  exit 1
fi

if [[ "${LAROPS_VERSION}" == "latest" || "${LAROPS_VERSION}" == "main" ]]; then
  if ! is_true "${LAROPS_ALLOW_UNPINNED}"; then
    echo "[larops-install] Refusing unpinned install (${LAROPS_VERSION})."
    echo "[larops-install] Use pinned version (e.g. LAROPS_VERSION=0.1.0),"
    echo "[larops-install] or explicitly set LAROPS_ALLOW_UNPINNED=true to continue."
    exit 1
  fi
fi

assert_safe_install_dir

echo "[larops-install] Installing base dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y git curl ca-certificates python3 python3-venv python3-pip tar

if [[ "${LAROPS_VERSION}" == "latest" || "${LAROPS_VERSION}" == "main" ]]; then
  stage_from_latest "${INSTALL_STAGING_DIR}"
else
  tag="$(normalize_tag "${LAROPS_VERSION}")"
  echo "[larops-install] Using pinned version ${tag}..."
  stage_from_release_asset "${tag}" "${INSTALL_STAGING_DIR}"
fi

setup_virtualenv "${INSTALL_STAGING_DIR}"
activate_install "${INSTALL_STAGING_DIR}"

echo "[larops-install] Done."
echo "[larops-install] Next step (WordOps-style full bootstrap):"
echo "  larops bootstrap init --apply"
