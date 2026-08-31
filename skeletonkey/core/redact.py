"""Secret redaction for anything that leaves the process (ledger, logs, results).

Autonomous agents paste tool output straight into model context, where it may also
be persisted by the host. A `cat ~/.aws/credentials`, a `Get-Content .env`, or a
`curl -H "Authorization: Bearer ..."` must not silently exfiltrate through our own
audit trail either. Policy denies those paths first; this module is the backstop,
so its one job is to *remove* the secret - never to annotate it in place.

Table format: (label, regex, short_name, value_group)
  value_group=None  -> the whole match is the secret
  value_group=N     -> only group N is replaced, the surrounding key/label stays
"""

from __future__ import annotations

import re
from typing import Any

_PATTERNS: list[tuple[str, re.Pattern[str], str, int | None]] = [
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA|AIDA|AROA|AGPA|AIPA|ANPA|ANVA|ABIA)[0-9A-Z]{16}\b"),
     "AWS_KEY", None),
    ("gh_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "GITHUB_TOKEN", None),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "GITHUB_TOKEN", None),
    ("slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "SLACK_TOKEN", None),
    ("discord", re.compile(r"\b[Mm][M-Za-z0-9]{23,25}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}\b"),
     "DISCORD_TOKEN", None),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "JWT", None),
    ("stripe", re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b"), "STRIPE_KEY", None),
    # more specific provider prefixes first, or the generic rule claims them
    ("anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"), "API_KEY", None),
    ("openai", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "API_KEY", None),
    ("google", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "GOOGLE_KEY", None),
    ("hf", re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"), "HF_TOKEN", None),
    ("azure", re.compile(r"(?i)=([a-z0-9]{52})\b"), "AZURE_SAS", 1),
    # The body is the secret; matching only the header would leave the key material
    # in the transcript with a friendly label attached to it.
    ("pem", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:.*?)(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
                        re.S), "PRIVATE_KEY_BLOCK", None),
    ("url_creds", re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)([^/:\s@]+):([^@\s/]+)@"), "URL_USERPASS", None),
    ("auth_header", re.compile(r"(?i)\b(authorization\s*[:=]\s*)(?:bearer|basic|token|apikey)?\s*"
                               r"([A-Za-z0-9._\-+/=]{16,})"), "AUTH_HEADER", 2),
    ("conn_str", re.compile(r"(?i)\b(?:Password|Pwd|postgres(?:ql)?|mysql|mongodb(?:\+srv)?|mssql|Server)"
                            r"=([^;\s'\"]{3,})"), "CONN_SECRET", 1),
    # The optional `[A-Za-z0-9]*[-_]` prefix is what lets an env-var style name
    # (`API_TOKEN=`, `X_AUTH_TOKEN:`) match: `_` is a word character, so a plain
    # `\btoken` never fires inside `API_TOKEN` and the value slipped into the audit log.
    ("bearer", re.compile(r"(?i)\b(?:[A-Za-z0-9]*[-_])?(authorization|bearer|token|secret)"
                          r"\s*[:=]\s*([A-Za-z0-9._\-]{12,})"),
     "BEARER", 2),
    # CLI flags: `mysql --password=hunter2` and `--password hunter2` both end up in
    # the ledger verbatim, and agents paste commands as freely as output.
    ("cli_flag", re.compile(
        r"(?i)((?:--|/)(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key(?:[_-](?:id|secret))?"
        r"|secret[_-]access[_-]key|auth[_-]?token|credential))[= ]+"
        r"(\"[^\"]{4,}\"|'[^']{4,}'|[^\s\"']{4,})"), "CLI_SECRET", 2),
    # JSON/YAML/INI/`.env` key=value pairs. Two details carry the whole rule:
    # the optional `[A-Za-z0-9]*[-_]` prefix, because `_` is a word character so a bare
    # `\btoken` never fires inside `API_TOKEN`; and quotes kept *outside* the captured
    # value, since the substitution replaces exactly that span - `"key": "v"` therefore
    # becomes `"key": "***REDACTED***"` and the line stays parseable JSON (the closing
    # quote is never part of the captured value). An audit trail nobody can read is
    # no trail at all.
    ("kv_secret", re.compile(
        "(?i)\\b(?:[A-Za-z0-9]*[-_])?(password|passwd|pwd|secret|token|api[_-]?key"
        "|access[_-]?token|id[_-]?token|client[_-]?secret|session[_-]?token"
        "|private[_-]?token|auth[_-]?token|secret[_-]?key|credential[s]?)"
        "[\\x22\\x27]?\\s*[:=]\\s*[\\x22\\x27]?([^\\s\\x22\\x27,;&}]{6,})"), "SECRET", 2),
]

