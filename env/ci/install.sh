#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo 'Run this installer with sudo or as root on CT 104.' >&2
  exit 1
fi

missing=()
command -v python3 >/dev/null || missing+=(python3)
command -v git >/dev/null || missing+=(git)
command -v ssh-keygen >/dev/null || missing+=(openssh-client)
command -v runuser >/dev/null || missing+=(util-linux)
if [ "${#missing[@]}" -gt 0 ]; then
  command -v apt-get >/dev/null || { echo 'Install Python 3, Git, OpenSSH client and util-linux first.' >&2; exit 1; }
  apt-get update
  apt-get install -y --no-install-recommends ca-certificates "${missing[@]}"
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$script_dir/install.py" "$@"
