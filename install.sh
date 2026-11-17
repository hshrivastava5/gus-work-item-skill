#!/usr/bin/env bash
# Install all skills and the Altura-GUS mapping service.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.claude/skills"

install_skill() {
  local name="$1"
  local src="$ROOT/skills/$name"
  local dest="$HOME/.claude/skills/$name"
  if [ -e "$dest" ] && [ ! -L "$dest" ]; then
    mv "$dest" "$dest.bak.$(date +%s)"
    echo "Existing skill backed up to $dest.bak.*"
  fi
  ln -sfn "$src" "$dest"
  echo "Installed skill: $dest -> $src"
}

install_skill gus-create-story
install_skill lsc-altura-run

echo ""
echo "============================================"
echo " Altura-GUS Results Mapping Service"
echo "============================================"
echo ""
echo "Service files are in: $ROOT/service/"
echo ""
echo "PREREQUISITES (must be done once per machine):"
echo "  1. Install uv:         curl -LsSf https://astral.sh/uv/install.sh | sh"
echo "  2. Clone altura-sdk:   git clone <altura-sdk-repo> ~/altura-sdk"
echo "  3. Copy Altura certs:  place combined-ca.pem in ~/.altura/combined-ca.pem"
echo "  4. Authenticate GUS:   sf org login web --instance-url https://gus.my.salesforce.com --alias gus"
echo ""
echo "TO START THE SERVICE:"
echo "  bash $ROOT/service/start.sh"
echo ""
echo "Then open http://localhost:8765 in your browser."
echo ""
echo "Optional: set ALTURA_SDK_PATH if your sdk is not at ~/altura-sdk"
echo "  export ALTURA_SDK_PATH=/path/to/your/altura-sdk"
echo "  bash $ROOT/service/start.sh"
