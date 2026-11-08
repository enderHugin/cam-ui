# -*- coding: utf-8 -*-
"""Pruebas del sistema de actualizaciones (mismo diseño que widget-arena): se levanta un
servidor HTTP local que hace de «enderhugin.github.io/cam-ui/dl/» y se ejercita update_check /
update_apply / update_rollback de verdad contra él (sin red real ni GUI)."""
import sys, os, re, json, time, hashlib, shutil, tempfile, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WORK = tempfile.mkdtemp(prefix="camui_update_test_")
DL = os.path.join(WORK, "dl")
os.makedirs(DL, exist_ok=True)

STATE = {"redirect_once": False}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/elsewhere":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"no deberias ver esto")
            return
        if path == "/dl/redirect-fuera" and STATE["redirect_once"]:
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:{0}/elsewhere".format(OTHER_PORT))
            self.end_headers()
            return
        local = os.path.join(DL, path.lstrip("/"))
        if path.startswith("/dl/") and os.path.isfile(local := os.path.join(WORK, path.lstrip("/"))):
            data = open(local, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(404)
        self.end_headers()


srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
PORT = srv.server_address[1]
other = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
OTHER_PORT = other.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
threading.Thread(target=other.serve_forever, daemon=True).start()

camui.UPDATE_BASE = "http://127.0.0.1:{0}/dl/".format(PORT)
camui.APP_FILE = os.path.join(WORK, "camui.py")
camui.UPDATE_STATE = os.path.join(WORK, "actualizacion.json")
camui.UPDATE_BACKUPS = os.path.join(WORK, "backups_programa")
camui._UPDATE_ST = {}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def publish(pack, body_extra=b"", version="9999.01.01", notes="prueba de actualizacion"):
    data = ("# -*- coding: utf-8 -*-\nAPP_PACK = {0}\n".format(pack)).encode("utf-8") + body_extra
    with open(os.path.join(DL, "camui.py"), "wb") as f:
        f.write(data)
    meta = {"camui.py": {"sha256": sha(data), "size": len(data)}}
    with open(os.path.join(DL, "update.json"), "w", encoding="utf-8") as f:
        json.dump({"pack": pack, "version": version, "notes": notes, "files": meta}, f)
    return data


def set_local(pack, data):
    camui.APP_PACK = pack
    with open(camui.APP_FILE, "wb") as f:
        f.write(data)
    camui._UPDATE_ST = {}        # que el siguiente update_check no reutilice last_check en caliente


# 1) hay una version mas nueva publicada
local_data = publish(1, notes="version local (sirve de base)")   # reutilizado como "local" tambien
set_local(1, local_data)
remote_data = publish(2, notes="novedades de la version 2")
r = camui.update_check()
assert r["ok"] and r["state"] == "newer", r
assert r["info"]["pack"] == 2 and r["local"]["pack"] == 1
print("1) update_check detecta una version mas nueva: OK")

# 2) update_apply la descarga, la valida y la instala, guardando una copia de la actual
shutil.rmtree(camui.UPDATE_BACKUPS, ignore_errors=True)
res = camui.update_apply(r["info"])
assert os.path.exists(res["backup"]), res
assert open(res["backup"], "rb").read() == local_data, "la copia de seguridad no es la version anterior"
assert open(camui.APP_FILE, "rb").read() == remote_data, "no se instalo la version nueva"
assert camui.update_backup_available()
print("2) update_apply instala la nueva version y guarda una copia: OK")

# 3) update_rollback vuelve a la copia, y a su vez guarda la que estaba puesta
res2 = camui.update_rollback()
assert open(camui.APP_FILE, "rb").read() == local_data, "el rollback no restauro la version anterior"
assert open(res2["backup"], "rb").read() == remote_data, "el rollback no guardo una copia de la que estaba puesta"
print("3) update_rollback restaura la copia anterior: OK")

# 4) mismo pack y mismo contenido -> "same"
set_local(2, remote_data)
r = camui.update_check()
assert r["ok"] and r["state"] == "same", r
print("4) update_check con la misma version -> same: OK")

# 5) mismo pack pero contenido distinto (cambios locales sin publicar) -> "diverged"
set_local(2, remote_data + b"\n# un cambio local\n")
r = camui.update_check()
assert r["ok"] and r["state"] == "diverged", r
print("5) update_check con cambios locales sin publicar -> diverged: OK")

# 6) version local mas nueva que la publicada -> "older"
set_local(3, remote_data)
r = camui.update_check()
assert r["ok"] and r["state"] == "older", r
print("6) update_check con version local mas nueva -> older: OK")

# 7) la huella publicada no coincide con lo que de verdad se sirve -> se rechaza sin tocar nada
set_local(1, local_data)
bad = publish(2, notes="con huella mala")
with open(os.path.join(DL, "update.json"), "r", encoding="utf-8") as f:
    meta = json.load(f)
meta["files"]["camui.py"]["sha256"] = "0" * 64
with open(os.path.join(DL, "update.json"), "w", encoding="utf-8") as f:
    json.dump(meta, f)
r = camui.update_check()
assert r["ok"] and r["state"] == "newer"
try:
    camui.update_apply(r["info"])
    raise AssertionError("deberia haber rechazado la huella que no coincide")
except ValueError as e:
    assert "huella" in str(e)
assert open(camui.APP_FILE, "rb").read() == local_data, "no debio tocar el archivo local"
print("7) huella sha256 que no coincide -> se rechaza sin tocar nada: OK")

# 8) el archivo descargado no trae el APP_PACK que anuncia update.json -> se rechaza
data_mismatch = b"# -*- coding: utf-8 -*-\nAPP_PACK = 999\n"
with open(os.path.join(DL, "camui.py"), "wb") as f:
    f.write(data_mismatch)
meta = {"camui.py": {"sha256": sha(data_mismatch), "size": len(data_mismatch)}}
with open(os.path.join(DL, "update.json"), "w", encoding="utf-8") as f:
    json.dump({"pack": 2, "version": "x", "notes": "", "files": meta}, f)
r = camui.update_check()
assert r["ok"] and r["state"] == "newer"
try:
    camui.update_apply(r["info"])
    raise AssertionError("deberia haber rechazado el APP_PACK que no coincide")
except ValueError as e:
    assert "anunciada" in str(e)
assert open(camui.APP_FILE, "rb").read() == local_data
print("8) APP_PACK del archivo descargado no coincide con lo anunciado -> se rechaza: OK")

# 9) python invalido -> compile() lo rechaza antes de tocar nada (defensa adicional)
bad_py = b"APP_PACK = 2\ndef mal(:\n"
meta = {"camui.py": {"sha256": sha(bad_py), "size": len(bad_py)}}
with open(os.path.join(DL, "camui.py"), "wb") as f:
    f.write(bad_py)
with open(os.path.join(DL, "update.json"), "w", encoding="utf-8") as f:
    json.dump({"pack": 2, "version": "x", "notes": "", "files": meta}, f)
r = camui.update_check()
assert r["ok"] and r["state"] == "newer"
try:
    camui.update_apply(r["info"])
    raise AssertionError("deberia haber rechazado el python invalido")
except SyntaxError:
    pass
assert open(camui.APP_FILE, "rb").read() == local_data
print("9) python invalido en el archivo publicado -> se rechaza sin tocar nada: OK")

# 10) _update_get solo sigue redirecciones dentro de UPDATE_BASE
STATE["redirect_once"] = True
try:
    camui._update_get(camui.UPDATE_BASE + "redirect-fuera", 1024)
    raise AssertionError("deberia haber rechazado la redireccion a otro sitio")
except ValueError as e:
    assert "redirec" in str(e)
print("10) _update_get rechaza una redireccion fuera de UPDATE_BASE: OK")

# 11) se conservan como mucho 5 copias de seguridad
set_local(1, local_data)
shutil.rmtree(camui.UPDATE_BACKUPS, ignore_errors=True)
for i in range(7):
    data_i = publish(2 + i, notes="v{0}".format(i))
    r = camui.update_check()
    camui.update_apply(r["info"])
    set_local(1, local_data)       # vuelve a "version vieja" para poder aplicar otra vez
    time.sleep(0.02)
kept = [f for f in os.listdir(camui.UPDATE_BACKUPS) if f.startswith("camui.pack")]
assert len(kept) == 5, kept
print("11) solo se conservan las 5 copias de seguridad mas recientes: OK")

# 12) update_set_auto_apply(True): si el aviso normal («avisarme al abrir») estaba apagado
#     (se habia marcado antes "no preguntar mas"), se reactiva solo -- si no, "actualizar
#     sola" nunca llegaria a mirar si hay algo que aplicar.
camui._UPDATE_ST = {"auto": False}
camui.update_set_auto_apply(True)
st = camui.update_state()
assert st["auto_apply"] is True and st["auto"] is True, st
print("12) update_set_auto_apply(True) reactiva el aviso normal si estaba apagado: OK")

# 13) update_set_auto_apply(False) solo apaga eso: no toca el aviso normal (que puede seguir
#     activado tal cual estaba)
camui._UPDATE_ST = {"auto": True, "auto_apply": True}
camui.update_set_auto_apply(False)
st = camui.update_state()
assert st["auto_apply"] is False and st["auto"] is True, st
print("13) update_set_auto_apply(False) no afecta al aviso normal: OK")


# 14) CamApp._update_probe_done: con "auto_apply" activado, al encontrar una version nueva se
#     instala sola -- el mismo camino que si se hubiera preguntado y dicho que si -- SIN
#     mostrar el aviso de "¿actualizar ahora?". Se llama al metodo real (sin Tk: ni self.root
#     ni self.show_updates necesitan ser de la GUI de verdad para comprobar que elige el
#     camino correcto).
class _FakeDialog:
    def __init__(self):
        self.applied_with = None

    def apply_result(self, r):
        self.applied_with = r


class _FakeApp:
    def __init__(self):
        self.recording = False
        self.root = None
        self._dlg = _FakeDialog()
        self.show_updates_calls = []

    def show_updates(self, check=True):
        self.show_updates_calls.append(check)
        return self._dlg


def _boom(*a, **kw):
    raise AssertionError("no debia preguntar: auto_apply estaba activado")


r_newer = {"ok": True, "state": "newer", "info": {"pack": 99}}
camui._UPDATE_ST = {"auto_apply": True}
old_ask = camui._ask_update_now
camui._ask_update_now = _boom
try:
    fake = _FakeApp()
    camui.CamApp._update_probe_done(fake, r_newer)
    assert fake.show_updates_calls == [False], fake.show_updates_calls
    assert fake._dlg.applied_with is r_newer, "no se aplico sola la version nueva"
finally:
    camui._ask_update_now = old_ask
print("14) _update_probe_done con auto_apply activado: se aplica sola sin preguntar: OK")

# 15) con "auto_apply" desactivado (lo normal), sigue preguntando como siempre; si el usuario
#     cancela, no se aplica nada (no se regresiona el comportamiento de antes de este cambio)
camui._UPDATE_ST = {"auto_apply": False}
asked = {"v": False}


def _fake_ask(parent, pack):
    asked["v"] = True
    return False, False          # cancela, sin marcar "no preguntar mas"


camui._ask_update_now = _fake_ask
try:
    fake2 = _FakeApp()
    camui.CamApp._update_probe_done(fake2, r_newer)
    assert asked["v"], "debia preguntar si auto_apply no esta activado"
    assert fake2.show_updates_calls == [], "no debia aplicar nada si el usuario cancelo"
finally:
    camui._ask_update_now = old_ask
print("15) _update_probe_done sin auto_apply: sigue preguntando como antes: OK")

# 16) «vigilar sola, sin avisos» (casilla oculta de UpdateDialog): _silent_watch_checked no
#     debe tocar _bg para nada salvo que de verdad haya una version mas nueva, la casilla este
#     activada, y no se este grabando ni ocupado con otra cosa a la vez.
class _FakeApp2:
    def __init__(self, silent_watch=True, recording=False, busy=False):
        self.recording = recording
        self.busy = busy
        self.bg_calls = []
        self.restart_calls = 0
        self.root = _FakeRoot()
        camui._UPDATE_ST = {"silent_watch": silent_watch}

    def _bg(self, fn, done):
        self.bg_calls.append((fn, done))

    def update_restart_app(self):
        self.restart_calls += 1

    # delegan al metodo real -- hacen falta como atributos de verdad (no solo para llamarlos a
    # mano desde la prueba) porque _silent_watch_checked/_silent_watch_tick los pasan como
    # "done"/callback a self._bg, y acceder a self._silent_watch_applied, etc. fallaria si la
    # clase fake no los tuviera definidos.
    def _silent_watch_checked(self, r):
        camui.CamApp._silent_watch_checked(self, r)

    def _silent_watch_applied(self, res):
        camui.CamApp._silent_watch_applied(self, res)

    def _silent_watch_tick(self):
        camui.CamApp._silent_watch_tick(self)


class _FakeRoot:
    def __init__(self):
        self.after_calls = []

    def after(self, ms, fn):
        self.after_calls.append((ms, fn))


r_newer2 = {"ok": True, "state": "newer", "info": {"pack": 123, "version": "1", "notes": ""}}
r_same2 = {"ok": True, "state": "same", "info": {}}
r_fail2 = {"ok": False, "error": "red caida"}

for bad_r, why in [(r_same2, "mismo pack (state=same)"), (r_fail2, "ok=False")]:
    fake = _FakeApp2(silent_watch=True)
    camui.CamApp._silent_watch_checked(fake, bad_r)
    assert fake.bg_calls == [], "no debia llamar a _bg con {0}".format(why)

fake = _FakeApp2(silent_watch=True, recording=True)
camui.CamApp._silent_watch_checked(fake, r_newer2)
assert fake.bg_calls == [], "no debia aplicar nada mientras se esta grabando"

fake = _FakeApp2(silent_watch=False)
camui.CamApp._silent_watch_checked(fake, r_newer2)
assert fake.bg_calls == [], "con la casilla apagada no debia llamar a _bg"

fake = _FakeApp2(silent_watch=True, busy=True)
camui.CamApp._silent_watch_checked(fake, r_newer2)
assert fake.bg_calls == [], "ocupado con otra cosa (busy) no debia llamar a _bg"
print("16) _silent_watch_checked no toca _bg salvo version nueva real, casilla activada, "
      "y nada mas en marcha a la vez: OK")

# 17) con todo en orden, _silent_watch_checked SI llama a _bg -- y lo que le pasa para
#     ejecutar en el hilo de fondo (_update_run(update_apply, info)) de verdad instala la
#     version nueva y guarda una copia de seguridad, igual que los demas caminos de aplicar.
shutil.rmtree(camui.UPDATE_BACKUPS, ignore_errors=True)
local_data2 = publish(10, notes="base para la vigilancia silenciosa")
set_local(10, local_data2)
remote_data2 = publish(11, notes="novedades silenciosas")
r17 = camui.update_check()
assert r17["ok"] and r17["state"] == "newer" and r17["info"]["pack"] == 11, r17

fake = _FakeApp2(silent_watch=True)
camui.CamApp._silent_watch_checked(fake, r17)
assert len(fake.bg_calls) == 1, "debia llamar a _bg justo una vez"
fn17, done17 = fake.bg_calls[0]
backups_before = len(os.listdir(camui.UPDATE_BACKUPS)) if os.path.isdir(camui.UPDATE_BACKUPS) else 0
res17 = fn17()                      # lo que de verdad correria en el hilo de fondo
assert res17.get("ok") is True, "no se aplico de verdad la version nueva: {0}".format(res17)
assert open(camui.APP_FILE, "rb").read() == remote_data2, "no se instalo de verdad la version nueva"
backups_after = len(os.listdir(camui.UPDATE_BACKUPS))
assert backups_after == backups_before + 1, "no se guardo la copia de seguridad de antes"
print("17) _silent_watch_checked, con todo en orden, llama a _bg una vez -- y lo que le pasa "
      "de verdad instala la version nueva y guarda su copia de seguridad, como siempre: OK")

# 18) _silent_watch_applied: si la aplicacion salio bien, reinicia solo; si fallo, no reinicia
#     nada (se reintentara solo en la siguiente vuelta del vigilante, sin avisar de nada).
fake = _FakeApp2()
camui.CamApp._silent_watch_applied(fake, res17)
assert fake.restart_calls == 1, "debia reiniciar solo tras aplicar bien, sin preguntar nada"

fake = _FakeApp2()
camui.CamApp._silent_watch_applied(fake, {"ok": False, "error": "disco lleno"})
assert fake.restart_calls == 0, "no debia reiniciar nada si la aplicacion fallo"
print("18) _silent_watch_applied reinicia solo si de verdad se aplico bien, y no reinicia "
      "nada si fallo (se reintenta solo, sin avisar): OK")

# 19) _silent_watch_tick se reprograma siempre solo a si mismo (cada SILENT_WATCH_INTERVAL_MS),
#     tanto con la casilla activada como apagada -- si no, el vigilante se pararia solo tras
#     la primera vuelta.
for sw in (True, False):
    fake = _FakeApp2(silent_watch=sw)
    camui.CamApp._silent_watch_tick(fake)
    assert fake.root.after_calls == [(camui.SILENT_WATCH_INTERVAL_MS, fake._silent_watch_tick)], \
        "no se reprogramo solo (silent_watch={0}): {1}".format(sw, fake.root.after_calls)
print("19) _silent_watch_tick se reprograma siempre solo, este activada la casilla o no: OK")

# 20) _silent_watch_tick solo toca la red (llama a _bg) si la casilla esta activada, y respeta
#     las mismas protecciones que el resto del programa: nada de tocar la red mientras se esta
#     grabando, ni mientras ya hay otra cosa en marcha (busy), ni en modo sin red de las pruebas.
fake = _FakeApp2(silent_watch=True)
camui.CamApp._silent_watch_tick(fake)
assert len(fake.bg_calls) == 1, "con la casilla activada si debia llamar a _bg"
assert fake.bg_calls[0][1] == fake._silent_watch_checked

fake = _FakeApp2(silent_watch=False)
camui.CamApp._silent_watch_tick(fake)
assert fake.bg_calls == [], "con la casilla apagada no debia tocar la red para nada"

fake = _FakeApp2(silent_watch=True, recording=True)
camui.CamApp._silent_watch_tick(fake)
assert fake.bg_calls == [], "no debia tocar la red mientras se esta grabando"

fake = _FakeApp2(silent_watch=True, busy=True)
camui.CamApp._silent_watch_tick(fake)
assert fake.bg_calls == [], "no debia tocar la red si ya hay otra cosa en marcha (busy)"

os.environ["CAMUI_OFFLINE"] = "1"
try:
    fake = _FakeApp2(silent_watch=True)
    camui.CamApp._silent_watch_tick(fake)
    assert fake.bg_calls == [], "no debia tocar la red en modo sin red (CAMUI_OFFLINE)"
finally:
    del os.environ["CAMUI_OFFLINE"]
print("20) _silent_watch_tick solo toca la red con la casilla activada, y respeta grabando / "
      "busy / sin-red igual que el resto del programa: OK")

srv.shutdown()
other.shutdown()
shutil.rmtree(WORK, ignore_errors=True)
print("TODO OK unit_update")
