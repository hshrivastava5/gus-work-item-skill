#!/usr/bin/env bash
# Symlink the skill into ~/.claude/skills so `git pull` updates it everywhere.
set -euo pipefail
src="$(cd "$(dirname "$0")" && pwd)/skills/gus-create-story"
dest="$HOME/.claude/skills/gus-create-story"
mkdir -p "$HOME/.claude/skills"
if [ -e "$dest" ] && [ ! -L "$dest" ]; then
  mv "$dest" "$dest.bak.$(date +%s)"
  echo "Existing skill backed up to $dest.bak.*"
fi
ln -sfn "$src" "$dest"
echo "Installed: $dest -> $src"
