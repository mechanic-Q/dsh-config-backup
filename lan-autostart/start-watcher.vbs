' DSH LAN autostart launcher (hidden window, no console flash).
' Starts the watch-lan.ps1 watcher, which brings up the LAN-bound
' "desktop-lan" host whenever the DeepSeek Harness desktop app is running.
Option Explicit

Dim fso, shell, script, pwsh, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

script = "C:\Users\LMR\.dsh\lan-autostart\watch-lan.ps1"
If Not fso.FileExists(script) Then WScript.Quit 0

' Prefer PowerShell 7, fall back to Windows PowerShell.
pwsh = "C:\Program Files\PowerShell\7\pwsh.exe"
If Not fso.FileExists(pwsh) Then pwsh = "powershell.exe"

cmd = """" & pwsh & """ -NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & script & """"
shell.Run cmd, 0, False