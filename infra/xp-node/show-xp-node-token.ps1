<#
.SYNOPSIS
  Copies one XP node token to the clipboard (it is never printed) so you can paste it into the
  hidden prompt of set-xp-node.sh on Oracle. Clear the clipboard after.
  Default: the executor token, for the API instance. -Watchdog: the halt-only watchdog token, for
  the watchdog instance (it can read the node's status and halt it, nothing else).
#>
param([switch]$Watchdog)
$file = Join-Path $env:USERPROFILE "proto-state\xp-node.json"
if (-not (Test-Path $file)) { throw "Run start-xp-node.ps1 once first: it generates the tokens." }
$secrets = Get-Content $file -Raw | ConvertFrom-Json
$token = if ($Watchdog) { $secrets.watchdog_token } else { $secrets.executor_token }
if (-not $token) { throw "No such token yet: run start-xp-node.ps1 again, it adds it." }
$prompt = if ($Watchdog) { "XP node watchdog token" } else { "XP node token" }
Set-Clipboard -Value $token
Write-Host "Token copied to the clipboard ($($token.Length) characters). Paste it at the '$prompt' prompt, then run: Set-Clipboard -Value ' '"
