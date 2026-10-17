<#
.SYNOPSIS
  Copies the XP node's executor token to the clipboard (it is never printed) so you can paste it
  into the hidden prompt of set-xp-node.sh on the Oracle API instance. Clear the clipboard after.
#>
$file = Join-Path $env:USERPROFILE "proto-state\xp-node.json"
if (-not (Test-Path $file)) { throw "Run start-xp-node.ps1 once first: it generates the token." }
$token = (Get-Content $file -Raw | ConvertFrom-Json).executor_token
Set-Clipboard -Value $token
Write-Host "Token copied to the clipboard ($($token.Length) characters). Paste it at the 'XP node token' prompt, then run: Set-Clipboard -Value ' '"
