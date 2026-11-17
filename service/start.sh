#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# Resolve altura-sdk path: use ALTURA_SDK_PATH env var or default ~/altura-sdk
ALTURA_SDK="${ALTURA_SDK_PATH:-$HOME/altura-sdk}"

if [ ! -d "$ALTURA_SDK/src" ]; then
  echo "ERROR: altura-sdk not found at $ALTURA_SDK"
  echo "Clone it first: git clone <altura-sdk-repo> ~/altura-sdk"
  echo "Or set: export ALTURA_SDK_PATH=/path/to/altura-sdk"
  exit 1
fi

echo "Starting Altura-GUS Results Mapping server on http://localhost:8765"
echo "Using altura-sdk at: $ALTURA_SDK"
echo ""

uv run \
  --with fastapi \
  --with "uvicorn[standard]" \
  --with httpx \
  --with cryptography \
  --with toml \
  --with pydantic \
  python3 -c "
import sys
sys.path.insert(0, '$ALTURA_SDK/src')
import uvicorn
from server import app
uvicorn.run(app, host='0.0.0.0', port=8765)
"
