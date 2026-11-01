@echo off
setlocal EnableExtensions
title Instalador de cam-ui

rem Instala (o actualiza) cam-ui en %LOCALAPPDATA%\cam-ui:
rem   - descarga camui.py
rem   - crea un entorno de Python propio (no toca tu Python)
rem   - instala numpy, pillow, pystray y pyvirtualcam
rem   - descarga ffmpeg.exe si no esta
rem   - crea un acceso en el menu Inicio y abre el programa
rem Para quitarlo:  cam-ui-instalar.cmd desinstalar

set "BASE=https://enderhugin.github.io/cam-ui/dl"
set "DEST=%LOCALAPPDATA%\cam-ui"
set "FFZIP=https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

if /i "%~1"=="desinstalar" goto :uninstall

echo.
echo  cam-ui - instalacion
echo  Carpeta: %DEST%
echo.

rem ---- 1. Python 3.10+ con Tkinter
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY goto :nopython
%PY% -c "import sys, tkinter; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 goto :nopython

rem ---- 2. programa
if not exist "%DEST%\bin" mkdir "%DEST%\bin"
echo  [1/4] Descargando cam-ui...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing '%BASE%/camui.py' -OutFile '%DEST%\camui.py'"
if errorlevel 1 goto :fail

rem ---- 3. entorno de Python y paquetes
echo  [2/4] Preparando Python (la primera vez tarda un par de minutos)...
if not exist "%DEST%\venv\Scripts\pythonw.exe" %PY% -m venv "%DEST%\venv"
if errorlevel 1 goto :fail
"%DEST%\venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q numpy pillow
if errorlevel 1 goto :fail
rem opcionales: sin ellos cam-ui funciona, pero sin icono de bandeja / camara virtual
"%DEST%\venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q pystray
"%DEST%\venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q pyvirtualcam

rem ---- 4. ffmpeg
if exist "%DEST%\bin\ffmpeg.exe" goto :ffok
echo  [3/4] Descargando ffmpeg (unos 100 MB, solo la primera vez)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $z=Join-Path $env:TEMP 'cam-ui-ffmpeg.zip'; $d=Join-Path $env:TEMP 'cam-ui-ffmpeg'; Invoke-WebRequest -UseBasicParsing '%FFZIP%' -OutFile $z; if (Test-Path $d) { Remove-Item $d -Recurse -Force }; Expand-Archive $z $d -Force; $f = Get-ChildItem $d -Recurse -Filter ffmpeg.exe | Select-Object -First 1; Copy-Item $f.FullName '%DEST%\bin\ffmpeg.exe' -Force; Remove-Item $z -Force; Remove-Item $d -Recurse -Force"
if errorlevel 1 goto :fail
:ffok

rem ---- 5. acceso en el menu Inicio y abrir
echo  [4/4] Creando el acceso en el menu Inicio...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Programs')+'\cam-ui.lnk'); $s.TargetPath='%DEST%\venv\Scripts\pythonw.exe'; $s.Arguments='camui.py'; $s.WorkingDirectory='%DEST%'; $s.Description='cam-ui'; $s.Save()"
echo.
echo  Listo. Abriendo cam-ui... (la proxima vez: menu Inicio, "cam-ui")
start "" /D "%DEST%" "%DEST%\venv\Scripts\pythonw.exe" camui.py
timeout /t 4 >nul
exit /b 0

:nopython
echo.
echo  No encuentro Python 3.10 o superior con Tkinter.
echo  Instalalo desde https://www.python.org/downloads/windows/
echo  (en el instalador, marca "Add python.exe to PATH") y vuelve a abrir este archivo.
start "" https://www.python.org/downloads/windows/
echo.
pause
exit /b 1

:fail
echo.
echo  La instalacion no ha terminado. Revisa tu conexion a internet y vuelve a abrir este archivo.
echo  Si sigue fallando, cuentalo en https://github.com/enderHugin/cam-ui/issues/new/choose
echo.
pause
exit /b 1

:uninstall
echo.
echo  Quitando cam-ui de %DEST% ...
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\cam-ui.lnk" >nul 2>&1
rmdir /s /q "%DEST%"
echo  Hecho. (Tus grabaciones no se tocan.)
timeout /t 3 >nul
exit /b 0
