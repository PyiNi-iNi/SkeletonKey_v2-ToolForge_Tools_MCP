# Probing PowerShell when you are not on Windows

`shell.available` answers "what can I run *here*". Before shipping a script that
targets Windows, reproduce a Windows-shaped profile locally, and validate the two
things that actually differ: the rendered payload and the parse of the result.

## 1. Render-only checks (no interpreter needed)

```python
from skeletonkey.shells.dialect import render, RenderOptions

for dialect, ver in [("pwsh", (7, 4)), ("powershell", (5, 1))]:
    r = render("Get-ChildItem | Select-Object -First 1",
               shell_path=f"/fake/{dialect}", shell_version=ver,
               options=RenderOptions(dialect=dialect, on_windows=True,
                                     capture_env=True))
    print(dialect, r.argv, r.suffix, r.bom)
    print(r.payload)
```

Expect, and assert in tests:

- `-NonInteractive -NoProfile -ExecutionPolicy Bypass -File {script}` on both.
- a UTF-8 **BOM** only for 5.1 (`bom=True`) — that is what stops its ANSI-default
  parser from mangling non-ASCII literals.
- `$PSNativeCommandUseErrorActionPreference` and `-ErrorActionPreference Stop` in
  the preamble; on 5.1, `Set-StrictMode -Version 2.0` instead of `Latest`.
- the sentinel appendix: `exit $__sk_rc` so the child's code survives the wrapper.

## 2. Run the same probe on both platforms and diff

`scripts/selftest.ps1` and `scripts/selftest.sh` emit one JSON object describing
the interpreter's real behaviour: separator handling, `$null` vs empty string,
redirection encoding, whether a native failure throws, console encoding. Run them
via `shell.run {expects: "json"}` on a Windows box and on Linux (the `.sh` one),
then diff the JSON. Any divergence in those six fields is a bug you will hit in
production, and now you know it before an agent burns its budget discovering it.

```
shell.run {dialect: "pwsh", script: "& './scripts/selftest.ps1'", expects: "json"}
```

## 3. Where to get a real Windows to test against

| Option | Fidelity | Notes |
| --- | --- | --- |
| GitHub Actions `windows-latest` | high | free for OSS; `shell: pwsh` step; caching of `winget install Microsoft.PowerShell` if needed |
| UTM/QEMU Windows VM | high | offline; snapshot before running anything destructive |
| `docker run --platform windows/amd64` | medium | needs Windows containers host |
| Wine + PowerShell | low | quoting bugs differ from real Win32; do not trust it for path tests |
| This repo's `win` pytest marker | n/a | the tests are *written*; they run only where pwsh exists |

`pytest -m win` is skipped automatically when `pwsh`/`powershell.exe` is absent, so
a red POSIX run never means "PowerShell support is broken" and a green one never
means "it works on Windows". Only the first CI run on a Windows runner makes that
claim true, which is why `docs/PLAN.md` gates Phase 1 exit on it.

## 4. The two bugs this procedure found here

1. `strip_ansi` did not remove OSC sequences, so hyperlink escapes leaked into
   agent context.
2. stderr from a native command arrives as CLIXML *and* plain text interleaved;
   decoding only the XML left a "Cannot bind argument" line invisible, which is
   why `decode_clixml` returns `(clean_text, error_lines)` and the runner keeps the
   plain text when the XML yields nothing.
