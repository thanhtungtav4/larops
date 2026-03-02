#!/usr/bin/env bash
set -euo pipefail

LAROPS_REPO_URL="${LAROPS_REPO_URL:-https://github.com/thanhtungtav4/larops.git}"
LAROPS_INSTALL_DIR="${LAROPS_INSTALL_DIR:-/opt/larops}"
LAROPS_CONFIG_PATH="${LAROPS_CONFIG_PATH:-/etc/larops/larops.yaml}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "[larops-install] Please run as root."
  exit 1
fi

echo "[larops-install] Installing base dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y git curl ca-certificates python3 python3-venv python3-pip

if [[ -d "${LAROPS_INSTALL_DIR}/.git" ]]; then
  echo "[larops-install] Existing install found, pulling latest source..."
  git -C "${LAROPS_INSTALL_DIR}" fetch --all --prune
  git -C "${LAROPS_INSTALL_DIR}" checkout main
  git -C "${LAROPS_INSTALL_DIR}" pull --ff-only
else
  echo "[larops-install] Cloning source from ${LAROPS_REPO_URL}..."
  rm -rf "${LAROPS_INSTALL_DIR}"
  git clone "${LAROPS_REPO_URL}" "${LAROPS_INSTALL_DIR}"
fi

echo "[larops-install] Setting up Python virtual environment..."
python3 -m venv "${LAROPS_INSTALL_DIR}/.venv"
"${LAROPS_INSTALL_DIR}/.venv/bin/pip" install --upgrade pip
"${LAROPS_INSTALL_DIR}/.venv/bin/pip" install -e "${LAROPS_INSTALL_DIR}"

ln -sf "${LAROPS_INSTALL_DIR}/.venv/bin/larops" /usr/local/bin/larops

if [[ ! -f "${LAROPS_CONFIG_PATH}" ]]; then
  echo "[larops-install] Writing default config to ${LAROPS_CONFIG_PATH}..."
  mkdir -p "$(dirname "${LAROPS_CONFIG_PATH}")"
  cp "${LAROPS_INSTALL_DIR}/config/larops.example.yaml" "${LAROPS_CONFIG_PATH}"
fi

echo "[larops-install] Done."
echo "[larops-install] Next step (WordOps-style full bootstrap):"
echo "  larops bootstrap init --apply"

