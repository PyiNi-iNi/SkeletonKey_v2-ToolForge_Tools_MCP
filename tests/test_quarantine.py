"""Tests for the P7 quarantine module (skeletonkey.core.quarantine)."""

from __future__ import annotations

import os

import pytest

from skeletonkey.core.quarantine import (
    QuarantineSpec,
    call_quarantined,
    create_session,
    promote,
    run_once,
)

# ---- QuarantineSpec tests --------------------------------------------------

def test_default_spec():
    spec = QuarantineSpec()
    assert spec.timeout_s == 10.0
    assert spec.max_output_bytes == 65_536
    assert spec.clean_env is True
    assert "PATH" in spec.allowed_env


def test_custom_spec():
    spec = QuarantineSpec(timeout_s=5.0, max_output_bytes=1024)
    assert spec.timeout_s == 5.0
    assert spec.max_output_bytes == 1024


# ---- QuarantineSession tests -----------------------------------------------

def test_create_session():
    session = create_session("test-tool")
    assert session.tool_name == "test-tool"
    assert session.id.startswith("q-")
    assert os.path.isdir(session.scratch_dir)
    assert session.calls == 0
    assert session.successes == 0
    assert session.failures == 0
    session.cleanup()


def test_session_cleanup_removes_scratch():
    session = create_session("cleanup-test")
    scratch = session.scratch_dir
    assert os.path.isdir(scratch)
    session.cleanup()
    assert not os.path.isdir(scratch)


def test_session_to_dict():
    session = create_session("dict-test")
    d = session.to_dict()
    assert d["tool_name"] == "dict-test"
    assert "id" in d
    assert "scratch_dir" in d
    session.cleanup()


def test_promotion_eligible():
    session = create_session("promo-test")
    assert not session.promotion_eligible
    session.successes = 1
    assert session.promotion_eligible
    session.failures = 2
    assert not session.promotion_eligible
    session.cleanup()


# ---- call_quarantined tests ------------------------------------------------

def test_call_simple_code():
    session = create_session("simple-test", spec=QuarantineSpec(timeout_s=5.0))
    code = "result = {'answer': 42}"
    r = call_quarantined(session, code)
    assert r.ok is True
    assert r.output == {"answer": 42}
    assert session.calls == 1
    assert session.successes == 1
    session.cleanup()


def test_call_with_args():
    session = create_session("args-test", spec=QuarantineSpec(timeout_s=5.0))
    code = """
import json
result = {"received": args}
"""
    r = call_quarantined(session, code, args={"key": "value"})
    assert r.ok is True
    assert r.output["received"] == {"key": "value"}
    session.cleanup()


def test_call_failure_captured():
    session = create_session("fail-test", spec=QuarantineSpec(timeout_s=5.0))
    code = "raise ValueError('test error')"
    r = call_quarantined(session, code)
    assert r.ok is False
    assert session.failures == 1
    assert "ValueError" in r.error or "exit code" in r.error
    session.cleanup()


def test_call_timeout():
    session = create_session(
        "timeout-test",
        spec=QuarantineSpec(timeout_s=0.5),
    )
    code = "import time; time.sleep(10); result = 'never'"
    r = call_quarantined(session, code)
    assert r.ok is False
    assert "timeout" in r.error.lower()
    session.cleanup()


def test_call_sets_result_required():
    """Code that doesn't set 'result' should fail."""
    session = create_session("no-result", spec=QuarantineSpec(timeout_s=5.0))
    code = "x = 42  # no result variable"
    r = call_quarantined(session, code)
    # Should fail because 'result' is not defined
    assert r.ok is False
    session.cleanup()


def test_quarantine_does_not_inherit_secrets():
    """The subprocess should not have access to parent secrets."""
    session = create_session(
        "secret-test",
        spec=QuarantineSpec(timeout_s=5.0, clean_env=True),
    )
    os.environ["SKELETONKEY_SECRET_TEST"] = "super-secret-value"
    try:
        code = """
import os
has_secret = 'SKELETONKEY_SECRET_TEST' in os.environ
result = {'has_secret': has_secret}
"""
        r = call_quarantined(session, code)
        assert r.ok is True
        assert r.output["has_secret"] is False
    finally:
        del os.environ["SKELETONKEY_SECRET_TEST"]
        session.cleanup()


def test_quarantine_scratch_dir_is_writable():
    """The subprocess should be able to write to the scratch dir."""
    session = create_session("scratch-test", spec=QuarantineSpec(timeout_s=5.0))
    code = """
import os
scratch = os.environ.get('SK_SCRATCH', '.')
path = os.path.join(scratch, 'test_output.txt')
with open(path, 'w') as f:
    f.write('hello')
result = {'wrote': os.path.exists(path)}
"""
    r = call_quarantined(session, code)
    assert r.ok is True
    assert r.output["wrote"] is True
    session.cleanup()


# ---- promote tests ---------------------------------------------------------

def test_promote_after_success():
    session = create_session("promo-test", spec=QuarantineSpec(timeout_s=5.0))
    code = "result = 'ok'"
    call_quarantined(session, code)
    assert promote(session) is True
    assert session.promoted is True
    session.cleanup()


def test_promote_fails_without_success():
    session = create_session("no-promo")
    assert promote(session) is False
    assert session.promoted is False
    session.cleanup()


def test_call_after_promote_raises():
    session = create_session("post-promo", spec=QuarantineSpec(timeout_s=5.0))
    code = "result = 'ok'"
    call_quarantined(session, code)
    promote(session)
    with pytest.raises(Exception, match="already promoted"):
        call_quarantined(session, code)
    session.cleanup()


# ---- run_once convenience --------------------------------------------------

def test_run_once():
    result, session = run_once(
        "once-test",
        "result = {'ran': True}",
        spec=QuarantineSpec(timeout_s=5.0),
    )
    assert result.ok is True
    assert result.output == {"ran": True}
    assert session.calls == 1
    session.cleanup()