#: Keys whose scalar values are secrets even when the key itself is bland.
#: `value` is included as a whole segment (the publish store's arg name) but
#: NOT when extended with an underscore (e.g. `value_masked` is display data).
_KEY_ONLY = re.compile(
    r"(?i)^.*(password|secret|token|api[_-]?key|credential|private[_-]?key"
    r"|(?<![a-z0-9])value(?![a-z0-9_])).*$")

_REDACTED = "***REDACTED***"
#: Public alias for engine-level secret-arg redaction (manifest `secret_args`).
REDACTED = _REDACTED


def tail(text: str, keep: int) -> str:
    """Show only a short tail so a human can identify *which* key it was."""
    return text[-keep:] if len(text) > keep else ""


def redact_text(text: str, *, keep: int = 4) -> tuple[str, list[str]]:
    """Return (redacted_text, hit_labels). Never raises: callers include the audit
    writer, where throwing would either lose the audit row or fail the call."""
    if not text:
        return text, []
    hits: list[str] = []
    out = text
    for label, rx, name, value_group in _PATTERNS:
        def _sub(m: re.Match[str], _label: str = label, _name: str = name,
                 _grp: int | None = value_group) -> str:
            if _label not in hits:
                hits.append(_label)
            if _grp is None:
                if _label == "url_creds":
                    # keep the username (useful, not secret), drop the password
                    return f"{m.group(1)}{m.group(2)}:{_REDACTED}@"
                if _label == "pem":
                    return "***REDACTED_PRIVATE_KEY***"
                return f"***{_name}:{tail(m.group(0), keep)}***"
            # Replace exactly the captured value span. Rebuilding the text from the
            # groups lost the closing quote of a JSON value (and `.replace()` masked
            # the first lookalike anywhere in the match), so `{"token": "abc..."}`
            # came back as invalid JSON - an audit line that cannot be parsed is worse
            # than one that leaks, and neither is acceptable.
            whole = m.group(0)
            start, end = m.start(_grp) - m.start(0), m.end(_grp) - m.start(0)
            return whole[:start] + _REDACTED + whole[end:]
        try:
            out = rx.sub(_sub, out)
        except re.error:  # pragma: no cover - guards a future regex typo at import
            continue
    return out, hits


def looks_secrety(line: str) -> bool:
    """Heuristic for log lines worth suppressing entirely."""
    return bool(_KEY_ONLY.match(line)) and re.search(r"[:=]", line) is not None


def redact_obj(obj: Any, *, max_str: int = 4096) -> Any:
    """Recursively redact inside dicts/lists; keys that look secret-bearing are masked."""
    if isinstance(obj, str):
        red, _ = redact_text(obj[:max_str])
        return red
    if isinstance(obj, dict):
        out: dict[Any, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str) and _KEY_ONLY.match(k) and not isinstance(v, (dict, list)):
                out[k] = _REDACTED
            else:
                out[k] = redact_obj(v, max_str=max_str)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v, max_str=max_str) for v in obj]
    return obj


def redact_env(env: dict[str, str]) -> dict[str, str]:
    """For logs/ledger/profile: keep names, drop values of anything secret-shaped."""
    out: dict[str, str] = {}
    for k, v in env.items():
        if re.search(r"(?i)(key|token|secret|password|pass|cred|auth|cookie|session)", k):
            out[k] = f"***{len(v or '')}B***"
        else:
            out[k] = v
    return out


def redact_cli(command: str) -> str:
    """Pre-flight scrub for commands we are about to run and log."""
    text, hits = redact_text(command)
    return text if hits else command
