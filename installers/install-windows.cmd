@echo off
rem cam-ui - instalador para Windows (no necesita permisos de administrador ni tener Python instalado).
rem Este archivo es un .cmd con un script de PowerShell dentro: lo extrae y lo ejecuta.
title cam-ui - instalador
set "CAMUI_SELF=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t=[IO.File]::ReadAllText($env:CAMUI_SELF); $i=$t.IndexOf('#'+'#PS##'); $p=Join-Path $env:TEMP 'cam-ui-setup.ps1'; [IO.File]::WriteAllText($p,$t.Substring($i),(New-Object Text.UTF8Encoding $true)); & powershell -NoProfile -ExecutionPolicy Bypass -File $p %*; exit $LASTEXITCODE"
set "CAMUI_RC=%ERRORLEVEL%"
if not "%CAMUI_RC%"=="0" pause
exit /b %CAMUI_RC%
##PS##
# ---------------------------------------------------------------------------------------------
# cam-ui - instalador de Windows.
# Todo va a carpetas del usuario (sin administrador): consigue un Python 3.12 propio con "uv" (no
# toca el Python que tengas instalado, si es que tienes alguno), prepara su entorno y deja el
# programa instalado con sus accesos directos, su icono y su entrada en "Aplicaciones instaladas".
# Volver a ejecutarlo actualiza. Para quitarlo: desde "Aplicaciones instaladas" de Windows, o
# ejecutando este archivo con "desinstalar".
# Para instalar en otra carpeta/unidad (p.ej. si %LOCALAPPDATA% anda corto de espacio):
#   set "CAMUI_INSTALL_DIR=D:\cam-ui"   y luego ejecuta este archivo desde esa misma ventana
# ---------------------------------------------------------------------------------------------
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

$Base   = '__BASE_URL__'
$FFZIP  = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
# normalmente todo va a %LOCALAPPDATA%; si esa unidad anda corta de espacio, se puede elegir
# otra con la variable CAMUI_INSTALL_DIR (p.ej.  set "CAMUI_INSTALL_DIR=D:\cam-ui"  y luego
# ejecutar este archivo desde esa misma ventana).
if ($env:CAMUI_INSTALL_DIR) {
    $Root = Join-Path $env:CAMUI_INSTALL_DIR 'data'
    $Prog = Join-Path $env:CAMUI_INSTALL_DIR 'app'
} else {
    $Root = Join-Path $env:LOCALAPPDATA 'cam-ui'
    $Prog = Join-Path $env:LOCALAPPDATA 'Programs\cam-ui'
}
$Bin    = Join-Path $Root 'bin'
$Rt     = Join-Path $Root 'runtime'
$Stage  = Join-Path $Root 'stage'
$RegKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\cam-ui'
$Total  = 5
$LogDir  = Join-Path $Root 'logs'
$LogFile = Join-Path $LogDir 'instalacion.log'
try { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null; Start-Transcript -Path $LogFile -Append -Force | Out-Null } catch { }

function Step([int]$n, [string]$text) {
    Write-Host ''
    Write-Host ("[{0}/{1}] {2}" -f $n, $Total, $text) -ForegroundColor Cyan
}
function Say([string]$text) { Write-Host ('      ' + $text) }

