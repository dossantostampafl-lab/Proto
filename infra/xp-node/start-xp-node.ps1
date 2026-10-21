<#
.SYNOPSIS
  Starts the XP (B3) node on this Windows PC: MT5 bridge + the XP executor, for the PROTO API on
  Oracle to reach over Tailscale (free). DEMO account only.

.DESCRIPTION
  MT5 terminal (logged in to the XP demo account) -> MT5 bridge (127.0.0.1:8200, attach mode)
  -> XP executor (<tailscale-ip>:8101, bearer token) <- PROTO API on Oracle, over the tailnet.

  - The bridge listens on 127.0.0.1 only. The executor listens on ONE address, the Tailscale one
    (or 127.0.0.1 with -BindIp for a local test), never on 0.0.0.0.
  - The executor verifies the Risk Permits the production API signs, so it needs the API's Ed25519
    PUBLIC key (Vault secret proto-permit-public-key). Without it the script stops: a throwaway key
    would make the node refuse every order, or worse, accept orders from anything that holds it.
  - Secrets (the executor token) live in %USERPROFILE%\proto-state\xp-node.json, generated once.
    The account password is never stored; the bridge attaches to the terminal that is logged in.

.EXAMPLE
  .\start-xp-node.ps1 -Login 511447388 -PermitPublicKey "<base64 public key>"
  .\start-xp-node.ps1 -Login 511447388 -PermitPublicKey $k -BindIp 127.0.0.1 -DryRun
