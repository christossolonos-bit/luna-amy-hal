# Stop only Luna HalMini backends — never Cursor / Discord app / random Electron.
param(
    [Parameter(Mandatory = $true)][string]$LunaRoot,
    [string]$AmyRoot = "",
    [string]$ShareRoot = ""
)

$ErrorActionPreference = "SilentlyContinue"

function Norm([string]$p) {
    if (-not $p) { return "" }
    try { return ((Resolve-Path -LiteralPath $p -ErrorAction Stop).Path).TrimEnd('\') }
    catch { return $p.Trim().TrimEnd('\') }
}

# Keep both junction path and resolved path (e.g. HalMini-share\Luna vs Luna HalMini).
$roots = New-Object System.Collections.Generic.HashSet[string] ([StringComparer]::OrdinalIgnoreCase)
foreach ($cand in @(
    $LunaRoot,
    (Join-Path $LunaRoot "."),
    (Norm $LunaRoot)
)) {
    if ($cand) { [void]$roots.Add($cand.TrimEnd('\')) }
}

$amy = Norm $AmyRoot
$share = Norm $ShareRoot
# Never treat drive root (D:) as a match prefix — that kills the whole PC.
if ($share -match '^[A-Za-z]:\\?$') { $share = "" }

$markers = @(
    'server.chat_terminal',
    'server.server',
    '-m server.server',
    'transcribe_worker',
    'interfaces.dashboard',
    'discord-bridge',
    'vite.js',
    'vite\bin\vite',
    '\desktop\node_modules\electron',
    'desktop\node_modules\electron'
)

$denyName = [regex]::new('(?i)^(Cursor|Discord|chrome|msedge|Code|brave|firefox|slack|Spotify)(\.exe)?$')

function BelongsToLuna([string]$cmd) {
    if (-not $cmd) { return $false }
    if ($cmd -match '(?i)\\Cursor\.exe|AppData\\Local\\Programs\\cursor|\\Discord\.exe') { return $false }

    $inRoot = $false
    foreach ($r in $roots) {
        if ($r.Length -gt 3 -and $cmd.IndexOf($r, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            $inRoot = $true
            break
        }
    }
    # Amy dashboard only when clearly the dashboard module + Amy path.
    $inAmyDash = $false
    if ($amy -and $amy.Length -gt 3 -and $cmd.IndexOf($amy, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
        if ($cmd -match 'interfaces\.dashboard') { $inAmyDash = $true }
    }

    if (-not $inRoot -and -not $inAmyDash) { return $false }

    foreach ($m in $markers) {
        if ($cmd.IndexOf($m, [StringComparison]::OrdinalIgnoreCase) -ge 0) { return $true }
    }
    # Vite/npm only if under Luna client / discord-bridge / desktop.
    if ($inRoot -and ($cmd -match '(?i)[\\/](client|discord-bridge|desktop)[\\/].*\bnpm(\.cmd)?\b')) {
        return $true
    }
    return $false
}

$killed = @()
Get-CimInstance Win32_Process | ForEach-Object {
    $name = $_.Name
    if ($denyName.IsMatch($name)) { return }
    $cmd = $_.CommandLine
    if (-not (BelongsToLuna $cmd)) { return }
    try {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
        $killed += "$($_.ProcessId):$name"
    } catch {}
}

if ($killed.Count) {
    Write-Output ("stopped " + ($killed -join ", "))
} else {
    Write-Output "stopped none"
}
