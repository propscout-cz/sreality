#!/usr/bin/env bash
# THE gitleaks: CI's `secrets` job and the .githooks run it through here, so one version scans every boundary (#2035).
# The release is fetched once into the user cache, checked against its pinned sha256, then run with the given args.
# Usage: scripts/gitleaks.sh <gitleaks args>   e.g. scripts/gitleaks.sh git --redact   (the whole history)
set -euo pipefail
version=8.30.1
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) asset=linux_x64 sha256=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb ;;
  Darwin-arm64) asset=darwin_arm64 sha256=b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5 ;;
  *) echo "scripts/gitleaks.sh: no pinned gitleaks for $(uname -s)-$(uname -m)" >&2; exit 2 ;;
esac
bin="${XDG_CACHE_HOME:-$HOME/.cache}/gitleaks/$version/gitleaks"
if [[ ! -x "$bin" ]]; then
  tmp=$(mktemp -d)
  tarball="$tmp/gitleaks.tar.gz"
  curl -fsSL -o "$tarball" "https://github.com/gitleaks/gitleaks/releases/download/v$version/gitleaks_${version}_$asset.tar.gz"
  actual=$( (sha256sum "$tarball" 2>/dev/null || shasum -a 256 "$tarball") | cut -d' ' -f1)
  [[ "$actual" == "$sha256" ]] || { echo "scripts/gitleaks.sh: checksum mismatch for $asset ($actual)" >&2; rm -rf "$tmp"; exit 2; }
  tar -xzf "$tarball" -C "$tmp" gitleaks
  mkdir -p "$(dirname "$bin")"
  mv "$tmp/gitleaks" "$bin"
  rm -rf "$tmp"
fi
exec "$bin" "$@"
