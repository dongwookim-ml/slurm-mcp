#!/bin/bash
# Setup script for slurm-mcp server
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Setting up Slurm MCP Server ==="

# Check Python version
PYTHON=$(command -v python3 || true)
if [ -z "$PYTHON" ]; then
    echo "Error: python3 not found. Please load a Python module first."
    echo "  e.g.: module load python/3.11"
    exit 1
fi

PY_VERSION=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Using Python $PY_VERSION ($PYTHON)"

# Create virtual environment
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    $PYTHON -m venv .venv
else
    echo "Virtual environment already exists."
fi

# Activate and install
source .venv/bin/activate
echo "Installing dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo ""
echo "=== Setup complete ==="
echo ""
echo "To test the server locally:"
echo "  cd $SCRIPT_DIR && .venv/bin/python server.py"
echo ""
echo "To configure Claude Code on your LOCAL machine, add to ~/.claude/settings.json:"
echo ""
echo '  {
    "mcpServers": {
      "slurm": {
        "command": "ssh",
        "args": ["YOUR_USER@YOUR_CLUSTER_HOST",
                 "cd /path/to/slurm-mcp && .venv/bin/python server.py"]
      }
    }
  }'
echo ""
echo "Replace YOUR_USER@YOUR_CLUSTER_HOST and /path/to/slurm-mcp with your actual values."
echo "Make sure SSH key-based auth is set up (no password prompts)."
