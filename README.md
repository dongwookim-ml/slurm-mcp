# slurm-mcp

An MCP (Model Context Protocol) server that gives AI coding assistants like [Claude Code](https://claude.ai/code) direct access to Slurm HPC clusters.

The server runs on your cluster's login node and exposes Slurm operations, file management, and shell access as MCP tools — letting Claude submit jobs, monitor GPU availability, read logs, and manage files through natural conversation.

## Features

- **Job Management** — submit (`sbatch`), list (`squeue`), cancel (`scancel`), status (`sacct`), and tail output
- **File Operations** — read, write, edit, search, and delete files with storage policy enforcement
- **Cluster Info** — partition overview, node states, GPU availability
- **Shell Access** — run arbitrary commands with safety guardrails
- **Git Sync** — pull latest code to the cluster
- **Storage Policy** — warns when data files (checkpoints, datasets, etc.) target quota-limited directories

## Quick Start

### 1. Setup on the cluster

```bash
git clone https://github.com/dongwookim-ml/slurm-mcp.git
cd slurm-mcp
bash setup.sh
```

### 2. Configure Claude Code on your local machine

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "slurm": {
      "command": "ssh",
      "args": ["user@cluster-host",
               "cd /path/to/slurm-mcp && .venv/bin/python server.py"]
    }
  }
}
```

Replace `user@cluster-host` and `/path/to/slurm-mcp` with your values. SSH key-based auth is required (no password prompts).

### 3. Use it

Once configured, Claude Code can directly interact with your cluster:

- *"Submit a training job on 4 GPUs"*
- *"Check my running jobs"*
- *"Show me the last 100 lines of job 12345's output"*
- *"What GPUs are available right now?"*
- *"Find all .py files under my project directory"*

## Tools

| Category | Tools |
|----------|-------|
| **Slurm Jobs** | `submit_job`, `list_jobs`, `cancel_job`, `job_status`, `tail_output` |
| **File Ops** | `read_file`, `write_file`, `edit_file`, `search_files`, `delete_file`, `disk_usage` |
| **System** | `run_command`, `sync_code`, `cluster_info` |

## Configuration

All paths are configurable via environment variables, making it work on any cluster:

| Variable | Default | Description |
|----------|---------|-------------|
| `SLURM_MCP_HOME_DIR` | `/home1/$USER` | Home directory (quota-limited) |
| `SLURM_MCP_DATA_DIR` | `/home/$USER` | Data storage directory |
| `SLURM_MCP_SCRATCH_DIR` | `/scratch` | Temporary staging area |
| `SLURM_MCP_HOME_QUOTA_GB` | `500` | Home quota threshold for warnings |

Set these in your shell profile or pass them when running the server:

```bash
SLURM_MCP_HOME_DIR=/home/myuser SLURM_MCP_DATA_DIR=/data/myuser .venv/bin/python server.py
```

## Requirements

- Python 3.10+
- Slurm cluster with CLI tools (`sbatch`, `squeue`, `sacct`, `sinfo`, `scancel`)
- SSH key-based access to the cluster
- `mcp` Python package (installed automatically by `setup.sh`)

## How It Works

The server is a single Python file (`server.py`) using the [FastMCP](https://github.com/modelcontextprotocol/python-sdk) framework. It runs on the cluster login node and wraps Slurm CLI commands as async MCP tools. Claude Code connects to it over SSH using the stdio transport.

Storage policy enforcement is built in — when you write files, the server checks if data files (model checkpoints, datasets, archives, etc.) are targeting a quota-limited home directory and suggests the data directory instead.

## License

MIT
