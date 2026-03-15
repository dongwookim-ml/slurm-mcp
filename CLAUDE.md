# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

slurm-mcp is a single-file MCP (Model Context Protocol) server that gives Claude Code programmatic access to a Slurm HPC cluster. It exposes tools for job submission/management, file operations with storage policy enforcement, shell command execution, git sync, and cluster info queries.

## Setup & Running

```bash
# Initial setup (creates .venv, installs deps)
bash setup.sh

# Run the server
.venv/bin/python server.py

# Or via SSH from a local machine
ssh user@cluster "cd /path/to/slurm-mcp && .venv/bin/python server.py"
```

**Dependency**: `mcp>=1.0.0` (installed via `pip install -r requirements.txt`)

**No tests, linting, or formatting tools are configured.**

## Architecture

The entire server lives in `server.py` (~530 lines). It uses the `FastMCP` framework from the `mcp` package.

### Structure within server.py

1. **Configuration** (top): Paths and quotas read from env vars (`SLURM_MCP_HOME_DIR`, `SLURM_MCP_DATA_DIR`, `SLURM_MCP_SCRATCH_DIR`, `SLURM_MCP_HOME_QUOTA_GB`) with sensible defaults. Data file extensions and directory names are also defined here.
2. **Helpers**: `_storage_warnings()` validates file paths against cluster storage policy; `_run()` is the async subprocess executor (all Slurm/shell commands go through this).
3. **Slurm Job Tools** (`@mcp.tool()`): `submit_job`, `list_jobs`, `cancel_job`, `job_status`, `tail_output` — wrap Slurm CLI commands (sbatch, squeue, scancel, sacct).
4. **File Tools**: `read_file`, `write_file`, `edit_file`, `search_files`, `delete_file`, `disk_usage` — file I/O with storage policy enforcement that warns when data files target the home directory.
5. **System/Git Tools**: `run_command` (arbitrary shell with safety blocklist), `sync_code` (git pull), `cluster_info` (sinfo).
6. **Entry point**: `mcp.run()` at the bottom.

### Key design decisions

- **Storage policy enforcement** is built into `write_file`: writes to `/home1/` are checked against data extensions (`.pt`, `.safetensors`, `.csv`, etc.) and data directory names (`datasets`, `models`, etc.). Violations produce warnings; callers must pass `force=True` to override.
- **`_run()` has a default 60s timeout**. `run_command` allows up to 300s. All subprocess calls are async.
- **`run_command` blocks dangerous patterns** like `rm -rf /`, `mkfs`, etc.
- **`submit_job` supports inline scripts**: if `script_content` is provided instead of `script_path`, it writes a temp file to the working directory.

## Configuration

All paths are configurable via environment variables:

| Env Var | Default | Description |
|---------|---------|-------------|
| `SLURM_MCP_HOME_DIR` | `/home1/$USER` | Home directory (quota-limited) |
| `SLURM_MCP_DATA_DIR` | `/home/$USER` | Data storage (datasets, models) |
| `SLURM_MCP_SCRATCH_DIR` | `/scratch` | Temporary staging |
| `SLURM_MCP_HOME_QUOTA_GB` | `500` | Home directory quota for warnings |

## Storage Policy (enforced in code)

Storage policy enforcement is built into `write_file` and checked via `_storage_warnings()`. It warns when data files (by extension or directory name) target the home directory. When modifying this logic, update both `_storage_warnings()` and the constants at the top of the file (`DATA_EXTENSIONS`, `DATA_DIRS`).
