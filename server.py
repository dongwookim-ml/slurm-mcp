#!/usr/bin/env python3
"""Slurm MCP Server — manage Slurm cluster resources via MCP protocol.

Run directly:  python3 server.py
Via SSH:       ssh user@cluster "cd /path/to/slurm-mcp && .venv/bin/python server.py"
"""

import asyncio
import os
import shlex
import tempfile
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
USER = os.environ.get("USER", "unknown")
HOME_DIR = os.environ.get("SLURM_MCP_HOME_DIR", f"/home1/{USER}")
DATA_DIR = os.environ.get("SLURM_MCP_DATA_DIR", f"/home/{USER}")
SCRATCH_DIR = os.environ.get("SLURM_MCP_SCRATCH_DIR", "/scratch")
HOME_QUOTA_GB = int(os.environ.get("SLURM_MCP_HOME_QUOTA_GB", "500"))

# File extensions that indicate "large data" (should NOT live in HOME_DIR)
DATA_EXTENSIONS = {
    ".pt", ".pth", ".bin", ".safetensors", ".ckpt",   # checkpoints
    ".h5", ".hdf5", ".nc", ".npy", ".npz",            # scientific data
    ".tar", ".tar.gz", ".tgz", ".zip", ".gz",         # archives
    ".csv", ".parquet", ".arrow", ".feather",          # tabular
    ".jsonl",                                          # large json-lines
}

# Directory names that suggest data storage
DATA_DIRS = {"datasets", "models", "checkpoints", "data", "weights", "pretrained"}

mcp = FastMCP("slurm-cluster")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _storage_warnings(file_path: str, content_size: int = 0) -> list[str]:
    """Return storage-policy warnings for a proposed write."""
    warnings: list[str] = []
    path = Path(file_path).resolve()

    if not str(path).startswith(HOME_DIR):
        return warnings

    if path.suffix.lower() in DATA_EXTENSIONS:
        try:
            suggested = f"{DATA_DIR}/{path.relative_to(HOME_DIR)}"
        except ValueError:
            suggested = f"{DATA_DIR}/{path.name}"
        warnings.append(
            f"Storage policy: '{path.suffix}' files should go in "
            f"{DATA_DIR}, not {HOME_DIR}. Suggested: {suggested}"
        )

    for part in path.parts:
        if part.lower() in DATA_DIRS:
            warnings.append(
                f"Storage policy: '{part}/' looks like a data directory. "
                f"Use {DATA_DIR} instead of {HOME_DIR}."
            )
            break

    if content_size > 100 * 1024 * 1024:
        warnings.append(
            f"Storage policy: File is {content_size / 1024 / 1024:.1f} MB. "
            f"Large files should go in {DATA_DIR}."
        )

    return warnings


async def _run(
    cmd: str | list[str],
    cwd: str | None = None,
    timeout: int = 60,
) -> tuple[str, str, int]:
    """Run a subprocess; return (stdout, stderr, returncode)."""
    if isinstance(cmd, str):
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return "", f"Command timed out after {timeout}s", -1

    return stdout.decode().strip(), stderr.decode().strip(), proc.returncode


# ===================================================================
# Slurm Job Tools
# ===================================================================

