"""Dialect rendering is pure string work, so the Windows/PowerShell paths are
fully testable on any OS. This is the payoff of keeping rendering (dialect)
separate from execution (base)."""

from __future__ import annotations

import pytest

from skeletonkey.shells.dialect import (
    RenderOptions,
    decode_clixml,
    env_from_b64,
    extract_json,
    parse_sentinel,
    render,
    strip_ansi,
)


def render_for(dialect, script="echo hi", **opts):
    shell = {"bash": "/bin/bash", "sh": "/bin/sh", "pwsh": "/usr/bin/pwsh",
             "powershell": "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
             "python": "/usr/bin/python3"}[dialect]
    version = opts.pop("version", None) or {"bash": (5, 2, 15), "pwsh": (7, 4, 2),
                                            "powershell": (5, 1, 0), "python": (3, 12, 0),
                                            "sh": (0,)}[dialect]
    o = RenderOptions(dialect=dialect, **opts)
    return render(script, shell_path=shell, shell_version=version, options=o)


# --------------------------------------------------------------------- bash


def test_bash_payload_is_verbatim_plus_wrapper():
    r = render_for("bash", "echo one\necho two")
    assert "echo one\necho two" in r.payload
    assert r.payload.startswith("set -o pipefail")
    assert "set -e" in r.payload
    assert r.argv[:2] == ["/bin/bash", "--noprofile"] and r.argv[-1] == "{script}"
    assert r.suffix == ".sh" and r.bom is False


def test_bash_strict_off_omits_errexit():
    r = render_for("bash", "echo hi", strict=False)
    assert "set -e" not in r.payload


def test_bash_utf8_preamble_sets_locale():
    r = render_for("bash", "echo", utf8=True)
    assert 'LC_ALL="${LC_ALL:-C.UTF-8}"' in r.payload
    assert render_for("bash", "echo", utf8=False).payload.find("LC_ALL") == -1


def test_bash_sentinel_reports_rc_cwd_and_env():
    r = render_for("bash", "true", capture_state=True, capture_env=True)
    body = f"output line\n<<<SK1|{r.token}|rc=3|done=1>>>\n<<<SK1|{r.token}|cwd=/tmp/proj>>>\n"
    d = parse_sentinel(body, r.token, dialect="bash")
    assert (d.rc, d.done, d.cwd) == (3, True, "/tmp/proj")
    assert d.head == "output line\n", "payload is returned byte-exact"


def test_bash_login_flag_switches_to_dash_l():
    assert render_for("bash", "echo", login=True).argv[1] == "-l"


def test_bash_marker_missing_means_incomplete_run():
    d = parse_sentinel("partial output only", "deadbeef", dialect="bash")
    assert d.rc is None and d.done is False
    assert d.head == "partial output only"


def test_script_that_prints_a_lookalike_sentinel_cannot_forge_completion():
    r = render_for("bash", "echo done")
    forged = f'echo "<<<SK1|ffffffff|rc=0|done=1>>>"\n{r.token}'
    d = parse_sentinel(forged, r.token, dialect="bash")
    assert d.done is False and d.rc is None, "a token we did not issue must not be honored"


def test_sh_reports_that_it_cannot_capture_env():
    """`sh` has no `compgen`; the payload must say so instead of returning a quiet lie."""
    rendered = render_for("sh", capture_state=True, capture_env=True)
    assert "cwd-only" in rendered.payload, "the note belongs where the user can read it"
    assert "--noprofile" not in " ".join(rendered.argv), "sh gets no bash-only flags"



# ----------------------------------------------------------------- powershell


def test_pwsh7_modern_preamble_and_argv():
    r = render_for("pwsh", "Get-Process")
    assert "$ErrorActionPreference = 'Stop'" in r.payload
    assert "Set-StrictMode -Version Latest" in r.payload
    assert "$PSNativeCommandUseErrorActionPreference = $true" in r.payload
    assert "$PSStyle.OutputRendering = 'PlainText'" in r.payload
    assert r.argv[:2] == ["/usr/bin/pwsh", "-NonInteractive"]
    assert r.argv[-2:] == ["-File", "{script}"]
    assert "-NoLogo" in r.argv and "-ExecutionPolicy" in r.argv
    assert r.suffix == ".ps1" and r.bom is False


def test_pwsh72_does_not_get_native_error_action_knob():
    r = render_for("pwsh", "x", version=(7, 2, 0))
    assert "$PSNativeCommandUseErrorActionPreference" in r.payload  # guarded by a version check
    assert "PSVersion.Major -eq 7" in r.payload and "Minor -ge 3" in r.payload


