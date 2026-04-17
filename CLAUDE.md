# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

slurm-mcp is a single-file MCP (Model Context Protocol) server that gives Claude Code programmatic access to the **ai2 Slurm HPC cluster**. It exposes tools for job submission/management, file operations with storage policy enforcement, shell command execution, git sync, and cluster info queries.

Cluster-specific assumptions (partition names, QOS rules, default paths) are baked into the code — see the "ai2 cluster policy" section below.

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
4. **Watcher Tools**: `watch_job`, `list_watches` — background `asyncio.Task` per job, polls `sacct`, posts to `SLURM_MCP_NOTIFY_WEBHOOK` on terminal state. Watchers live in the module-level `_watchers` dict and do not persist across server restarts.
5. **File Tools**: `read_file`, `write_file`, `edit_file`, `search_files`, `delete_file`, `disk_usage` — file I/O with storage policy enforcement that warns when data files target the home directory.
6. **System/Git Tools**: `run_command` (arbitrary shell with safety blocklist), `sync_code` (git pull), `cluster_info` (sinfo).
7. **Entry point**: `mcp.run()` at the bottom.

### Key design decisions

- **Storage policy enforcement** is built into `write_file`: writes to `/home1/` are checked against data extensions (`.pt`, `.safetensors`, `.csv`, etc.) and data directory names (`datasets`, `models`, etc.). Violations produce warnings; callers must pass `force=True` to override.
- **`_run()` has a default 60s timeout**. `run_command` allows up to 300s. All subprocess calls are async.
- **`run_command` blocks dangerous patterns** like `rm -rf /`, `mkfs`, etc.
- **`submit_job` supports inline scripts**: if `script_content` is provided instead of `script_path`, it writes a temp file to the working directory.
- **Preamble injection**: when `SLURM_MCP_PREAMBLE` is set, its contents are prepended after the shebang of inline `script_content` (not applied to `script_path` — that file is owned by the caller).
- **Auto-QOS**: when `partition` is in `HPGPU_PARTITIONS` and `extra_args` contains no `--qos` / `-q`, `--qos=hpgpu` is injected. Extend `HPGPU_PARTITIONS` when cluster policy changes.
- **Webhook notifications** use stdlib `urllib` via `asyncio.to_thread`; no extra dependency. The payload includes both `text` (Slack) and `content` (Discord) keys for compatibility.

## Configuration

All paths are configurable via environment variables:

| Env Var | Default | Description |
|---------|---------|-------------|
| `SLURM_MCP_HOME_DIR` | `/home1/$USER` | Home directory (quota-limited) |
| `SLURM_MCP_DATA_DIR` | `/home/$USER` | Data storage (datasets, models) |
| `SLURM_MCP_SCRATCH_DIR` | `/scratch` | Temporary staging |
| `SLURM_MCP_HOME_QUOTA_GB` | `500` | Home directory quota for warnings |
| `SLURM_MCP_PREAMBLE` | *(empty)* | Shell lines injected after shebang of inline job scripts |
| `SLURM_MCP_NOTIFY_WEBHOOK` | *(empty)* | Webhook URL for job-completion notifications |

## Storage Policy (enforced in code)

Storage policy enforcement is built into `write_file` and checked via `_storage_warnings()`. It warns when data files (by extension or directory name) target the home directory. When modifying this logic, update both `_storage_warnings()` and the constants at the top of the file (`DATA_EXTENSIONS`, `DATA_DIRS`).

## ai2 cluster policy

These rules are specific to the ai2 cluster and are encoded in `server.py`. Update both the code and this section when cluster policy changes.

### Partition → QOS mapping

| Partition | QOS required | Notes |
|-----------|--------------|-------|
| `A100-40GB` (SXM4) | `hpgpu` | 1 node, 8 GPUs |
| `A100-80GB` (SXM4) | `hpgpu` | ~14 nodes, 8 GPUs each |
| `4A100` | `hpgpu` | A100 SXM4 variant |
| `A100-40GB-PCIe` | `normal` (default) | **Does not require hpgpu** — distinct from the SXM4 A100 partitions |
| `A6000`, `RTX4090`, `L40S`, `RTX6000ADA`, `RTX3090`, `RTX2080Ti`, `TITANRTX` | `normal` (default) | |
| `H200`, `H200-ZT`, `H200-ZT-PCIe` | **unclear** | Verify QOS before submitting |

`HPGPU_PARTITIONS` in `server.py` holds the set of partitions that trigger auto-injection of `--qos=hpgpu` in `submit_job`. The A100-40GB-PCIe partition is intentionally **excluded** from this set.

### Storage layout

- `/home1/$USER` — quota-limited home (~500 GB). For code, configs, small logs. Storage policy warns when writing data files here.
- `/home/$USER` — data storage. For datasets, model weights, checkpoints, archives.
- `/scratch` — temporary staging.

### Manual usage reference

- `sbatch`: `--qos=hpgpu --partition=A100-80GB`
- `srun`: `srun -q hpgpu -p A100-80GB -N 1 -n 1 --gres=gpu:4 --pty /bin/bash`