@mcp.tool()
async def submit_job(
    script_path: Optional[str] = None,
    script_content: Optional[str] = None,
    job_name: Optional[str] = None,
    partition: Optional[str] = None,
    gpus: Optional[str] = None,
    nodes: int = 1,
    ntasks: int = 1,
    time_limit: Optional[str] = None,
    output: Optional[str] = None,
    error: Optional[str] = None,
    working_dir: Optional[str] = None,
    extra_args: Optional[str] = None,
) -> str:
    """Submit a Slurm job via sbatch.

    Provide either ``script_path`` (existing .sh/.slurm file on the cluster)
    or ``script_content`` (inline script text — will be saved to a temp file).
    Additional sbatch flags can be passed via ``extra_args`` as a string.
    """
    if not script_path and not script_content:
        return "Error: Provide either script_path or script_content."

    # Write inline content to a temp file
    if script_content:
        if not script_content.startswith("#!"):
            script_content = "#!/bin/bash\n" + script_content
        tmp_dir = working_dir or HOME_DIR
        fd, tmp_path = tempfile.mkstemp(suffix=".sh", dir=tmp_dir, prefix="slurm_job_")
        with os.fdopen(fd, "w") as f:
            f.write(script_content)
        os.chmod(tmp_path, 0o755)
        script_path = tmp_path

    # Build sbatch command
    cmd: list[str] = ["sbatch"]
    if job_name:
        cmd += ["--job-name", job_name]
    if partition:
        cmd += ["--partition", partition]
    if gpus:
        cmd += ["--gpus", gpus]
    cmd += ["--nodes", str(nodes), "--ntasks", str(ntasks)]
    if time_limit:
        cmd += ["--time", time_limit]
    if output:
        cmd += ["--output", output]
    if error:
        cmd += ["--error", error]
    if working_dir:
        cmd += ["--chdir", working_dir]
    if extra_args:
        cmd += shlex.split(extra_args)
    cmd.append(script_path)

    stdout, stderr, rc = await _run(cmd)

    if rc == 0:
        msg = f"Job submitted: {stdout}"
        if script_content:
            msg += f"\nScript saved to: {script_path}"
        return msg
    return f"Submission failed (exit {rc})\nstdout: {stdout}\nstderr: {stderr}"


@mcp.tool()
async def list_jobs(
    user: Optional[str] = None,
    partition: Optional[str] = None,
    state: Optional[str] = None,
) -> str:
    """List Slurm jobs for the current user (or specified user) via squeue."""
    cmd = [
        "squeue",
        "--format=%i|%j|%P|%T|%M|%l|%D|%C|%b|%R",
        "--noheader",
        "--user", user or USER,
    ]
    if partition:
        cmd += ["--partition", partition]
    if state:
        cmd += ["--state", state]

    stdout, stderr, rc = await _run(cmd)
    if rc != 0:
        return f"Error: {stderr}"
    if not stdout:
        return f"No jobs found for user '{user or USER}'."

    headers = "JobID | Name | Partition | State | Elapsed | TimeLimit | Nodes | CPUs | GPUs | Reason/NodeList"
    lines = [headers, "-" * 100]
    for line in stdout.splitlines():
        if line.strip():
            lines.append(" | ".join(f.strip() for f in line.split("|")))
    return "\n".join(lines)


@mcp.tool()
async def cancel_job(job_id: str, signal: Optional[str] = None) -> str:
    """Cancel a Slurm job. job_id can be a single ID or comma-separated list."""
    cmd = ["scancel"]
    if signal:
        cmd += ["--signal", signal]
    cmd.append(job_id)

    _, stderr, rc = await _run(cmd)
    if rc == 0:
        return f"Job {job_id} cancelled."
    return f"Failed to cancel job {job_id}: {stderr}"


@mcp.tool()
async def job_status(job_id: str, detailed: bool = False) -> str:
    """Get detailed job info via sacct. Works for running and completed jobs."""
    fmt = "JobID,JobName,Partition,State,ExitCode,Elapsed,TimelimitRaw,AllocCPUS,AllocTRES,MaxRSS,Start,End,WorkDir"
    if detailed:
        fmt += ",Submit,Eligible,NNodes,NTasks,ReqMem,AveRSS,MaxVMSize"

    cmd = ["sacct", "-j", str(job_id), f"--format={fmt}", "--parsable2", "--noheader"]
    stdout, stderr, rc = await _run(cmd)

    if rc != 0:
        return f"Error: {stderr}"
    if not stdout:
        return f"No information found for job {job_id}."

    fields = fmt.split(",")
    output_lines = [f"Job {job_id} Status:"]
    for line in stdout.splitlines():
        if not line.strip():
            continue
        values = line.split("|")
        output_lines.append("-" * 40)
        for i, field in enumerate(fields):
            if i < len(values) and values[i]:
                output_lines.append(f"  {field}: {values[i]}")
    return "\n".join(output_lines)


