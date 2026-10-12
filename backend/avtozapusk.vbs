' Starts the server and the CloudPub link without windows.
' Runs at logon (shortcut in the Startup folder) and every 5 minutes from
' Task Scheduler as a watchdog, so it only starts what is not running yet.
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
Set wmi = GetObject("winmgmts:\\.\root\cimv2")

Function Running(script)
    Running = False
    Set procs = wmi.ExecQuery("SELECT CommandLine FROM Win32_Process WHERE Name = 'cmd.exe'")
    For Each p In procs
        If Not IsNull(p.CommandLine) Then
            If InStr(1, p.CommandLine, script, 1) > 0 Then Running = True
        End If
    Next
End Function

If Not Running("avtozapusk.cmd") Then sh.Run """" & here & "\avtozapusk.cmd""", 0, False
If Not Running("cloudpub.cmd") Then sh.Run """" & here & "\cloudpub.cmd""", 0, False
