#!/usr/bin/env bash
# Read-only checks for CVE-2026-31431 ("Copy Fail") exposure via CONFIG_CRYPTO_USER_API_AEAD.
# Run on the target Linux host: bash check-copy-fail.sh

set -euo pipefail

kver=$(uname -r)
boot_cfg="/boot/config-${kver}"

echo "=== CVE-2026-31431 (Copy Fail) — local check ==="
echo "Kernel release: ${kver}"
echo

cfg_line=""
cfg_source=""
if [[ -r "${boot_cfg}" ]]; then
  cfg_source="${boot_cfg}"
  cfg_line=$(grep -E '^CONFIG_CRYPTO_USER_API_AEAD=' "${boot_cfg}" 2>/dev/null || true)
  if [[ -z "${cfg_line}" ]] && grep -q '^# CONFIG_CRYPTO_USER_API_AEAD is not set$' "${boot_cfg}" 2>/dev/null; then
    cfg_line="# CONFIG_CRYPTO_USER_API_AEAD is not set"
  fi
elif [[ -r /proc/config.gz ]]; then
  cfg_source="/proc/config.gz"
  cfg_line=$(zgrep -E '^CONFIG_CRYPTO_USER_API_AEAD=' /proc/config.gz 2>/dev/null || true)
  if [[ -z "${cfg_line}" ]] && zgrep -q '^# CONFIG_CRYPTO_USER_API_AEAD is not set$' /proc/config.gz 2>/dev/null; then
    cfg_line="# CONFIG_CRYPTO_USER_API_AEAD is not set"
  fi
else
  echo "WARN: No ${boot_cfg} and no /proc/config.gz — run on the host or inspect vendor kernel docs."
  exit 0
fi

echo "Source: ${cfg_source}"
echo "${cfg_line:-<no CONFIG_CRYPTO_USER_API_AEAD line found>}"
echo

if [[ "${cfg_line}" == "# CONFIG_CRYPTO_USER_API_AEAD is not set" ]]; then
  echo "Classification: option disabled — not vulnerable via this AF_ALG AEAD path."
  exit 0
fi

if [[ -z "${cfg_line}" ]]; then
  echo "CONFIG_CRYPTO_USER_API_AEAD: (not found — cannot auto-classify)"
  exit 0
fi

case "${cfg_line}" in
  CONFIG_CRYPTO_USER_API_AEAD=m)
    echo "Classification: module (algif_aead) — exploit path exists if module loads."
    if lsmod 2>/dev/null | grep -q '^algif_aead'; then
      echo "Module state: LOADED"
    else
      echo "Module state: not currently loaded (still patch or blacklist before untrusted code runs)."
    fi
    echo
    echo "Recommended: install vendor-patched kernel and reboot."
    echo "Temporary (until reboot/patch), if policy allows:"
    echo '  echo "install algif_aead /bin/false" | sudo tee /etc/modprobe.d/disable-algif.conf'
    echo "  sudo rmmod algif_aead 2>/dev/null || true"
    ;;
  CONFIG_CRYPTO_USER_API_AEAD=y)
    echo "Classification: built-in — rmmod will NOT mitigate."
    echo "Recommended: vendor-patched kernel, or initcall_blacklist=algif_aead_init + reboot per distro docs."
    ;;
  *)
    echo "Classification: unexpected line — review manually."
    ;;
esac