@mcp.tool()
async def tail_output(
    job_id: Optional[str] = None,
    file_path: Optional[str] = None,
    lines: int = 50,
    output_type: str = "stdout",
) -> str:
    """Read the stdout/stderr output of a Slurm job.

    Provide ``job_id`` (auto-finds the default slurm-<id>.out file)
    or ``file_path`` (direct path). ``output_type``: 'stdout' or 'stderr'.
    """
    if not job_id and not file_path:
        return "Error: Provide either job_id or file_path."

    if job_id and not file_path:
        # Determine working directory from sacct
        out, err, rc = await _run(
            ["sacct", "-j", str(job_id), "--format=WorkDir", "--parsable2", "--noheader"]
        )
        if rc != 0 or not out:
            return f"Could not find working directory for job {job_id}: {err}"

        work_dir = out.splitlines()[0].split("|")[0].strip()
        ext = ".err" if output_type == "stderr" else ".out"
        candidates = [f"slurm-{job_id}{ext}", f"slurm-{job_id}.out", f"{job_id}{ext}"]

        found = None
        for name in candidates:
            p = os.path.join(work_dir, name)
            if os.path.exists(p):
                found = p
                break
        if not found:
            return f"No output file found for job {job_id} in {work_dir}. Tried: {candidates}"
        file_path = found

    if not os.path.exists(file_path):
        return f"File not found: {file_path}"

    try:
        with open(file_path, "r", errors="replace") as f:
            all_lines = f.readlines()
        tail = all_lines[-lines:]
        header = f"=== {file_path} (last {len(tail)} of {len(all_lines)} lines) ==="
        return header + "\n" + "".join(tail)
    except Exception as e:
        return f"Error reading {file_path}: {e}"


# ===================================================================
# File Tools (with storage-policy enforcement)
# ===================================================================

@mcp.tool()
async def read_file(
    file_path: str,
    offset: int = 0,
    limit: int = 2000,
) -> str:
    """Read a file (or list a directory) on the cluster.

    Returns content with line numbers. Use offset/limit for large files.
    """
    path = Path(file_path).resolve()

    if not path.exists():
        return f"Not found: {path}"

    if path.is_dir():
        entries = sorted(path.iterdir())
        result = [f"Directory: {path} ({len(entries)} entries)"]
        for entry in entries[:200]:
            kind = "d" if entry.is_dir() else "f"
            size = ""
            if entry.is_file():
                try:
                    size = f" ({entry.stat().st_size:,} bytes)"
                except OSError:
                    pass
            result.append(f"  [{kind}] {entry.name}{size}")
        if len(entries) > 200:
            result.append(f"  ... and {len(entries) - 200} more")
        return "\n".join(result)

    try:
        with open(path, "r", errors="replace") as f:
            all_lines = f.readlines()
        selected = all_lines[offset : offset + limit]
        header = f"=== {path} ({len(all_lines)} lines, showing {offset + 1}–{offset + len(selected)}) ==="
        numbered = [f"{i:6d}\t{line.rstrip()}" for i, line in enumerate(selected, start=offset + 1)]
        return header + "\n" + "\n".join(numbered)
    except Exception as e:
        return f"Error reading {path}: {e}"


@mcp.tool()
async def write_file(file_path: str, content: str, force: bool = False) -> str:
    """Write content to a file. Enforces cluster storage policy.

    If a policy warning is raised, set ``force=True`` to write anyway.
    """
    path = Path(file_path).resolve()
    warnings = _storage_warnings(str(path), len(content.encode()))

    if warnings and not force:
        return (
            "Storage policy warnings:\n"
            + "\n".join(f"  - {w}" for w in warnings)
            + "\n\nSet force=True to write anyway, or use the suggested path."
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "w") as f:
            f.write(content)
        msg = f"Written {len(content):,} bytes to {path}"
        if warnings:
            msg += "\n(warnings overridden with force=True)"
        return msg
    except Exception as e:
        return f"Error writing {path}: {e}"