#>
param(
    [Parameter(Mandatory)] [string]$Login,
    [Parameter(Mandatory)] [string]$PermitPublicKey,
    [string]$Server = "XPMT5-DEMO",
    [string]$BindIp,
    [string]$Symbols = "PETR4F,VALE3F,BOVA11F",
    [string]$Repo = (Resolve-Path "$PSScriptRoot\..\..").Path,
    [string]$Mt5Path = "C:\Program Files\MetaTrader 5 Terminal\terminal64.exe",
    [int]$ExecutorPort = 8101,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$Login = $Login -replace '\D', ''   # MT5 logins are plain numbers
$state = Join-Path $env:USERPROFILE "proto-state"
New-Item -ItemType Directory -Force $state | Out-Null

if (-not $BindIp) {
    # The installer does not always put tailscale.exe on PATH (a scheduled task sees none).
    $ts = (Get-Command tailscale -ErrorAction SilentlyContinue).Source
    if (-not $ts) { $ts = Join-Path $env:ProgramFiles "Tailscale	ailscale.exe" }
    if (-not (Test-Path $ts)) { throw "Tailscale is not installed. Install it from https://tailscale.com/download, sign in, and run again." }
    $BindIp = (& $ts ip -4 | Select-Object -First 1).Trim()
    if (-not $BindIp) { throw "Tailscale has no IPv4 address: run 'tailscale up' first." }
}
if ($BindIp -eq "0.0.0.0") { throw "Refusing to bind the executor to 0.0.0.0." }

$python = Join-Path $Repo ".venv313\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = Join-Path $Repo ".venv\Scripts\python.exe" }
if (-not (Test-Path $python)) { throw "No Python environment in $Repo (.venv313 or .venv). See docs/runbooks/xp-node-on-pc.md." }

$secretsFile = Join-Path $state "xp-node.json"
if (-not (Test-Path $secretsFile)) {
    $token = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
    $bridge = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
    @{ executor_token = $token; bridge_token = $bridge } | ConvertTo-Json | Set-Content $secretsFile -Encoding ascii
}
$secrets = Get-Content $secretsFile -Raw | ConvertFrom-Json
if (-not $secrets.watchdog_token) {
    # Halt-only token for the Oracle watchdog (show-xp-node-token.ps1 -Watchdog). Added once to an
    # existing node without touching its executor and bridge tokens.
    $secrets | Add-Member -NotePropertyName watchdog_token -NotePropertyValue (
        [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N"))
    $secrets | ConvertTo-Json | Set-Content $secretsFile -Encoding ascii
}

Write-Host "XP node: demo login $Login on $Server"
Write-Host "  bridge   http://127.0.0.1:8200   (this PC only)"
Write-Host "  executor http://${BindIp}:${ExecutorPort}   (Tailscale only, bearer token)"
if ($DryRun) { Write-Host "Dry run: nothing started."; return }

if (-not (Get-Process terminal64 -ErrorAction SilentlyContinue)) {
    Write-Host "Opening the MT5 terminal: it must be logged in to the demo account."
    Start-Process $Mt5Path
    Start-Sleep 25
}

function Start-Child([string]$name, [hashtable]$envVars, [string[]]$pyArgs) {
    $exports = ($envVars.GetEnumerator() | ForEach-Object { "`$env:$($_.Key) = '$($_.Value)';" }) -join " "
    $command = "Set-Location '$Repo'; $exports & '$python' $($pyArgs -join ' ')"
    Start-Process powershell.exe -WindowStyle Minimized -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command)
}

function Wait-Health([string]$url) {
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        try { return Invoke-RestMethod $url -TimeoutSec 3 } catch { Start-Sleep 2 }
    }
    return $null
}

New-Item -ItemType Directory -Force (Join-Path $state "xp-node") | Out-Null
$bridgeEnv = @{
    MT5_LOGIN = $Login; MT5_SERVER = $Server; MT5_PATH = $Mt5Path
    MT5_BRIDGE_HOST = "127.0.0.1"; MT5_BRIDGE_PORT = "8200"
    MT5_BRIDGE_TOKEN = $secrets.bridge_token
    MT5_BRIDGE_STATE = (Join-Path $state "xp-node\mt5-bridge.sqlite3")
}
if (-not (Wait-Health "http://127.0.0.1:8200/health")) {
    Start-Child "bridge" $bridgeEnv @("-m", "apps.mt5_bridge.service") | Out-Null
}
$health = Wait-Health "http://127.0.0.1:8200/health"
if (-not $health -or -not $health.connected) { throw "The MT5 bridge did not come up: log the terminal in to the demo account." }
if ($health.account_mode -ne "DEMO") { throw "Refusing: the terminal is logged in to a $($health.account_mode) account, not a DEMO one." }
Write-Host "bridge: $($health.account_mode) on $($health.server)"

$executorEnv = @{
    EXECUTOR_VENUE = "XP_B3"; EXECUTOR_API_TOKEN = $secrets.executor_token
    EXECUTOR_WATCHDOG_TOKEN = $secrets.watchdog_token
    EXECUTOR_BRIDGE_URL = "http://127.0.0.1:8200"; EXECUTOR_BRIDGE_TOKEN = $secrets.bridge_token
    EXECUTOR_SYMBOLS = $Symbols; EXECUTOR_PERMIT_PUBLIC_KEY = $PermitPublicKey
    EXECUTOR_DATABASE_URL = ("sqlite+aiosqlite:///" + ((Join-Path $state "xp-node\executor-xp.db") -replace '\\', '/'))
}
if (-not (Wait-Health "http://${BindIp}:${ExecutorPort}/health")) {
    Start-Child "executor" $executorEnv @("-m", "uvicorn", "apps.executor.main:app", "--host", $BindIp, "--port", "$ExecutorPort") | Out-Null
}
$ex = Wait-Health "http://${BindIp}:${ExecutorPort}/health"
if (-not $ex) { throw "The XP executor did not come up." }
Write-Host "XP executor: $($ex.status) ($($ex.venue_environment))"
Write-Host "Next: run show-xp-node-token.ps1 for set-xp-node.sh on the API instance, and"
Write-Host "      show-xp-node-token.ps1 -Watchdog for set-xp-node.sh on the watchdog instance."
