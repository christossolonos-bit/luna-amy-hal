# Start Luna backends with no console windows.
# Electron avatar is started separately (visible). Browser opens the HalMini/Amy dashboard.
param(
    [Parameter(Mandatory = $true)][string]$Root,
    [string]$Character = "luna",
    [string]$VoiceSource = "both",
    [int]$StartDiscord = 0,
    [string]$AmyRoot = "",
    [string]$HalminiDb = "",
    [int]$DashboardPort = 8766,
    [string]$ShareRoot = ""
)

$ErrorActionPreference = "Stop"

# Keep the path as launched (may be HalMini-share\Luna junction) for share detection.
$RootGiven = $Root.TrimEnd('\')
$Root = (Resolve-Path -LiteralPath $Root).Path
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "missing $py" }

# Share root: prefer explicit / env; never collapse a junction to "D:\".
if (-not $ShareRoot) { $ShareRoot = ($env:HALMINI_SHARE -as [string]) }
if (-not $ShareRoot) {
    $parentGiven = Split-Path -Parent $RootGiven
    if (
        (Test-Path (Join-Path $parentGiven "Amy\app\conversation.py")) -or
        (Test-Path (Join-Path $parentGiven "HalMini\app\conversation.py")) -or
        (Test-Path (Join-Path $parentGiven "new-champ"))
    ) {
        $ShareRoot = $parentGiven
    }
}
if ($ShareRoot -match '^[A-Za-z]:\\?$') { $ShareRoot = "" }

if (-not $AmyRoot) {
    if ($ShareRoot -and (Test-Path (Join-Path $ShareRoot "Amy\app\conversation.py"))) {
        $AmyRoot = Join-Path $ShareRoot "Amy"
    } elseif ($ShareRoot -and (Test-Path (Join-Path $ShareRoot "HalMini\app\conversation.py"))) {
        $AmyRoot = Join-Path $ShareRoot "HalMini"
    } else {
        $AmyRoot = "D:\Amy\Amy"
    }
}
if (-not (Test-Path $AmyRoot)) { throw "missing Amy/HalMini root: $AmyRoot" }

$newChampDb = ""
if ($ShareRoot) {
    foreach ($rel in @(
        "amy-smith\data\companion.db",
        "new-champ\data\companion.db"
    )) {
        $cand = Join-Path $ShareRoot $rel
        if (Test-Path $cand) { $newChampDb = $cand; break }
    }
}

if (-not $HalminiDb) {
    $HalminiDb = Join-Path $Root "data\luna_amy.db"
}
New-Item -ItemType Directory -Force -Path (Split-Path $HalminiDb) | Out-Null

$logDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$npmCmd = $null
foreach ($c in @("npm.cmd", "npm")) {
    $g = Get-Command $c -ErrorAction SilentlyContinue
    if ($g) { $npmCmd = $g.Source; break }
}
if (-not $npmCmd) { throw "npm not found on PATH" }

$env:RIKO_CHARACTER = $Character
$env:RIKO_VOICE_SOURCE = $VoiceSource
$env:HALMINI_ENABLED = "1"
$env:HALMINI_ROOT = $AmyRoot
$env:HALMINI_SELF_NAME = "Luna"
$env:HALMINI_PERSONA = "amy"
$env:HALMINI_DB = $HalminiDb
$env:HALMINI_DASHBOARD_PORT = "$DashboardPort"
$env:HALMINI_NAME = "Luna"
if ($ShareRoot) { $env:HALMINI_SHARE = $ShareRoot }
$env:NEWCHAMP_ENABLED = "1"
$env:NEWCHAMP_DB = (Join-Path $Root "data\newchamp_memories.db")
if ($newChampDb) { $env:NEWCHAMP_IMPORT_DB = $newChampDb }

function Start-Quiet {
    param([string]$FilePath, [string]$ArgumentList, [string]$WorkDir, [string]$OutLog, [string]$ErrLog)
    Start-Process -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $OutLog `
        -RedirectStandardError $ErrLog `
        -PassThru | Out-Null
}

# API + Vite (feed Electron avatar)
Start-Quiet -FilePath $py -ArgumentList "-m server.server" -WorkDir $Root `
    -OutLog (Join-Path $logDir "server.out.log") -ErrLog (Join-Path $logDir "server.err.log")

Start-Quiet -FilePath $npmCmd -ArgumentList "run dev" -WorkDir (Join-Path $Root "client") `
    -OutLog (Join-Path $logDir "vite.out.log") -ErrLog (Join-Path $logDir "vite.err.log")

if ($StartDiscord -eq 1) {
    Start-Quiet -FilePath $npmCmd -ArgumentList "start" -WorkDir (Join-Path $Root "discord-bridge") `
        -OutLog (Join-Path $logDir "discord.out.log") -ErrLog (Join-Path $logDir "discord.err.log")
}

# Amy/HalMini dashboard (same DB Luna's brain uses) — browser UI
Start-Quiet -FilePath $py -ArgumentList "-m interfaces.dashboard --port $DashboardPort" `
    -WorkDir $AmyRoot `
    -OutLog (Join-Path $logDir "dashboard.out.log") -ErrLog (Join-Path $logDir "dashboard.err.log")

Write-Output "ok root=$Root amy=$AmyRoot share=$ShareRoot"
