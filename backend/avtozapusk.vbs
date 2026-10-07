' Starts the server and the CloudPub link without windows.
' A shortcut to this file sits in the Windows Startup folder.
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.Run """" & here & "\avtozapusk.cmd""", 0, False
sh.Run """" & here & "\cloudpub.cmd""", 0, False
