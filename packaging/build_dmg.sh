#!/bin/bash
# Build a DMG for the AI-kms Daemon (macOS only)
#
# Usage: ./packaging/build_dmg.sh
#
# Prerequisites:
#   - macOS
#   - uv (for running PyInstaller in the project venv)
#   - pyinstaller (dev dependency, already in pyproject.toml)
#
# Output: dist/AI-kms-Daemon.dmg
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Building frozen app with PyInstaller..."
uv run pyinstaller packaging/daemon.spec --clean --noconfirm

echo "==> Creating DMG..."
mkdir -p dist/dmg
cp -R "dist/AI-kms Daemon.app" dist/dmg/

hdiutil create \
  -volname "AI-kms Daemon" \
  -srcfolder dist/dmg \
  -ov \
  -format UDZO \
  "dist/AI-kms-Daemon.dmg"

echo "==> DMG built: dist/AI-kms-Daemon.dmg"
