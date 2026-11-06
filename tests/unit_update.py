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

srv.shutdown()
other.shutdown()
shutil.rmtree(WORK, ignore_errors=True)
print("TODO OK unit_update")
