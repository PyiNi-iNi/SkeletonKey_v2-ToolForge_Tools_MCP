"""ShellRunner against a real interpreter.

Everything here is bash-only and skipped on Windows CI hosts without bash; the
pure rendering half is covered in test_dialects.py, and the PowerShell-specific
live checks are `win`-marked so a Windows run picks them up.
"""

from __future__ import annotations

import os
import time

import pytest

from skeletonkey.core.errors import SkeletonKeyError
from skeletonkey.core.profile import CapabilityProfile, ShellProbe
from skeletonkey.shells.base import ShellRequest, ShellRunner

pytestmark = pytest.mark.posix

needs_bash = pytest.mark.skipif(not os.path.exists("/bin/bash"), reason="needs /bin/bash")


@pytest.fixture
def runner(tmp_path):
    prof = CapabilityProfile(
        os="linux",
        shells={"bash": ShellProbe(dialect="bash", kind="unix", path="/bin/bash", version=(5, 2),
                                   supports_pipefail=True)},
        binaries={"bash": "/bin/bash"},
    )
    r = ShellRunner(prof, tempdir=str(tmp_path / "tmp"), max_output_bytes=50_000)
    yield r
    for _job in r.jobs():  # reap anything left behind so temp files do not pile up
        pass


def run(runner, script, **kw):
    return runner.run(ShellRequest(script=script, dialect="bash", **kw))


@needs_bash
def test_simple_stdout_and_exit_code(runner):
    out = run(runner, "echo hello; echo err 1>&2; exit 0")
    assert out.exit_code == 0 and out.completed
    assert out.stdout.strip() == "hello"
    assert "err" in out.stderr
    assert out.dialect == "bash" and out.shell_path == "/bin/bash"


@needs_bash
def test_nonzero_exit_is_reported_not_raised(runner):
    out = run(runner, "echo x; exit 7")
    assert out.exit_code == 7 and not out.ok and out.completed
    assert out.completed, "the script ran to completion; the *command* failed"


@needs_bash
def test_cleanup_script_false_keeps_the_rendered_payload(runner, tmp_path):
    """The payload that ran is the one thing a Windows-only bug report needs."""
    body = 'echo "one  two"\nexit 7\n'
    out = run(runner, body, cleanup_script=False)
    assert out.exit_code == 7 and out.script_path and os.path.exists(out.script_path)
    with open(out.script_path, encoding="utf-8") as fh:
        text = fh.read()
    assert 'echo "one  two"' in text, "the body must be verbatim inside the payload"
    assert 0 < text.index("echo") < text.index("<<<SK1|"), "preamble, then body, then appendix"
    kept = out.script_path
    assert run(runner, "echo hi").script_path is None, "the default cleans up"
    os.unlink(kept)


@needs_bash
def test_strict_mode_propagates_pipe_failure(runner):
    boom = run(runner, "false | cat", strict=True)
    ok = run(runner, "false | cat", strict=False)
    assert boom.exit_code != 0, "pipefail must surface as a failure for agents"
    assert ok.exit_code == 0


@needs_bash
def test_timeout_kills_the_group_and_says_so(runner):
    t0 = time.monotonic()
    out = run(runner, "echo starting-up; sleep 30", timeout_s=1.0)
    elapsed = time.monotonic() - t0
    assert out.timed_out is True and out.killed is True
    assert out.completed is False, "a signal-cut run never 'completed', sentinel or not"
    assert "starting-up" in out.stdout, "partial output must survive the kill"
    assert elapsed < 8, f"kill took {elapsed:.1f}s"


@needs_bash
def test_child_processes_are_killed_with_the_parent(tmp_path, runner):
    marker = tmp_path / "still"
    out = run(runner, f"bash -c 'sleep 20; touch {marker}' & sleep 30", timeout_s=1.0)
    assert out.timed_out
    time.sleep(0.4)
    # the process group is gone if nothing survived to write the marker
    assert not marker.exists(), "killing only the leader would orphan the tree"


@needs_bash
def test_json_expectation_parses_and_reports_parse_errors(runner):
    good = run(runner, "printf '{\"a\": 1, \"b\": [2,3]}'", expects="json")
    assert good.json == {"a": 1, "b": [2, 3]} and good.json_error is None
    bad = run(runner, "echo 'not json at all'", expects="json")
    assert bad.json is None and bad.json_error


