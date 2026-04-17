"""Unit tests for server.py helpers and _poll_job_state.

Runs without a Slurm cluster: _run is monkeypatched to return canned responses.
The real mcp package is stubbed by conftest.py, so tests work in any venv with
pytest + pytest-asyncio installed.
"""

import pytest

import server


# ---------------------------------------------------------------------------
# _has_qos_flag
# ---------------------------------------------------------------------------

class TestHasQosFlag:
    def test_none_returns_false(self):
        assert server._has_qos_flag(None) is False

    def test_empty_string_returns_false(self):
        assert server._has_qos_flag("") is False

    def test_long_form_equals(self):
        assert server._has_qos_flag("--qos=hpgpu") is True

    def test_long_form_spaced(self):
        assert server._has_qos_flag("--qos hpgpu") is True

    def test_short_form(self):
        assert server._has_qos_flag("-q hpgpu") is True

    def test_only_unrelated_flags(self):
        assert server._has_qos_flag("--mem=4G --cpus-per-task=8") is False

    def test_substring_of_another_flag(self):
        # "qos" appearing as substring of a value must NOT trigger detection
        assert server._has_qos_flag("--job-name=qostest") is False

    def test_detected_among_other_flags(self):
        assert server._has_qos_flag("--time=10:00 --qos=hpgpu --gres=gpu:1") is True


# ---------------------------------------------------------------------------
# _inject_preamble
# ---------------------------------------------------------------------------

class TestInjectPreamble:
    def test_empty_preamble_returns_unchanged(self, monkeypatch):
        monkeypatch.setattr(server, "PREAMBLE", "")
        script = "#!/bin/bash\necho hi\n"
        assert server._inject_preamble(script) == script

    def test_preamble_inserted_after_shebang(self, monkeypatch):
        monkeypatch.setattr(server, "PREAMBLE", "module load cuda/12.1")
        result = server._inject_preamble("#!/bin/bash\necho hi\n")
        assert result == "#!/bin/bash\nmodule load cuda/12.1\necho hi\n"

    def test_shebang_added_when_missing(self, monkeypatch):
        monkeypatch.setattr(server, "PREAMBLE", "module load cuda/12.1")
        result = server._inject_preamble("echo hi\n")
        assert result.startswith("#!/bin/bash\n")
        assert "module load cuda/12.1" in result
        assert result.endswith("echo hi\n")

    def test_multiline_preamble_preserves_order(self, monkeypatch):
        monkeypatch.setattr(
            server,
            "PREAMBLE",
            "module load cuda/12.1\nsource ~/.venv/bin/activate",
        )
        result = server._inject_preamble("#!/bin/bash\npython train.py\n")
        lines = result.splitlines()
        assert lines[0] == "#!/bin/bash"
        assert lines[1] == "module load cuda/12.1"
        assert lines[2] == "source ~/.venv/bin/activate"
        assert lines[3] == "python train.py"


# ---------------------------------------------------------------------------
# _storage_warnings
# ---------------------------------------------------------------------------

class TestStorageWarnings:
    @pytest.fixture(autouse=True)
    def _paths(self, monkeypatch):
        monkeypatch.setattr(server, "HOME_DIR", "/home1/alice")
        monkeypatch.setattr(server, "DATA_DIR", "/home/alice")

    def test_path_outside_home_no_warnings(self):
        assert server._storage_warnings("/home/alice/datasets/big.pt", 0) == []

    def test_data_extension_in_home_warns(self):
        warnings = server._storage_warnings("/home1/alice/model.pt", 0)
        assert len(warnings) == 1
        assert ".pt" in warnings[0]
        assert "/home/alice" in warnings[0]

    def test_data_dir_name_in_home_warns(self):
        warnings = server._storage_warnings("/home1/alice/datasets/x.txt", 0)
        # .txt is not a data extension, so only the dir-name warning fires
        assert len(warnings) == 1
        assert "datasets" in warnings[0]

    def test_large_file_in_home_warns(self):
        warnings = server._storage_warnings("/home1/alice/logs.txt", 200 * 1024 * 1024)
        assert any("MB" in w for w in warnings)

    def test_normal_file_in_home_no_warnings(self):
        assert server._storage_warnings("/home1/alice/code/train.py", 1000) == []

    def test_both_extension_and_dir_warn_together(self):
        # .ckpt extension AND /checkpoints/ dir both trigger
        warnings = server._storage_warnings(
            "/home1/alice/checkpoints/epoch_5.ckpt", 0
        )
        assert len(warnings) == 2


