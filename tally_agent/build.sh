#!/bin/bash
# Build script for macOS/Linux (for testing; production .exe must be built on Windows)
set -e

echo "Installing build dependencies..."
pip install -r requirements.txt

echo "Building standalone binary..."
pyinstaller \
  --onefile \
  --name OnsummTallyAgent \
  --hidden-import tkinter \
  --collect-all pystray \
  --collect-all PIL \
  agent.py

echo "Build complete: dist/OnsummTallyAgent"
sha256sum dist/OnsummTallyAgent 2>/dev/null || shasum -a 256 dist/OnsummTallyAgent
