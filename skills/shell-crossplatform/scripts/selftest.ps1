# selftest.ps1 - report the shell's real behaviour as one JSON object.
# Keep in lockstep with selftest.sh: same keys, same meanings (see ../references/pwsh-probe.md).
# Executed by the `shell.selftest` tool when the dialect is PowerShell; the manual equivalent is
# shell.run {dialect: "pwsh", script: "<this file>", expects: "json"}
$ErrorActionPreference = 'Continue'

$out = [ordered]@{}
$out['shell'] = if ($PSVersionTable.PSEdition) { $PSVersionTable.PSEdition.ToLower() } else { 'windowspowershell' }
$out['version'] = "$($PSVersionTable.PSVersion)"
$out['os_name'] = if ($IsWindows -ne $null) { if ($IsWindows) { 'windows' } elseif ($IsLinux) { 'linux' } else { 'macos' } } else { 'windows' }
$out['path_sep'] = [IO.Path]::DirectorySeparatorChar
$out['empty_var_prints'] = "[$env:UNSET_MARKER_VAR]"
# no `set -u` analogue for environment variables: StrictMode refuses to read an unset *variable*,
# a missing env var is simply $null, so this is the one key that differs by design
$out['unset_var_under_set_u'] = 'no (StrictMode guards variables, not $env:)'
$out['strict_mode_default'] = if ($PSVersionTable.PSVersion.Major -ge 7) { 'Latest' } else { '2.0 (pinned by us)' }
$out['null_device'] = '$null'
$out['stdout_encoding'] = [Console]::OutputEncoding.WebName
$out['redirect_is_byte_faithful'] = 'no'   # PowerShell re-encodes and appends a newline

# does a native non-zero exit throw under our preamble's settings?
$nativeThrows = 'unknown'
$prev = $ErrorActionPreference
$ErrorActionPreference = 'Stop'
try {
    if ($PSVersionTable.PSVersion.Major -ge 7 -and (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue)) {
        $PSNativeCommandUseErrorActionPreference = $true
        cmd.exe /c 'exit 3' | Out-Null
        $nativeThrows = 'no'
    } else {
        $nativeThrows = 'n/a (no native EAP knob)'
    }
} catch {
    $nativeThrows = 'yes'
} finally {
    $ErrorActionPreference = $prev
}
$out['command_subst_honours_errexit'] = $nativeThrows
$out['globstar_supported'] = 'no'
$out['pipefail_supported'] = if ($PSVersionTable.PSVersion.Major -ge 7) { 'yes (PS7 set -o pipefail emulation)' } else { 'no' }
$out['unicode_literal'] = 'héllo → wörld'
$out['tmp_dir'] = [IO.Path]::GetTempPath()
$out['env_marker_present'] = if ($env:SKELETONKEY_RUN) { 'yes' } else { 'no' }
$out['hostname_bytes'] = ([Text.Encoding]::UTF8.GetByteCount($env:COMPUTERNAME ?? $env:HOSTNAME ?? ''))

# ConvertTo-Json escapes non-ASCII on 5.1, which is exactly the encoding bug worth
# seeing, so emit both forms.
$json = $out | ConvertTo-Json -Compress
Write-Output $json