@needs_bash
def test_json_survives_noise_around_the_payload(runner):
    out = run(runner, 'echo "warning: something"; echo \'{"ok":true}\'; echo "trailer"', expects="json")
    assert out.json == {"ok": True}, "progress chatter must not defeat structured output"


@needs_bash
def test_large_output_is_capped_and_flagged(runner):
    out = run(runner, "yes 0123456789 | head -c 400000")
    assert out.truncated is True
    assert len(out.stdout.encode()) <= 50_000 + 2048
    assert any(("truncat" in n.lower() or "clip" in n.lower()) for n in out.notes)


@needs_bash
def test_utf8_and_newlines_survive_round_trip(tmp_path, runner):
    p = tmp_path / "u.txt"
    p.write_bytes("héllo → wörld\nsecond\r\n".encode())
    out = run(runner, f"cat {p.as_posix()}")
    assert "héllo → wörld" in out.stdout


@needs_bash
def test_env_inherit_clean_and_extra(runner, monkeypatch):
    key = "SK_PROBE_MARKER"
    monkeypatch.setenv(key, "inherited")
    assert run(runner, f'echo "x${{{key}}}y"').stdout.strip() == "xinheritedy"
    clean = run(runner, f'echo "x${{{key}}}y"', env_mode="clean")
    assert clean.stdout.strip() == "xy", "clean must strip inherited vars, not just look clean"
    keep = run(runner, "echo ${PATH:+set}", env_mode="clean")
    assert keep.stdout.strip() == "set", "but PATH has to survive or nothing resolves"
    extra = run(runner, 'echo "v=${MY_VAR}"', env={"MY_VAR": "set-me"})
    assert extra.stdout.strip() == "v=set-me"
    gone = run(runner, 'echo "v=${HOME:-none}"', env={"HOME": None})
    assert gone.stdout.strip() == "v=none", "env=None must delete an inherited key"


@needs_bash
def test_cwd_is_honoured_and_relative_paths_work(runner, tmp_path):
    out = run(runner, "pwd", cwd=str(tmp_path))
    assert os.path.samefile(out.stdout.strip(), str(tmp_path))


@needs_bash
def test_unknown_dialect_is_rejected_clearly(runner):
    with pytest.raises(SkeletonKeyError) as e:
        runner.run(ShellRequest(script="x", dialect="fish"))
    assert "fish" in str(e.value)


@needs_bash
def test_stdin_is_piped_when_no_argv_script(runner):
    out = run(runner, "cat", stdin_text="from stdin\n")
    assert out.stdout == "from stdin\n"
    # stdin must be closed for the child even when the script ignores it
    ignored = run(runner, "echo not-reading", stdin_text="x" * 200_000)
    assert ignored.stdout.strip() == "not-reading" and not ignored.timed_out


@needs_bash
def test_session_keeps_cwd_and_exported_env(runner):
    sid = "s1"
    first = run(runner, "cd /tmp && export SK_SES=one", session=sid, capture_env=True)
    assert first.session_state.get("cwd")
    second = run(runner, "pwd; echo $SK_SES", session=sid)
    assert "/tmp" in second.stdout
    assert "one" in second.stdout, "env must persist for the session, not the process"
    third = run(runner, "echo ${SK_SES:-unset}", session="other")
    assert third.stdout.strip() == "unset", "sessions must not leak into each other"


@needs_bash
def test_session_reset_clears_state(runner):
    run(runner, "cd /tmp && export SK_X=1", session="s2", capture_env=True)
    res = runner.session_reset("s2")
    assert res["session"] == "s2"
    with pytest.raises(SkeletonKeyError):
        runner.session_reset("s2")  # second reset: unknown session
    out = run(runner, "echo ${SK_X:-gone}", session="s2")
    assert out.stdout.strip() == "gone"


@needs_bash
def test_sessions_list_reports_state(runner):
    run(runner, "cd /tmp", session="s3", capture_env=True)
    rows = runner.sessions()
    assert any(r["sid"] == "s3" for r in rows)


