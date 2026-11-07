"""«Abrir en VLC» (para Chromecast, vía el Renderizador propio de VLC): sin Tk ni display, como
tests/unit_netcam.py -- aquí solo se prueba la lógica de _find_vlc() y CamApp._open_in_vlc(),
no la ventana."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui


class _FakeIpsrv:
    def __init__(self, port):
        self.port = port


class _FakeApp(camui.CamApp):              # solo para heredar _open_in_vlc; no se llama a __init__
    def __init__(self, ipout_on, port=58399):
        self.ipout_on = ipout_on
        self.ipsrv = _FakeIpsrv(port)
        self.calls = {"status": [], "popen": [], "askyesno": [], "webbrowser": [], "showerror": 0}

    def _set_status(self, txt):
        self.calls["status"].append(txt)


class _FakeMsgBox:
    def __init__(self, calls, answer):
        self._calls = calls
        self._answer = answer

    def askyesno(self, title, msg):
        self._calls["askyesno"].append((title, msg))
        return self._answer

    def showerror(self, title, msg):
        self._calls["showerror"] += 1


def _patch(app, vlc_path, answer=True, popen_raises=None):
    """Sustituye camui.messagebox/webbrowser/subprocess.Popen/_find_vlc mientras dura la prueba;
    devuelve una función para deshacerlo todo (igual que _FakeMsgBox10 en unit_netcam.py)."""
    orig = (camui.messagebox, camui.webbrowser.open, camui.subprocess.Popen, camui._find_vlc)

    def fake_open(url):
        app.calls["webbrowser"].append(url)

    def fake_popen(cmd, **kw):
        if popen_raises:
            raise popen_raises
        app.calls["popen"].append((cmd, kw))

    camui.messagebox = _FakeMsgBox(app.calls, answer)
    camui.webbrowser.open = fake_open
    camui.subprocess.Popen = fake_popen
    camui._find_vlc = lambda: vlc_path

    def restore():
        camui.messagebox, camui.webbrowser.open, camui.subprocess.Popen, camui._find_vlc = orig
    return restore


# 1) «En la red» apagada -> no intenta nada, solo avisa en el estado
app1 = _FakeApp(ipout_on=False)
undo1 = _patch(app1, vlc_path="/usr/bin/vlc")
try:
    app1._open_in_vlc()
finally:
    undo1()
assert app1.calls["popen"] == [], "no debería lanzar VLC si «En la red» está apagada"
assert app1.calls["askyesno"] == []
assert len(app1.calls["status"]) == 1 and "En la red" in app1.calls["status"][0], app1.calls
print("1) «En la red» apagada: _open_in_vlc no hace nada salvo avisar OK")

# 2) activa, VLC encontrado -> lanza VLC con la URL del stream (puerto real del IpServer, NO la
#    página HTML del visor: VLC no entiende HTML, solo /stream.mjpg)
app2 = _FakeApp(ipout_on=True, port=12345)
undo2 = _patch(app2, vlc_path="/usr/bin/vlc")
try:
    app2._open_in_vlc()
finally:
    undo2()
assert len(app2.calls["popen"]) == 1, app2.calls["popen"]
cmd, kw = app2.calls["popen"][0]
assert cmd == ["/usr/bin/vlc", "http://127.0.0.1:12345/stream.mjpg"], cmd
assert app2.calls["askyesno"] == [], "con VLC encontrado no debe preguntar nada"
assert app2.calls["showerror"] == 0
print("2) VLC encontrado: lo abre con la URL directa de /stream.mjpg (no la página HTML) OK")

# 3) activa, VLC NO encontrado, el usuario acepta -> abre la página de descarga, no lanza Popen
app3 = _FakeApp(ipout_on=True)
undo3 = _patch(app3, vlc_path=None, answer=True)
try:
    app3._open_in_vlc()
finally:
    undo3()
assert app3.calls["popen"] == [], "sin VLC no debe intentar lanzar nada"
assert len(app3.calls["askyesno"]) == 1
assert app3.calls["webbrowser"] == ["https://www.videolan.org/vlc/"], app3.calls["webbrowser"]
print("3) VLC no encontrado, el usuario acepta: abre la página de descarga de VLC OK")

# 4) activa, VLC NO encontrado, el usuario dice que no -> no abre nada
app4 = _FakeApp(ipout_on=True)
undo4 = _patch(app4, vlc_path=None, answer=False)
try:
    app4._open_in_vlc()
finally:
    undo4()
assert app4.calls["popen"] == [] and app4.calls["webbrowser"] == []
print("4) VLC no encontrado, el usuario dice que no: no abre ninguna página OK")

# 5) activa, VLC encontrado pero Popen falla (p. ej. permisos) -> aviso de error, no revienta
app5 = _FakeApp(ipout_on=True)
undo5 = _patch(app5, vlc_path="/usr/bin/vlc", popen_raises=OSError("boom"))
try:
    app5._open_in_vlc()
finally:
    undo5()
assert app5.calls["showerror"] == 1, "un fallo al lanzar VLC debe avisar, no reventar"
print("5) VLC encontrado pero falla al lanzarlo: aviso de error controlado, sin excepción OK")

# 6) _find_vlc(): si shutil.which lo encuentra (como en este sandbox, donde se instaló vlc-bin
#    para las pruebas de compatibilidad MJPEG), devuelve esa ruta sin mirar nada más
import shutil
real_which = shutil.which("vlc") or shutil.which("cvlc")
if real_which:
    found = camui._find_vlc()
    assert found in (shutil.which("vlc"), shutil.which("vlc.exe")), found
    print("6) _find_vlc(): encuentra el vlc real instalado en este sandbox vía PATH OK")
else:
    print("6) (sin vlc/cvlc en el PATH de este sandbox; se omite la comprobación por PATH real)")

# 7) _find_vlc(): simula Windows con VLC instalado en la ruta típica (Program Files), sin que
#    esté en el PATH -- comprueba la rama de reserva, no solo shutil.which
import tempfile
_orig_os_name = os.name
_orig_which = shutil.which
tmp = tempfile.mkdtemp()
pf = os.path.join(tmp, "Program Files")
vlc_dir = os.path.join(pf, "VideoLAN", "VLC")
os.makedirs(vlc_dir)
fake_exe = os.path.join(vlc_dir, "vlc.exe")
open(fake_exe, "w").close()
os.environ["ProgramFiles"] = pf
shutil.which = lambda *a, **kw: None     # como si no estuviera en el PATH
os.name = "nt"
try:
    found = camui._find_vlc()
finally:
    os.name = _orig_os_name
    shutil.which = _orig_which
    del os.environ["ProgramFiles"]
assert found == fake_exe, found
print("7) _find_vlc(): encuentra VLC en «Program Files\\VideoLAN\\VLC» aunque no esté en el PATH OK")

# 8) _find_vlc(): ni en el PATH ni en ninguna ruta típica -> None (no revienta ni inventa nada)
shutil.which = lambda *a, **kw: None
os.name = "nt"
os.environ.pop("ProgramFiles", None)
os.environ.pop("ProgramFiles(x86)", None)
os.environ.pop("ProgramW6432", None)
try:
    found = camui._find_vlc()
finally:
    os.name = _orig_os_name
    shutil.which = _orig_which
assert found is None, found
print("8) _find_vlc(): sin VLC en ningún sitio conocido, devuelve None OK")

print("TODO OK unit_vlc")
