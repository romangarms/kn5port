@echo off
rem One-click build of dist\kn5port.exe: double-click this file. See packaging\build.ps1 (-Fresh rebuilds the build Python).
rem PSModulePath is cleared so Windows PowerShell loads its own modules, not PowerShell 7's when started from a pwsh terminal
setlocal
set PSModulePath=
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\build.ps1" -Pause %*
exit /b %ERRORLEVEL%
