' Starts the panel with no window: what the Windows Startup shortcut and the weekday task run
' ("start.bat --autostart" / "--weekday-start" set them up - start.py). With no window there is nothing to
' close by mistake. Its log is data\logs\panel.log; to stop it, use the Stop button in the panel
' (http://127.0.0.1:8000). Any arguments go on to start.bat, e.g. --no-browser.
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
args = ""
For Each arg In WScript.Arguments
    args = args & " " & arg
Next
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = folder
shell.Run "cmd.exe /c """"" & folder & "\start.bat""" & args & """", 0, False
