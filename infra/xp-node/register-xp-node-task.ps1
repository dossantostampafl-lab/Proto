<#
.SYNOPSIS
  Starts the XP node every weekday before the B3 session and stops it after (local PC time must be
  Brasilia time). B3 regular hours are 10:00 to 17:55; the node is up 09:30 to 18:10.
  Outside that window the API sees the XP executor as unavailable and refuses B3 risk (fail-closed);
  Binance is unaffected.
#>
param(
    [Parameter(Mandatory)] [string]$Login,
    [Parameter(Mandatory)] [string]$PermitPublicKey
)
$ErrorActionPreference = "Stop"
$start = Join-Path $PSScriptRoot "start-xp-node.ps1"
$stopScript = {
    Get-CimInstance Win32_Process | Where-Object {
        $_.Name -like "python*.exe" -and $_.CommandLine -match "apps\.mt5_bridge|apps\.executor\.main"
    } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
}.ToString()
$stopFile = Join-Path $env:USERPROFILE "proto-state\stop-xp-node.ps1"
Set-Content -Path $stopFile -Value $stopScript -Encoding ascii

$days = "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"
$startAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
    "-NoProfile -ExecutionPolicy Bypass -File `"$start`" -Login $Login -PermitPublicKey `"$PermitPublicKey`"")
$stopAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$stopFile`""
Register-ScheduledTask -TaskName "PROTO XP node start" -Force -Action $startAction `
    -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At "09:30") | Out-Null
Register-ScheduledTask -TaskName "PROTO XP node stop" -Force -Action $stopAction `
    -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $days -At "18:10") | Out-Null
Write-Host "Scheduled: start 09:30, stop 18:10, Monday to Friday (this PC's local time)."
Write-Host "The MT5 terminal must be logged in to the demo account and the PC awake at 09:30."