def test_windows_powershell_51_is_handling_aware():
    r = render_for("powershell", "Get-Service")
    assert r.bom is True, "5.1 reads BOM-less files as ANSI and mangles non-ASCII"
    assert r.payload.startswith("\ufeff")
    assert "-NoLogo" not in r.argv
    assert "Set-StrictMode -Version 2.0" in r.payload
    assert "Latest" not in r.payload
    assert "[System.Text.UTF8Encoding]::new($false)" in r.payload


def test_pwsh_utf8_on_windows_inserts_chcp():
    modern = render_for("pwsh", "x", on_windows=True)
    assert "chcp.com 65001" in modern.payload
    assert "chcp.com" not in render_for("pwsh", "x", on_windows=False).payload


def test_pwsh_exit_propagates_script_status():
    r = render_for("pwsh", "exit 42")
    tail = r.payload.strip().splitlines()[-1]
    assert tail == "exit $__sk_rc"
    assert "$LASTEXITCODE" in r.payload


def test_pwsh_sentinel_carries_env_base64():
    r = render_for("pwsh", "x", capture_state=True, capture_env=True)
    body = f"out\n<<<SK1|{r.token}|rc=0|done=1>>>\n<<<SK1|{r.token}|cwd=C:\\work>>>\n"
    d = parse_sentinel(body, r.token, dialect="pwsh")
    assert d.cwd == "C:\\work"
    assert d.head == "out\n", "byte-exact: the sentinel's own newline goes, the payload's stays"


def test_clixml_is_decoded_into_readable_errors():
    raw = ('#< CLIXML\n<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'
           '<S S="Error">Get-Process : Cannot bind argument to parameter \'Name\' because it is '
           'null.&#xD;</S></Objs>')
    clean, errors, had = decode_clixml(raw)
    assert had is True and len(errors) == 1
    assert "Cannot bind argument" in errors[0]
    assert "<Objs" not in clean and "&#xD;" not in clean


def test_clixml_tolerates_truncated_streams():
    raw = '<Objs><S S="Error">boom&#xD;</S><T S="Error">detail</T></Objs'  # truncated -> no closing tag
    _clean, errors, had = decode_clixml(raw)
    assert had and any("boom" in e for e in errors)


def test_plain_text_passes_through_clixml_decoder():
    assert decode_clixml("just a normal error\n") == ("just a normal error\n", [], False)


def test_progress_records_are_dropped_but_warnings_kept():
    raw = ('<Objs xmlns="http://schemas.microsoft.com/powershell/2004/04">'
           '<S S="Progress">33%</S><S S="Warning">careful</S></Objs>')
    clean, _errors, _ = decode_clixml("line1\n" + raw)
    assert "33%" not in clean and "careful" in clean


# --------------------------------------------------------------------- python


def test_python_runs_body_unwrapped_with_sentinel():
    r = render_for("python", "print('hi')")
    assert "print('hi')" in r.payload
    assert "exec(" not in r.payload, "body must not be wrapped in exec() - tracebacks stay honest"
    assert r.argv[:3] == ["/usr/bin/python3", "-u", "{script}"]
    assert r.suffix == ".py"


def test_python_sentinel_and_env_roundtrip():
    r = render_for("python", "pass", capture_env=True)
    import base64
    import json

    blob = base64.b64encode(json.dumps({"A": "1"}).encode()).decode()
    body = f"x\n<<<SK1|{r.token}|rc=1|done=1>>>\n<<<SK1|{r.token}|env64={blob}>>>\n"
    d = parse_sentinel(body, r.token, dialect="python")
    assert d.rc == 1
    assert env_from_b64(d.env64, dialect="python") == {"A": "1"}


# ------------------------------------------------------------------ json/ansi


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', {"a": 1}),
    ('PowerShell banner\n{"a": 1}\n', {"a": 1}),
    ('{"a": {"b": [1, 2]}} trailing', {"a": {"b": [1, 2]}}),
    ('first {"x":1}\nsecond {"y":2}', {"y": 2}),
])
def test_extract_json_tolerates_surrounding_noise(text, expected):
    obj, err = extract_json(text)
    assert obj == expected and err is None


def test_extract_json_reports_failure_reason():
    obj, err = extract_json("not json at all")
    assert obj is None and "no JSON" in err


def test_strip_ansi_removes_csi_and_osc():
    assert strip_ansi("\x1b[31mred\x1b[0m") == "red"
    assert strip_ansi("\x1b]0;title\x07body") == "body"


def test_unknown_dialect_raises_instead_of_guessing():
    from skeletonkey.shells.dialect import UnsupportedDialect

    with pytest.raises(UnsupportedDialect):
        render("echo", shell_path="/x", shell_version=(1,), options=RenderOptions(dialect="tcsh"))