@mcp.tool()
async def edit_file(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """Edit a file by exact string replacement.

    Set ``replace_all=True`` to replace every occurrence.
    """
    path = Path(file_path).resolve()
    if not path.exists():
        return f"File not found: {path}"

    content = path.read_text()
    if old_string not in content:
        return f"old_string not found in {path}"

    count = content.count(old_string)
    if count > 1 and not replace_all:
        return (
            f"old_string found {count} times. "
            "Provide more context or set replace_all=True."
        )

    new_content = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
    path.write_text(new_content)
    replaced = count if replace_all else 1
    return f"Replaced {replaced} occurrence(s) in {path}"


@mcp.tool()
async def disk_usage(path: Optional[str] = None) -> str:
    """Check disk usage. Defaults to home & data directories.

    Useful for monitoring the 500 GB home-directory quota.
    """
    results: list[str] = []

    # Try quota first (fast)
    out, _, rc = await _run("quota -s 2>/dev/null", timeout=10)
    if rc == 0 and out:
        results.append(f"Quota:\n{out}")

    # Specific path
    target = path or HOME_DIR
    out, _, rc = await _run(f"du -sh {shlex.quote(target)} 2>/dev/null", timeout=30)
    if rc == 0 and out:
        results.append(f"Usage ({target}): {out.split()[0]}")

    if (path is None) or (path != DATA_DIR):
        out, _, rc = await _run(f"du -sh {shlex.quote(DATA_DIR)} 2>/dev/null", timeout=30)
        if rc == 0 and out:
            results.append(f"Usage ({DATA_DIR}): {out.split()[0]}")

    return "\n".join(results) if results else "Could not determine disk usage."


@mcp.tool()
async def search_files(
    directory: Optional[str] = None,
    pattern: str = "*",
    search_type: str = "filename",
    file_pattern: Optional[str] = None,
    max_results: int = 50,
) -> str:
    """Search for files by name or search file contents by regex.

    ``search_type``:
      - 'filename': find files whose name matches a glob ``pattern`` (default).
      - 'content': grep file contents for a regex ``pattern``.

    ``file_pattern``: when search_type='content', limit to files matching
    this glob (e.g. '*.py'). Ignored for filename search.
    ``max_results``: cap the number of results (default 50).
    """
    base = directory or HOME_DIR
    if not Path(base).is_dir():
        return f"Directory not found: {base}"

    if search_type == "content":
        cmd = (
            f"grep -rn --include={shlex.quote(file_pattern or '*')} "
            f"-m 1 {shlex.quote(pattern)} {shlex.quote(base)} "
            f"| head -n {max_results}"
        )
        stdout, stderr, rc = await _run(cmd, timeout=60)
        if rc not in (0, 1):  # grep returns 1 for "no matches"
            return f"Error: {stderr}"
        if not stdout:
            return f"No matches for pattern '{pattern}' in {base}"
        return f"Content matches (max {max_results}):\n{stdout}"

    # Default: filename search
    cmd = (
        f"find {shlex.quote(base)} -maxdepth 5 -name {shlex.quote(pattern)} "
        f"-type f 2>/dev/null | head -n {max_results}"
    )
    stdout, stderr, rc = await _run(cmd, timeout=60)
    if rc != 0 and not stdout:
        return f"Error: {stderr}"
    if not stdout:
        return f"No files matching '{pattern}' in {base}"
    return f"Files found (max {max_results}):\n{stdout}"


@mcp.tool()
async def delete_file(
    file_path: str,
    recursive: bool = False,
    force: bool = False,
) -> str:
    """Delete a file or directory on the cluster.

    Set ``recursive=True`` to remove a directory and its contents.
    Set ``force=True`` to confirm the deletion (required).
    """
    path = Path(file_path).resolve()

    # Block deletion of critical root-level directories
    protected = {HOME_DIR, DATA_DIR, SCRATCH_DIR, "/", "/home", "/home1", "/tmp"}
    if str(path) in protected:
        return f"Refused: cannot delete protected path '{path}'."

    if not path.exists():
        return f"Not found: {path}"

    if path.is_dir() and not recursive:
        count = sum(1 for _ in path.iterdir())
        return (
            f"'{path}' is a directory with {count} entries. "
            "Set recursive=True and force=True to delete it."
        )

    if not force:
        if path.is_dir():
            size_out, _, _ = await _run(
                f"du -sh {shlex.quote(str(path))} 2>/dev/null", timeout=10
            )
            size = size_out.split()[0] if size_out else "unknown"
            return (
                f"Confirm deletion of directory '{path}' ({size}). "
                "Set force=True to proceed."
            )
        size = path.stat().st_size
        return (
            f"Confirm deletion of '{path}' ({size:,} bytes). "
            "Set force=True to proceed."
        )

    try:
        if path.is_dir():
            import shutil
            shutil.rmtree(path)
        else:
            path.unlink()
        return f"Deleted: {path}"
    except Exception as e:
        return f"Error deleting {path}: {e}"


# ===================================================================
# System / Git Tools
# ===================================================================

@mcp.tool()
async def run_command(
    command: str,
    working_dir: Optional[str] = None,
    timeout: int = 120,
) -> str:
    """Run an arbitrary shell command on the cluster.

    Runs as the current user. Timeout defaults to 120 s (max 300 s).
    """
    timeout = min(timeout, 300)

    # Refuse obviously catastrophic patterns
    blocked = ["rm -rf /", "mkfs", "> /dev/sd", "chmod -R 777 /"]
    for pat in blocked:
        if pat in command:
            return f"Refused: command contains dangerous pattern '{pat}'."

    stdout, stderr, rc = await _run(command, cwd=working_dir or HOME_DIR, timeout=timeout)
    parts = [f"exit code: {rc}"]
    if stdout:
        parts.append(f"stdout:\n{stdout}")
    if stderr:
        parts.append(f"stderr:\n{stderr}")
    return "\n".join(parts)


@mcp.tool()
async def sync_code(
    repo_path: str,
    branch: Optional[str] = None,
    remote: str = "origin",
) -> str:
    """Git-pull a repository on the cluster to sync latest changes."""
    p = Path(repo_path).resolve()
    if not (p / ".git").exists():
        return f"{p} is not a git repository."

    _, err, rc = await _run(f"git fetch {shlex.quote(remote)}", cwd=str(p))
    if rc != 0:
        return f"Fetch failed: {err}"

    if branch:
        _, err, rc = await _run(f"git checkout {shlex.quote(branch)}", cwd=str(p))
        if rc != 0:
            return f"Checkout failed: {err}"

    pull = f"git pull {shlex.quote(remote)}"
    if branch:
        pull += f" {shlex.quote(branch)}"
    out, err, rc = await _run(pull, cwd=str(p))

    if rc != 0:
        return f"Pull failed: {err}\n{out}"

    log, _, _ = await _run("git log --oneline -5", cwd=str(p))
    return f"Repository synced.\n\nRecent commits:\n{log}"


@mcp.tool()
async def cluster_info() -> str:
    """Show Slurm cluster overview: partitions, nodes, GPU availability."""
    parts: list[str] = []

    out, _, rc = await _run("sinfo --format='%P|%a|%l|%D|%T|%G' --noheader")
    if rc == 0 and out:
        parts.append("=== Partitions ===")
        parts.append("Partition | Avail | TimeLimit | Nodes | State | Gres")
        parts.append("-" * 80)
        for line in out.splitlines():
            if line.strip():
                parts.append(" | ".join(f.strip() for f in line.split("|")))

    out, _, rc = await _run(
        "sinfo -N --format='%N|%P|%T|%c|%m|%G' --noheader"
    )
    if rc == 0 and out:
        parts.append("\n=== Nodes ===")
        parts.append("Node | Partition | State | CPUs | Mem(MB) | Gres")
        parts.append("-" * 80)
        for line in out.splitlines()[:50]:
            if line.strip():
                parts.append(" | ".join(f.strip() for f in line.split("|")))

    return "\n".join(parts) if parts else "Could not retrieve cluster info."


# ===================================================================
# Entry point
# ===================================================================

if __name__ == "__main__":
    mcp.run()
