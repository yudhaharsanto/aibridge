#!/usr/bin/env bash
# aibridge installer for macOS / Linux
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

PY="${PYTHON:-python3}"

echo "==> checking python..."
if ! command -v "$PY" >/dev/null; then
  echo "error: python3 not found. install python >= 3.10 first." >&2
  exit 1
fi

V=$("$PY" -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')
MAJOR="${V%%.*}"
MINOR="${V##*.}"
if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 10 ]; }; then
  echo "error: need python >= 3.10, got $V" >&2
  echo "try: PYTHON=python3.12 ./install.sh" >&2
  exit 1
fi

echo "==> creating venv (.venv)..."
"$PY" -m venv .venv

# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> upgrading pip..."
pip install --upgrade pip wheel >/dev/null

echo "==> installing aibridge + deps..."
pip install -e .

echo "==> fetching camoufox browser (~200MB, once)..."
python -m camoufox fetch || echo "warning: camoufox fetch returned non-zero (maybe already up-to-date)"

echo "==> creating config dir..."
mkdir -p "$HOME/.aibridge/sessions" "$HOME/.aibridge/logs"

BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/aibridge" <<EOF
#!/usr/bin/env bash
exec "$HERE/.venv/bin/aibridge" "\$@"
EOF
chmod +x "$BIN_DIR/aibridge"

echo ""
echo "done. '$BIN_DIR/aibridge' is ready."
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "note: add '$BIN_DIR' to PATH:"
     echo "      echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc"
     ;;
esac
echo ""
echo "next steps:"
echo "  aibridge login monica"
echo "  aibridge serve monica"