function Get-File([string]$url, [string]$dest, [int]$timeoutSec = 180) {
    # Invoke-WebRequest, si la conexion se queda atascada a medias (no cortada, solo sin avanzar),
    # puede quedarse colgada indefinidamente sin avisar ni fallar -- y como no se le ve progreso
    # (ver $ProgressPreference mas arriba), eso es indistinguible de verdad de una descarga lenta
    # pero viva. Se lanza en un job aparte con un limite de tiempo real: si no termina a tiempo, se
    # mata y se cuenta como un intento fallido mas (hay 3, con espera creciente entre ellos). De
    # paso, se va avisando cuanto se lleva descargado cada pocos segundos para que no parezca
    # colgado mientras de verdad esta avanzando.
    $last = $null
    for ($try = 1; $try -le 3; $try++) {
        if (Test-Path $dest) { Remove-Item $dest -Force -ErrorAction SilentlyContinue }
        $job = $null
        try {
            $job = Start-Job -ScriptBlock {
                param($u, $d)
                $ProgressPreference = 'SilentlyContinue'
                try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }
                Invoke-WebRequest -UseBasicParsing -Uri $u -OutFile $d
            } -ArgumentList $url, $dest
            $deadline = (Get-Date).AddSeconds($timeoutSec)
            $lastMb = -1
            while ($job.State -eq 'Running' -and (Get-Date) -lt $deadline) {
                Start-Sleep -Seconds 3
                if (Test-Path $dest) {
                    $mb = [math]::Floor((Get-Item $dest).Length / 1MB)
                    if ($mb -gt $lastMb) { Say ('... ' + $mb + ' MB'); $lastMb = $mb }
                }
            }
            if ($job.State -eq 'Running') {
                Stop-Job -Job $job -ErrorAction SilentlyContinue
                throw ('la descarga no termino en ' + $timeoutSec + 's (conexion muy lenta o atascada)')
            }
            Receive-Job -Job $job -ErrorAction Stop | Out-Null
            if ((Get-Item $dest -ErrorAction SilentlyContinue).Length -gt 0) { return }
            throw 'el archivo descargado esta vacio'
        } catch {
            $last = $_
        } finally {
            if ($job) { Remove-Job -Job $job -Force -ErrorAction SilentlyContinue }
        }
        Start-Sleep -Seconds (2 * $try)
    }
    throw ("No se pudo descargar " + $url + " (" + $last + "). Comprueba tu conexion a internet.")
}

function Stop-Running {
    try {
        $pat = '*' + [Management.Automation.WildcardPattern]::Escape($Prog) + '*camui.py*'
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.Name -like 'python*' -and $_.CommandLine -like $pat -and $_.ProcessId -ne $PID } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    } catch { }
}

function Show-Box([string]$text, [string]$icon) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [void][Windows.Forms.MessageBox]::Show($text, 'cam-ui', 'OK', $icon)
    } catch { }
}

# ---- "desinstalar" directo (sin pasar por el Panel de control) --------------------------------
if ($args.Count -ge 1 -and $args[0] -eq 'desinstalar') {
    $u = Join-Path $Root 'uninstall.ps1'
    if (Test-Path $u) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $u
    } else {
        Write-Host '  cam-ui no parece estar instalado.'
    }
    try { Stop-Transcript | Out-Null } catch { }
    exit 0
}

