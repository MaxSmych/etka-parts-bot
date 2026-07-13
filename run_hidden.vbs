' Запускает run_bot.bat скрыто (без окна консоли).
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.Run """" & dir & "\run_bot.bat""", 0, False