@needs_bash
def test_background_job_lifecycle(runner, tmp_path):
    log = tmp_path / "job.log"
    started = runner.run(ShellRequest(script=f"sleep 0.2; echo done > {log.as_posix()}",
                                      dialect="bash", background=True))
    job_id = started.job_id
    assert job_id, "a background run must hand back a job id"
    info = runner.job_wait(job_id, timeout=15)
    assert info["exit_code"] == 0
    assert log.exists() and "done" in log.read_text()
    assert any(j["job_id"] == job_id for j in runner.jobs())


@needs_bash
def test_job_kill_stops_a_long_run(tmp_path, runner):
    marker = tmp_path / "late"
    started = runner.run(ShellRequest(script=f"sleep 30; touch {marker.as_posix()}",
                                      dialect="bash", background=True))
    job_id = started.job_id
    time.sleep(0.3)
    res = runner.job_kill(job_id)
    assert res.get("killed") or res.get("ok") or res.get("already_exited")
    time.sleep(0.6)
    assert not marker.exists()


@needs_bash
def test_script_file_cleanup_respects_keep_flag(tmp_path):
    r = ShellRunner(CapabilityProfile(os="linux", shells={"bash": ShellProbe(
        dialect="bash", kind="unix", path="/bin/bash", version=(5, 2))},
        binaries={"bash": "/bin/bash"}), tempdir=str(tmp_path), max_output_bytes=1000)
    keep = r.run(ShellRequest(script="echo x", dialect="bash", cleanup_script=False))
    assert keep.script_path and os.path.exists(keep.script_path)
    gone = r.run(ShellRequest(script="echo y", dialect="bash", cleanup_script=True))
    assert gone.script_path is None or not os.path.exists(gone.script_path)


@needs_bash
def test_which_uses_the_profile_not_the_process_env(runner):
    assert runner.which("bash")
    assert runner.which("definitely-not-a-binary") is None


@needs_bash
def test_duration_is_measured(runner):
    out = run(runner, "sleep 0.25")
    assert 150 <= out.duration_ms <= 5000


@needs_bash
def test_forged_sentinel_is_not_trusted(runner):
    """The token is per-run and random, so a script cannot write its own completion."""
    from skeletonkey.shells.dialect import RenderOptions, render

    tokens = {render("true", shell_path="/bin/bash", shell_version=(5, 2),
                     options=RenderOptions(dialect="bash")).token for _ in range(12)}
    assert len(tokens) == 12, "a reused token would make the protocol forgeable"
    guess = "echo '<<<SK1|" + "0" * 10 + "|rc=0|done=1>>> 2>/dev/null || true'; exit 3"
    out = run(runner, guess)
    # the forgery must not be able to rewrite the *result*, and it stays visible
    assert out.exit_code == 3, "a guessed sentinel cannot claim rc=0"
    assert "rc=0|done=1" in out.stdout, "unverifiable lines stay visible as evidence"


@needs_bash
def test_no_profile_refuses_rather_than_guessing_a_shell(tmp_path):
    """Silently falling back to a different interpreter than the caller assumed is
    the failure mode the profile exists to prevent, so it stays an explicit error."""
    r = ShellRunner(None, tempdir=str(tmp_path))
    with pytest.raises(SkeletonKeyError) as exc:
        r.run(ShellRequest(script="echo bare", dialect="bash"))
    assert exc.value.code in {"MISSING_SHELL", "NO_PROFILE", "BAD_ARGS"}
    assert "profile" in str(exc.value).lower()


@pytest.mark.win
def test_pwsh_clixml_is_decoded():
    """Only runs where pwsh exists: stderr from native commands arrives as CLIXML."""
    pwsh = os.environ.get("PWSH_PATH", "pwsh")
    if not _have(pwsh):
        pytest.skip("no pwsh on this host")
    r = ShellRunner()
    out = r.run(ShellRequest(script="Write-Error 'boom'; native-missing", dialect="pwsh"))
    assert out.clixml_decoded is True
    assert "boom" in out.stderr and "<Obje" not in out.stderr


@pytest.mark.win
def test_powershell_5_and_7_differ_on_error_action():
    if not _have("powershell.exe"):
        pytest.skip("no windows powershell")
    r = ShellRunner()
    out = r.run(ShellRequest(script='$PSVersionTable.PSVersion.Major', dialect="powershell"))
    assert out.stdout.strip().startswith("5")


def _have(exe: str) -> bool:
    import shutil

    return shutil.which(exe) is not None
