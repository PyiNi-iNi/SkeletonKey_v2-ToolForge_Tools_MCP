# Security matrix — P6 executable bypass pass

The boundary is the path sandbox (`fsx/sandbox.py`), the sentinel/CLIXML
parsers (`shells/dialect.py`), the spill writer (`core/envelope.py`), and the
ledger redactor (`core/redact.py`). Every row below is an executable test in
`tests/test_security_matrix.py`; run them with the rest of the suite, not as a
one-off script.

## Path bypass matrix

| Attempt | Outcome | Receipt |
| --- | --- | --- |
| `../../etc/passwd`, `src/../../x` | denied | `SANDBOX_VIOLATION` / `DENY_RULE` |
| absolute external path (`/tmp/…/outside`) | denied (read *and* write intent) | `SANDBOX_VIOLATION` |
| symlink escaping the root (any hop count) | denied | `SANDBOX_VIOLATION`, details carry `requested`/`resolved` (the link target) |
| `file:///etc/passwd` | **contained**: no URI scheme handling, treated as a literal relative name inside the root | resolve returns an in-root realpath |
| `..\..\etc\passwd`, `\\server\share`, `\\?\C:\…` (Windows shapes) | denied | `SANDBOX_VIOLATION` (Windows-branch tests force `_IS_WIN` so they run on Linux CI too) |
| `CON`, `nul.txt`, `LPT1`, `data.txt:evil` (devices/ADS) | denied | `BAD_ARGS` |
| `dir. ` (trailing dot/space) | Win32-normalized to `dir` **inside** the same root, or denied | `BAD_ARGS`/normalized in-root path — never an escape |
| NUL byte / control chars | denied | `SANDBOX_VIOLATION` |
| contained `..` (`a/../b`) | allowed by design | normalized in-root path |
| random path shapes (400-case property, seeded) | never escapes | every allow has an in-root realpath |

## Parser / injection / storage matrix

| Attempt | Outcome |
| --- | --- |
| foreign sentinel token in output (`<<<SK1|other…>>>`) | data, not protocol: `done` stays `False`, output unsplit |
| sentinel-shaped random noise (200 cases) | never crashes; `rc` stays `None` or int |
| CLIXML claiming anything (`<Objs` spoof, truncated) | error stream decoded; non-XML lines preserved; malformed XML tolerated |
| `shell.run env=` with a cred-shaped value | feature works (explicit allow), but the value never reaches the ledger — args redaction + result-preview pattern redaction, `secrets:*` receipt recorded |
| oversized result spill with a hostile tool id | artifact path always inside `spill_dir`; the `full copy at …` note advertises only that path |

## Dependency audit (extras only)

Core is `dependencies = []` (ADR-0001), so an audit of the installed env *is*
the extras+dev audit. As of this run: `pip-audit` — **no known
vulnerabilities**. (A sandbox-bundled `setuptools <78` triggered findings
until upgraded; that is build plumbing, not a project dependency.)