# ---------------------------------------------------------------------------
# _poll_job_state — squeue-first with sacct fallback
# ---------------------------------------------------------------------------

def _fake_run_factory(responses):
    """Return an async _run stub that pops (stdout, stderr, rc) from responses."""
    calls = []

    async def fake_run(cmd, cwd=None, timeout=60):
        calls.append(cmd[0] if isinstance(cmd, list) else cmd)
        return responses.pop(0)

    fake_run.calls = calls  # attach for assertions
    return fake_run


class TestPollJobState:
    @pytest.mark.asyncio
    async def test_squeue_running_skips_sacct(self, monkeypatch):
        squeue_row = "342786|mcp_test|RUNNING|0:15|2:00|n1|cpu-max10"
        fake = _fake_run_factory([(squeue_row, "", 0)])
        monkeypatch.setattr(server, "_run", fake)

        state, summary = await server._poll_job_state("342786")

        assert state == "RUNNING"
        assert "via squeue" in summary
        assert "n1" in summary
        assert "cpu-max10" in summary
        assert fake.calls == ["squeue"], "sacct should NOT be called when squeue has the job"

    @pytest.mark.asyncio
    async def test_squeue_pending_returns_pending(self, monkeypatch):
        squeue_row = "342786|mcp_test|PENDING|0:00|2:00|(None)|cpu-max10"
        fake = _fake_run_factory([(squeue_row, "", 0)])
        monkeypatch.setattr(server, "_run", fake)

        state, _ = await server._poll_job_state("342786")
        assert state == "PENDING"

    @pytest.mark.asyncio
    async def test_squeue_empty_sacct_completed(self, monkeypatch):
        # Parent step row has empty MaxRSS; the .batch step row carries it.
        sacct_rows = (
            "342786|mcp_test|COMPLETED|0:0|00:00:25||n1\n"
            "342786.batch|batch|COMPLETED|0:0|00:00:25|2048K|n1"
        )
        fake = _fake_run_factory([
            ("", "", 0),            # squeue empty (job left the queue)
            (sacct_rows, "", 0),    # sacct has final state
        ])
        monkeypatch.setattr(server, "_run", fake)

        state, summary = await server._poll_job_state("342786")

        assert state == "COMPLETED"
        assert "via sacct" in summary
        assert "MaxRSS: 2048K" in summary, "MaxRSS should be picked up from .batch step row"
        assert fake.calls == ["squeue", "sacct"]

    @pytest.mark.asyncio
    async def test_sacct_cancelled_suffix_stripped(self, monkeypatch):
        # sacct reports "CANCELLED by <uid>" for user cancellations
        sacct_row = "342786|mcp_test|CANCELLED by 1000|0:0|00:00:05||n1"
        fake = _fake_run_factory([("", "", 0), (sacct_row, "", 0)])
        monkeypatch.setattr(server, "_run", fake)

        state, summary = await server._poll_job_state("342786")

        assert state == "CANCELLED", "the ' by <uid>' suffix should be stripped"
        assert "CANCELLED by 1000" in summary, "raw state preserved in summary for context"

    @pytest.mark.asyncio
    async def test_slurmdbd_down_returns_unknown_with_detail(self, monkeypatch):
        # squeue empty + sacct fails with slurmdbd connection refused
        fake = _fake_run_factory([
            ("", "", 0),
            ("", "sacct: error: Connection refused", 1),
        ])
        monkeypatch.setattr(server, "_run", fake)

        state, summary = await server._poll_job_state("342786")

        assert state == "UNKNOWN"
        assert "unavailable" in summary.lower() or "slurmdbd" in summary.lower()

    @pytest.mark.asyncio
    async def test_job_not_found_anywhere(self, monkeypatch):
        fake = _fake_run_factory([("", "", 0), ("", "", 0)])
        monkeypatch.setattr(server, "_run", fake)

        state, summary = await server._poll_job_state("999999")

        assert state == "UNKNOWN"
        assert "not found" in summary.lower()
