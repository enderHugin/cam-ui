@echo off
setlocal EnableExtensions
title Instalador de cam-ui

rem Instala (o actualiza) cam-ui:
rem   - descarga camui.py
rem   - crea un entorno de Python propio (no toca tu Python)
rem   - instala numpy, pillow, pystray y pyvirtualcam
rem   - descarga ffmpeg.exe si no esta
rem   - crea un acceso en el menu Inicio y abre el programa
rem Para quitarlo:  cam-ui-instalar.cmd desinstalar
rem Para instalar en otra carpeta/unidad sin que te lo pregunte:
rem   set CAMUI_INSTALL_DIR=D:\cam-ui   y luego ejecuta este archivo desde esa misma ventana

set "BASE=https://enderhugin.github.io/cam-ui/dl"
set "FFZIP=https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
if defined CAMUI_INSTALL_DIR (set "DEST=%CAMUI_INSTALL_DIR%") else (set "DEST=%LOCALAPPDATA%\cam-ui")

if /i "%~1"=="desinstalar" goto :uninstall

echo.
echo  cam-ui - instalacion
echo.
set /p "ASK=  Carpeta de instalacion [%DEST%] (pulsa Intro para usar esta): "
if not "%ASK%"=="" set "DEST=%ASK%"
echo  Instalando en: %DEST%
echo.

rem ---- 0. espacio libre en esa unidad (aviso, no bloquea: por si el calculo falla)
call :freemb "%DEST%" DESTFREE
echo %DESTFREE%| findstr /r "^[0-9][0-9]*$" >nul
if errorlevel 1 goto :spaceok
echo  (espacio libre detectado en esa unidad: %DESTFREE% MB)
if %DESTFREE% GEQ 1200 goto :spaceok
echo.
echo  Aviso: esa unidad tiene poco espacio libre ^(unos %DESTFREE% MB^).
echo  cam-ui necesita alrededor de 1 GB entre Python, ffmpeg y las librerias.
echo.
echo  Para liberar espacio: Configuracion - Sistema - Almacenamiento - "Liberar
echo  espacio ahora" (tambien ayuda vaciar la papelera y la carpeta Descargas).
echo.
set /p "ASK2=  Escribe otra carpeta/unidad (o pulsa Intro para seguir igualmente): "
if not "%ASK2%"=="" set "DEST=%ASK2%"
echo.
:spaceok

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
rem --no-cache-dir: ocupa menos y evita depender de la cache de pip (a veces corrupta)
"%DEST%\venv\Scripts\python.exe" -m pip install --disable-pip-version-check --no-cache-dir -q numpy pillow
if errorlevel 1 goto :failpkg
rem opcionales: sin ellos cam-ui funciona, pero sin icono de bandeja / camara virtual
"%DEST%\venv\Scripts\python.exe" -m pip install --disable-pip-version-check --no-cache-dir -q pystray
"%DEST%\venv\Scripts\python.exe" -m pip install --disable-pip-version-check --no-cache-dir -q pyvirtualcam

rem ---- 4. ffmpeg
if exist "%DEST%\bin\ffmpeg.exe" goto :ffok
echo  [3/4] Descargando ffmpeg (unos 100 MB, solo la primera vez)...
rem extrae solo ffmpeg.exe y ffplay.exe del zip (no todo el paquete): mas rapido, menos
rem espacio y evita un fallo conocido de Expand-Archive al limpiar tras descomprimir
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $z=Join-Path $env:TEMP 'cam-ui-ffmpeg.zip'; Invoke-WebRequest -UseBasicParsing '%FFZIP%' -OutFile $z; Add-Type -AssemblyName System.IO.Compression.FileSystem; $zip=[System.IO.Compression.ZipFile]::OpenRead($z); try { $got=$false; foreach($n in @('ffmpeg.exe','ffplay.exe')) { $e=$zip.Entries | Where-Object { $_.Name -eq $n -and $_.FullName -like '*/bin/*' } | Select-Object -First 1; if ($e) { [System.IO.Compression.ZipFileExtensions]::ExtractToFile($e, (Join-Path '%DEST%\bin' $n), $true); if ($n -eq 'ffmpeg.exe') { $got=$true } } } if (-not $got) { throw 'ffmpeg.exe no estaba en el zip descargado' } } finally { $zip.Dispose() }; Remove-Item $z -Force"
if errorlevel 1 goto :failffmpeg
:ffok

rem ---- 5. acceso en el menu Inicio y abrir
echo  [4/4] Creando el acceso en el menu Inicio...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Programs')+'\cam-ui.lnk'); $s.TargetPath='%DEST%\venv\Scripts\pythonw.exe'; $s.Arguments='camui.py'; $s.WorkingDirectory='%DEST%'; $s.Description='cam-ui'; $s.Save()"
echo.
echo  Listo. Abriendo cam-ui... (la proxima vez: menu Inicio, "cam-ui")
start "" /D "%DEST%" "%DEST%\venv\Scripts\pythonw.exe" camui.py
timeout /t 4 >nul
exit /b 0

rem ---- subrutina: MB libres en la unidad de %1 -> variable %2 (vacia si no se pudo calcular)
:freemb
set "%~2="
for /f "usebackq delims=" %%F in (`powershell -NoProfile -Command "try { $r = Split-Path -Qualifier '%~1'; if (-not $r) { $r = (Get-Location).Drive.Name + ':' }; $d = Get-PSDrive -Name $r.TrimEnd(':'); [math]::Floor($d.Free/1MB) } catch { '' }"`) do set "%~2=%%F"
goto :eof

:nopython
echo.
echo  No encuentro Python 3.10 o superior con Tkinter.
echo  Instalalo desde https://www.python.org/downloads/windows/
echo  (en el instalador, marca "Add python.exe to PATH") y vuelve a abrir este archivo.
start "" https://www.python.org/downloads/windows/
echo.
pause
exit /b 1

:failpkg
echo.
echo  La instalacion no ha terminado (fallo al instalar un paquete de Python).
echo  La causa mas frecuente es que la unidad "%DEST:~0,2%" se quedo sin espacio
echo  libre a mitad de la instalacion. Comprueba el espacio en esa unidad concreta
echo  (Explorador - Este equipo) y, si hace falta, vuelve a ejecutar este archivo
echo  y escribe otra carpeta/unidad cuando te lo pregunte.
echo  Si sigue fallando, cuentalo en https://github.com/enderHugin/cam-ui/issues/new/choose
echo.
pause
exit /b 1

:failffmpeg
echo.
echo  La instalacion no ha terminado al preparar ffmpeg (no es un problema de espacio).
echo  Suele ser la descarga cortada a mitad, o un antivirus bloqueando el archivo.
echo  Vuelve a abrir este archivo para reintentarlo; si sigue fallando, copia el
echo  texto de arriba y cuentalo en https://github.com/enderHugin/cam-ui/issues/new/choose
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