try {
    Write-Host ''
    Write-Host '  ==============================' -ForegroundColor Yellow
    Write-Host '        cam-ui'                    -ForegroundColor Yellow
    Write-Host '  ==============================' -ForegroundColor Yellow
    Write-Host '  Se instala solo para tu usuario. No hace falta nada mas: ni Python ni permisos de administrador.'
    Write-Host '  La primera vez descarga unas decenas de MB (Python y ffmpeg); puede tardar un par de minutos.'

    Say ('registro de esta instalacion: ' + $LogFile)
    Say ('Windows ' + [Environment]::OSVersion.Version + ' | PowerShell ' + $PSVersionTable.PSVersion + ' | procesador ' + $env:PROCESSOR_ARCHITECTURE + ' | usuario ' + $env:USERNAME)
    Say ('programa en ' + $Prog)
    if (-not [Environment]::Is64BitOperatingSystem) { throw 'cam-ui necesita un Windows de 64 bits.' }
    New-Item -ItemType Directory -Force -Path $Root, $Bin, $Rt, $Stage, $Prog | Out-Null
    Stop-Running

    try {
        $drive = (Get-Item $Root).PSDrive.Name
        $free = [math]::Floor((Get-PSDrive -Name $drive).Free / 1MB)
        if ($free -lt 700) { Say ("aviso: unos " + $free + " MB libres en la unidad " + $drive + ":; cam-ui necesita unos 500 MB entre Python, ffmpeg y las librerias. Para instalar en otra unidad: cierra esto, ejecuta `"set CAMUI_INSTALL_DIR=D:\cam-ui`" en una ventana de comandos y luego el instalador desde ahi.") }
    } catch { }

    # --- 1) uv: una herramienta de un solo archivo que descarga y gestiona Python -----------------------------------
    Step 1 'Preparando el gestor de Python (uv)'
    $uv = Join-Path $Bin 'uv.exe'
    $uvOk = $false
    if (Test-Path $uv) { try { & $uv --version | Out-Null; $uvOk = ($LASTEXITCODE -eq 0) } catch { $uvOk = $false } }
    if ($uvOk) {
        Say 'ya estaba instalado'
    } else {
        $zip = Join-Path $Root 'uv.zip'
        # Siempre la version de 64 bits Intel/AMD: en Windows ARM funciona (emulada) y evita librerias que aun no existen para ARM.
        Get-File 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' $zip
        $tmp = Join-Path $Root 'uv-tmp'
        if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $found = Get-ChildItem -Path $tmp -Recurse -Filter 'uv.exe' | Select-Object -First 1
        if (-not $found) { throw 'El archivo descargado de uv no contiene uv.exe.' }
        Copy-Item $found.FullName $uv -Force
        Remove-Item -Recurse -Force $tmp, $zip
        Say 'listo'
    }

    # --- 2) Python 3.12 propio (no toca el Python que tengas instalado) ---------------------------------------------
    Step 2 'Preparando Python 3.12'
    $env:UV_PYTHON_INSTALL_DIR = $Rt
    $env:UV_CACHE_DIR = Join-Path $Root 'cache'
    $env:UV_PYTHON_PREFERENCE = 'only-managed'
    $env:UV_PYTHON_DOWNLOADS = 'automatic'
    $env:UV_NO_PROGRESS = '1'
    # uv no lee el proxy de Windows: si PowerShell si llega a GitHub por uno, se lo pasamos.
    if (-not ($env:HTTPS_PROXY -or $env:https_proxy)) {
        try {
            $sp = [Net.WebRequest]::GetSystemWebProxy(); $tu = [Uri]'https://github.com'
            if (-not $sp.IsBypassed($tu)) { $pu = $sp.GetProxy($tu); if ($pu -and $pu.AbsoluteUri -ne $tu.AbsoluteUri) { $env:HTTPS_PROXY = $pu.AbsoluteUri; Say ('proxy del sistema: ' + $pu.AbsoluteUri) } }
        } catch { }
    }
    function Run-Native([string]$exe, [string[]]$nativeArgs) {
        # $ErrorActionPreference = 'Stop' (puesto al principio del script) hace que la simple
        # escritura a stderr de un programa nativo -- aunque se redirija con "2>$null" -- tire
        # abajo TODO el instalador con una excepcion que solo deja ver la primera linea (por eso
        # a veces se veia solo "Traceback (most recent call last):" y nada mas, sin ni siquiera
        # el mensaje de error pensado para esa situacion). Aqui se baja la guardia un momento
        # para poder leer el fallo entero y decidir que hacer, en vez de morir a medias.
        $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        try { $o = & $exe @nativeArgs 2>&1 | ForEach-Object { [string]$_ }; $rc = $LASTEXITCODE } catch { $o = @([string]$_); $rc = 99 }
        $ErrorActionPreference = $old
        return @{ rc = $rc; out = @($o) }
    }
    function Run-Uv([string[]]$uvArgs) {
        $r = Run-Native $uv $uvArgs
        $r.out | ForEach-Object { Write-Host ('      ' + $_) }
        return $r
    }
    $py = $null; $uvTail = ''
    $tries = @(
        @{ n = 'normal'; dir = $Rt; env = @{} },
        @{ n = 'carpeta nueva y certificados de Windows'; dir = (Join-Path $Root 'py2'); env = @{ UV_SYSTEM_CERTS = '1' } },
        @{ n = 'carpeta corta y conexion lenta'; dir = (Join-Path $env:LOCALAPPDATA 'cam-ui-py'); env = @{ UV_SYSTEM_CERTS = '1'; UV_HTTP_TIMEOUT = '300'; UV_CONCURRENT_DOWNLOADS = '1' } }
    )
    foreach ($t in $tries) {
        foreach ($k in $t.env.Keys) { Set-Item -Path ('Env:' + $k) -Value $t.env[$k] }
        if ($t -ne $tries[0]) { Say ('reintentando con ' + $t.n + '...') }
        # una carpeta de un intento anterior a medias (sin Lib\os.py) hace que uv falle al escribir dentro de ella
        New-Item -ItemType Directory -Force -Path $t.dir | Out-Null
        $env:UV_PYTHON_INSTALL_DIR = $t.dir
        Get-ChildItem -LiteralPath $t.dir -Directory -Filter 'cpython-3.12*' -ErrorAction SilentlyContinue |
            Where-Object { -not (Test-Path (Join-Path $_.FullName 'Lib\os.py')) } |
            ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
        $r = Run-Uv @('python', 'install', '3.12')
        if ($r.rc -eq 0) {
            $f = & $uv python find 3.12
            if ($LASTEXITCODE -eq 0 -and $f) { $py = ([string]@($f)[0]).Trim(); break }
        }
        $uvTail = (($r.out | Where-Object { $_.Trim() } | Select-Object -Last 4) -join ' | ')
        Start-Sleep -Seconds 2
    }
    if (-not $py -or -not (Test-Path $py)) {
        # Plan B: bajar Python con PowerShell (que si respeta tu proxy y tus certificados) y descomprimirlo con tar.
        Say 'uv no pudo bajar Python; probando una descarga directa...'
        try {
            $rel = Invoke-RestMethod -UseBasicParsing -Uri 'https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest'
            $as = @($rel.assets | Where-Object { $_.name -match '^cpython-3\.12\.\d+\+\d+-x86_64-pc-windows-msvc-install_only\.tar\.gz$' }) | Select-Object -First 1
            if (-not $as) { $as = @($rel.assets | Where-Object { $_.name -match '^cpython-3\.12\.\d+\+\d+-x86_64-pc-windows-msvc-install_only_stripped\.tar\.gz$' }) | Select-Object -First 1 }
            if (-not $as) { throw 'no hay Python 3.12 para Windows en la ultima version publicada' }
            $tgz = Join-Path $Root 'python-3.12.tar.gz'
            Get-File $as.browser_download_url $tgz
            $dest = Join-Path $Rt 'manual-3.12'
            if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
            New-Item -ItemType Directory -Force -Path $dest | Out-Null
            & tar.exe -xzf $tgz -C $dest
            if ($LASTEXITCODE -ne 0) { throw ('tar fallo (codigo ' + $LASTEXITCODE + ')') }
            $cand = Join-Path $dest 'python\python.exe'
            if (-not (Test-Path $cand)) { throw 'el archivo descargado no contiene python.exe' }
            & $cand -c "import sys; assert sys.version_info[:2] == (3, 12)"
            if ($LASTEXITCODE -ne 0) { throw 'el Python descargado no arranca' }
            $py = $cand
        } catch { $uvTail += ' | descarga directa: ' + $_.Exception.Message }
    }
    if (-not $py -or -not (Test-Path $py)) {
        throw ('No se pudo conseguir Python 3.12. Puede ser Windows Defender (acceso controlado a carpetas) u otro antivirus que borra archivos mientras se descomprimen, o un proxy o cortafuegos que bloquea GitHub. Prueba a pausar el antivirus unos minutos y vuelve a ejecutar el instalador. Ultimos mensajes: ' + $uvTail)
    }
    Say $py
    # comprobacion temprana: tkinter en el Python recien bajado (algunas compilaciones de terceros
    # lo han tenido roto alguna vez; mejor saberlo aqui que al abrir cam-ui por primera vez)
    $tkOk = $false
    try { & $py -c "import tkinter; tkinter.Tcl()" 2>$null; if ($LASTEXITCODE -eq 0) { $tkOk = $true } } catch { }
    if (-not $tkOk) {
        throw 'El Python descargado no trae tkinter funcionando (Tcl/Tk). Vuelve a ejecutar el instalador (a veces una compilacion nueva lo arregla); si sigue fallando, cuentalo en https://github.com/enderHugin/cam-ui/issues/new/choose.'
    }

    # --- 3) el programa --------------------------------------------------------------------------------------------
    Step 3 'Descargando cam-ui'
    foreach ($f in @('camui.py', 'VERSION')) { Get-File ($Base + '/' + $f) (Join-Path $Stage $f) }
    try { Get-File ($Base + '/icon.ico') (Join-Path $Stage 'icon.ico') } catch { Say 'no se pudo bajar el icono (se sigue sin el)' }
    $version = (Get-Content (Join-Path $Stage 'VERSION') -Raw).Trim()
    Say ('version ' + $version)

    # --- 4) entorno con sus librerias (la parte lenta): venv con el Python propio + numpy, pillow -------------------
    Step 4 'Instalando el programa y sus librerias'
    Copy-Item (Join-Path $Stage 'camui.py') (Join-Path $Prog 'camui.py') -Force
    if (Test-Path (Join-Path $Stage 'icon.ico')) { Copy-Item (Join-Path $Stage 'icon.ico') (Join-Path $Prog 'icon.ico') -Force }
    $venv = Join-Path $Prog 'venv'
    $vpy  = Join-Path $venv 'Scripts\python.exe'
    $vpyw = Join-Path $venv 'Scripts\pythonw.exe'
    # un entorno a medias de un intento anterior (sin pythonw.exe, o sin pip: "uv venv" no lo
    # instala salvo que se le pida con --seed) se rehace solo
    $venvOk = $false
    if (Test-Path $vpyw) {
        $rpip = Run-Native $vpy @('-c', 'import pip')
        if ($rpip.rc -eq 0) { $venvOk = $true } else { Say 'entorno anterior sin pip: se rehace' }
    }
    if (-not $venvOk -and (Test-Path $venv)) {
        Remove-Item -LiteralPath $venv -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (-not $venvOk) {
        # "uv venv" (en vez del "python -m venv" de la libreria estandar) conoce mejor las
        # compilaciones de python-build-standalone, incluido donde quedan los archivos de Tcl/Tk.
        # --seed: que incluya pip (si no, el entorno se crea sin el).
        $r = Run-Uv @('venv', '--seed', '--python', $py, $venv)
        if ($r.rc -ne 0 -or -not (Test-Path $vpyw)) { throw 'no se pudo crear el entorno de Python' }
    } else {
        Say 'el entorno ya estaba'
    }
    $rpip2 = Run-Native $vpy @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-cache-dir', '-q', '--upgrade', 'numpy', 'pillow')
    if ($rpip2.rc -ne 0) {
        throw ('fallo al instalar numpy/pillow: ' + (($rpip2.out | Where-Object { $_.Trim() } | Select-Object -Last 4) -join ' | '))
    }
    # opcionales: sin ellos cam-ui funciona igual, solo que sin icono de bandeja / camara virtual --
    # si fallan (red, antivirus...) no debe tirar abajo el resto de la instalacion, solo avisar
    $rpystray = Run-Native $vpy @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-cache-dir', '-q', '--upgrade', 'pystray')
    if ($rpystray.rc -ne 0) { Say ('no se pudo instalar pystray (opcional; sin el no habra icono de bandeja): ' + (($rpystray.out | Where-Object { $_.Trim() } | Select-Object -Last 2) -join ' | ')) }
    $rpvcam = Run-Native $vpy @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-cache-dir', '-q', '--upgrade', 'pyvirtualcam')
    if ($rpvcam.rc -ne 0) { Say ('no se pudo instalar pyvirtualcam (opcional; sin el no habra camara virtual): ' + (($rpvcam.out | Where-Object { $_.Trim() } | Select-Object -Last 2) -join ' | ')) }
    # (sin tildes a proposito en estos dos avisos: el mismo patron que el resto del instalador,
    # que evita acentos en los textos de consola por si la code page de alguna consola los destroza)

    # ffmpeg (solo si falta: no se vuelve a bajar en cada actualizacion)
    $ffdir = Join-Path $Prog 'bin'
    New-Item -ItemType Directory -Force -Path $ffdir | Out-Null
    if (-not (Test-Path (Join-Path $ffdir 'ffmpeg.exe'))) {
        Say 'descargando ffmpeg (unos 100 MB, solo la primera vez; puede tardar varios minutos)...'
        $z = Join-Path $Root 'ffmpeg.zip'
        try {
            Get-File $FFZIP $z -timeoutSec 480
        } catch {
            throw ('No se pudo descargar ffmpeg (' + $_.Exception.Message + '). Puedes bajarlo tu mismo: ' +
                   'abre ' + $FFZIP + ' con el navegador, y de dentro del zip copia "bin\ffmpeg.exe" y ' +
                   '"bin\ffplay.exe" a "' + $ffdir + '" (creala si no existe); luego vuelve a ejecutar ' +
                   'este instalador, que al verlos ya puestos seguira sin pararse aqui.')
        }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $zipf = [System.IO.Compression.ZipFile]::OpenRead($z)
        try {
            $got = $false
            foreach ($n in @('ffmpeg.exe', 'ffplay.exe')) {
                $e = $zipf.Entries | Where-Object { $_.Name -eq $n -and $_.FullName -like '*/bin/*' } | Select-Object -First 1
                if ($e) {
                    [System.IO.Compression.ZipFileExtensions]::ExtractToFile($e, (Join-Path $ffdir $n), $true)
                    if ($n -eq 'ffmpeg.exe') { $got = $true }
                }
            }
            if (-not $got) { throw 'ffmpeg.exe no estaba en el zip descargado' }
        } finally { $zipf.Dispose() }
        Remove-Item $z -Force
    } else {
        Say 'ffmpeg ya estaba'
    }

    # --- 5) icono, accesos directos, "Aplicaciones instaladas" y desinstalador --------------------------------------
    Step 5 'Terminando: accesos directos y desinstalador'
    $icoPath = Join-Path $Prog 'icon.ico'
    $hasIco = Test-Path $icoPath
    $sh = New-Object -ComObject WScript.Shell
    foreach ($dir in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {
        $lnk = Join-Path $dir 'cam-ui.lnk'
        $s = $sh.CreateShortcut($lnk)
        $s.TargetPath = $vpyw
        $s.Arguments = '"camui.py"'
        $s.WorkingDirectory = $Prog
        $s.Description = 'cam-ui'
        if ($hasIco) { $s.IconLocation = $icoPath + ',0' }
        $s.Save()
    }

    # comprobacion final de verdad: ¿cargan las librerias que usa cam-ui? (numpy, pillow, tkinter)
    Say 'comprobando que las librerias cargan...'
    $smoke = ''; $smokeRc = 1
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
        $smoke = (& $vpy -c "import numpy, PIL, tkinter; tkinter.Tcl(); print('CAMUI_OK')" 2>&1 | Out-String)
        $smokeRc = $LASTEXITCODE
    } catch { $smoke = [string]$_ }
    $ErrorActionPreference = $old
    if ($smokeRc -ne 0 -or $smoke -notmatch 'CAMUI_OK') {
        Write-Host $smoke -ForegroundColor Yellow
        $hint = ''
        if ($smoke -match 'DLL load failed') { $hint = "`n`nFalta el componente 'Microsoft Visual C++ Redistributable'. Instalalo desde https://aka.ms/vs/17/release/vc_redist.x64.exe y abre cam-ui desde el menu Inicio." }
        if ($smoke -match 'init\.tcl|TclError|_tkinter') { $hint += "`n`nEsto suele ser el entorno de Python a medias. Vuelve a ejecutar el instalador (borra primero la carpeta '" + $venv + "' si sigue fallando)." }
        $tl = ($smoke -split "`n" | Where-Object { $_.Trim() } | Select-Object -Last 6) -join "`n"
        try { Stop-Transcript | Out-Null } catch { }
        Show-Box ("cam-ui se ha instalado, pero sus librerias no cargan en este equipo, asi que no se abrira todavia.`n`n" + $tl + $hint + "`n`nRegistro: " + $LogFile) 'Warning'
        exit 2
    }
    Say 'librerias correctas'

    # desinstalador (se guarda en $Root, no en $Prog: tiene que seguir ahi aunque el usuario borre sus datos)
    $un = @'
Add-Type -AssemblyName System.Windows.Forms
$r = [Windows.Forms.MessageBox]::Show('¿Desinstalar cam-ui?', 'cam-ui', 'YesNo', 'Question')
if ($r -ne 'Yes') { exit 0 }
$d = [Windows.Forms.MessageBox]::Show("¿Borrar tambien tus capturas, grabaciones y configuracion?`n`nSi dices que no, se conservan por si vuelves a instalarlo.", 'cam-ui', 'YesNo', 'Question')
try {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.Name -like 'python*' -and $_.CommandLine -like '__PROCPAT__' -and $_.ProcessId -ne $PID } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
} catch { }
Start-Sleep -Milliseconds 800
try { Remove-Item -LiteralPath (Join-Path ([Environment]::GetFolderPath('Programs')) 'cam-ui.lnk') -Force -ErrorAction SilentlyContinue } catch { }
try { Remove-Item -LiteralPath (Join-Path ([Environment]::GetFolderPath('Desktop')) 'cam-ui.lnk') -Force -ErrorAction SilentlyContinue } catch { }
if (Test-Path '__PROG__') {
    if ($d -eq 'Yes') {
        Remove-Item -LiteralPath '__PROG__' -Recurse -Force -ErrorAction SilentlyContinue
    } else {
        Get-ChildItem -LiteralPath '__PROG__' -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notin @('config.json', 'Capturas', 'camui.log', 'actualizacion.json', 'backups_programa') } |
            ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
    }
}
Remove-Item -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\cam-ui' -Recurse -Force -ErrorAction SilentlyContinue
Start-Process -WindowStyle Hidden -FilePath cmd.exe -ArgumentList '/c ping -n 4 127.0.0.1 >nul & rmdir /s /q "__ROOT__"'
[void][Windows.Forms.MessageBox]::Show('cam-ui se ha desinstalado.', 'cam-ui', 'OK', 'Information')
'@
    $procPat = '*' + [Management.Automation.WildcardPattern]::Escape($Prog) + '*camui.py*'
    $un = $un.Replace('__PROG__', $Prog).Replace('__ROOT__', $Root).Replace('__PROCPAT__', $procPat)
    [IO.File]::WriteAllText((Join-Path $Root 'uninstall.ps1'), $un, (New-Object Text.UTF8Encoding $true))

    New-Item -Path $RegKey -Force | Out-Null
    $vals = @{
        DisplayName     = 'cam-ui'
        DisplayVersion  = $version
        Publisher       = 'cam-ui'
        InstallLocation = $Prog
        UninstallString = ('powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $Root 'uninstall.ps1') + '"')
        URLInfoAbout    = $Base
    }
    if ($hasIco) { $vals['DisplayIcon'] = $icoPath }
    foreach ($k in $vals.Keys) { New-ItemProperty -Path $RegKey -Name $k -Value $vals[$k] -PropertyType String -Force | Out-Null }
    New-ItemProperty -Path $RegKey -Name NoModify -Value 1 -PropertyType DWord -Force | Out-Null
    New-ItemProperty -Path $RegKey -Name NoRepair -Value 1 -PropertyType DWord -Force | Out-Null

    Remove-Item -Recurse -Force $Stage -ErrorAction SilentlyContinue

    Write-Host ''
    Write-Host '  Listo. cam-ui esta instalado.' -ForegroundColor Green
    Write-Host '  Lo encuentras en el menu Inicio y en el escritorio. Se abre ahora.'
    Start-Process -FilePath $vpyw -ArgumentList '"camui.py"' -WorkingDirectory $Prog
    try { Stop-Transcript | Out-Null } catch { }
    exit 0
}
catch {
    Write-Host ''
    Write-Host ('  ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host ('  ' + $_.ScriptStackTrace) -ForegroundColor DarkGray
    Show-Box ("No se pudo completar la instalacion:`n`n" + $_.Exception.Message + "`n`nPuedes volver a ejecutar el instalador: continuara donde lo dejo.`n`nRegistro: " + $LogFile) 'Error'
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}
