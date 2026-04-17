"""Test fixtures — stubs the `mcp` package so tests don't require it installed,
and adds the repo root to sys.path so `import server` works from tests/."""

import sys
import types
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class _StubMCP:
    def __init__(self, *_, **__):
        pass

    def tool(self, *_, **__):
        def decorator(fn):
            return fn

        return decorator

    def run(self):
        pass


if "mcp" not in sys.modules:
    mcp_pkg = types.ModuleType("mcp")
    mcp_server = types.ModuleType("mcp.server")
    mcp_fastmcp = types.ModuleType("mcp.server.fastmcp")
    mcp_fastmcp.FastMCP = _StubMCP
    sys.modules["mcp"] = mcp_pkg
    sys.modules["mcp.server"] = mcp_server
    sys.modules["mcp.server.fastmcp"] = mcp_fastmcp
