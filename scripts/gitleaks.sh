#!/usr/bin/env bash
# THE gitleaks: CI's `secrets` job and the .githooks run it through here, so one version and one output policy (values
# redacted, each finding shown, quiet when clean) scan every boundary (#2035). The release is fetched once into the user
# cache and checked against its pinned sha256.
# Usage: scripts/gitleaks.sh <gitleaks subcommand and args>
#   the whole history, a merge by its own changes: scripts/gitleaks.sh git --log-opts="--all --remerge-diff"
set -euo pipefail
version=8.30.1
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) asset=linux_x64 sha256=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb ;;
  Darwin-arm64) asset=darwin_arm64 sha256=b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5 ;;
  *) echo "scripts/gitleaks.sh: no pinned gitleaks for $(uname -s)-$(uname -m)" >&2; exit 2 ;;
esac
bin="${XDG_CACHE_HOME:-$HOME/.cache}/gitleaks/$version/gitleaks"
if [[ ! -x "$bin" ]]; then
  mkdir -p "$(dirname "$bin")"
  # Beside the binary, so the final mv is an atomic rename: a concurrent first run never execs a half-written file.
  tmp=$(mktemp -d "$(dirname "$bin")/.download.XXXXXX")
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL -o "$tmp/gitleaks.tar.gz" \
    "https://github.com/gitleaks/gitleaks/releases/download/v$version/gitleaks_${version}_$asset.tar.gz"
  actual=$( (sha256sum "$tmp/gitleaks.tar.gz" 2>/dev/null || shasum -a 256 "$tmp/gitleaks.tar.gz") | cut -d' ' -f1)
  [[ "$actual" == "$sha256" ]] || { echo "scripts/gitleaks.sh: checksum mismatch for $asset ($actual)" >&2; exit 2; }
  tar -xzf "$tmp/gitleaks.tar.gz" -C "$tmp" gitleaks
  mv -f "$tmp/gitleaks" "$bin"
  rm -rf "$tmp"
  trap - EXIT
fi
exec "$bin" --no-banner --redact --verbose --log-level warn "$@"
