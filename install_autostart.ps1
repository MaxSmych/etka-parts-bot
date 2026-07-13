# Installs autostart at user logon WITHOUT admin rights:
# creates a shortcut in the user's Startup folder that launches the bot
# hidden via run_hidden.vbs -> run_bot.bat (auto-restart loop).
$ErrorActionPreference = 'Stop'
$dir = $PSScriptRoot
$vbs = Join-Path $dir 'run_hidden.vbs'

$startup = [Environment]::GetFolderPath('Startup')
$lnk = Join-Path $startup 'etka-bot.lnk'

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($lnk)
$sc.TargetPath = 'wscript.exe'
$sc.Arguments = '"' + $vbs + '"'
$sc.WorkingDirectory = $dir
$sc.WindowStyle = 7   # minimized (vbs itself runs the bat fully hidden)
$sc.Description = 'etka-bot Telegram bot autostart'
$sc.Save()

Write-Host "OK: autostart shortcut created -> $lnk"
