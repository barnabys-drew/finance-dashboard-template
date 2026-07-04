#!/bin/bash
# Fetch the standalone Tailwind CSS CLI (single binary, no node_modules).
# Run once after cloning: ./scripts/setup-tailwind.sh
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="v3.4.17"
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64)  ASSET="tailwindcss-linux-x64" ;;
  Linux-aarch64) ASSET="tailwindcss-linux-arm64" ;;
  Darwin-arm64)  ASSET="tailwindcss-macos-arm64" ;;
  Darwin-x86_64) ASSET="tailwindcss-macos-x64" ;;
  *) echo "Unsupported platform: $(uname -s)-$(uname -m)"; exit 1 ;;
esac

mkdir -p bin
echo "Downloading Tailwind $VERSION ($ASSET)…"
curl -sL -o bin/tailwindcss \
  "https://github.com/tailwindlabs/tailwindcss/releases/download/${VERSION}/${ASSET}"
chmod +x bin/tailwindcss
echo "Done. Build CSS with: ./bin/tailwindcss -i static/src/input.css -o static/app.css --minify"
