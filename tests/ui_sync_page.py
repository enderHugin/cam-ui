"""La página de la claqueta (packs 52 y 55) en un navegador de verdad (Chromium, con Playwright de
Node: hace falta «node» con el paquete «playwright»; si no está, se dice y no se prueba nada).

El servidor de «Cámara del móvil» de verdad (PhoneCamHub) sirve /sync; el navegador la abre, se
toca «Empezar», y aquí se hace de PC: al «go» se pone a grabar, se apunta «started», a los 12 s se
le pide que termine («terminando», como el botón «Terminar» del PC) y se apunta «done» con los
trozos que llegó a dar. En la página se anota, refresco a refresco, cuándo está la pantalla en
blanco: los cambios blanco/negro tienen que caer donde los del código (SYNC_CODE, a SYNC_BIT_S
cada trozo), ± un refresco de pantalla; y al acabar, la página enseña lo que dice el PC."""
import os as _os_lang
_os_lang.environ.setdefault("LANGKIT_LANG", "es")   # las pruebas comprueban los textos en espanol

import os
import sys
import json
import time
import shutil
import socket
import tempfile
import threading
import subprocess
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = tempfile.mkdtemp(prefix="camui_claqueta_")
for n_ in ("PHONECAM_CERT_FILE", "PHONECAM_KEY_FILE", "PHONECAM_CA_CERT_FILE", "PHONECAM_CA_KEY_FILE"):
    setattr(camui, n_, os.path.join(HOME, n_.lower() + ".pem"))
camui.PHONECAM_DEVICES_FILE = os.path.join(HOME, "devices.json")
CODE = camui.SYNC_CODE
BIT = camui.SYNC_BIT_S
STOP_AFTER = 12.0                                     # «Terminar» en el PC a los 12 s

NODE_JS = r"""
const { chromium } = require('playwright');
(async () => {
  const url = process.argv[1];
  const browser = await chromium.launch({args: ['--autoplay-policy=no-user-gesture-required']});
  const ctx = await browser.newContext({ignoreHTTPSErrors: true});
  const page = await ctx.newPage();
  await page.addInitScript(() => {
    window.__flash = [];
    let last = null;
    const tick = (now) => {
      const f = document.getElementById('flash');
      if (f) {
        const on = f.style.display === 'block';
        if (on !== last) { window.__flash.push([now, on]); last = on; }
      }
      const h = document.querySelector('#devs .h');
      if (h && document.getElementById('ui').className === 'quiet' && !window.__devsQuiet &&
          h.textContent.indexOf('Faltan') === 0) {
        window.__devsQuiet = [h.textContent, getComputedStyle(h).color];
      }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  await page.goto(url);
  const title = await page.textContent('#title');
  await page.click('#go');
  await page.waitForFunction(() => document.getElementById('msg').textContent.indexOf('Prueba hecha') >= 0,
                             null, {timeout: 90000});
  const out = await page.evaluate(() => ({
    flashes: window.__flash,
    msg: document.getElementById('msg').textContent,
    devsHead: (document.querySelector('#devs .h') || {}).textContent || null,
    devsRows: Array.prototype.map.call(document.querySelectorAll('#devs .r'),
                                       function (r) { return r.textContent; }),
    devsQuiet: window.__devsQuiet || null,
    button: document.getElementById('go').textContent,
    outTs: !!(window.AudioContext && AudioContext.prototype.getOutputTimestamp)}));
  out.title = title;
  console.log('RESULT ' + JSON.stringify(out));
  await browser.close();
})().catch(e => { console.log('ERROR ' + e); process.exit(1); });
"""


def node_ok():
    if not shutil.which("node"):
        return False
    r = subprocess.run(["node", "-e", "require('playwright')"], capture_output=True, cwd=HERE)
    return r.returncode == 0


if not node_ok():
    print("(sin node + playwright: no se prueba la página en un navegador)")
    print("TODO OK ui_sync_page")
    sys.exit(0)

s_ = socket.socket()
s_.bind(("127.0.0.1", 0))
PORT = s_.getsockname()[1]
s_.close()
hub = camui.PhoneCamHub(deque(), deque())
hub.start(port=PORT)
b = hub.sync
try:
    token = b.open()
    url = "https://127.0.0.1:{0}/sync?t={1}".format(PORT, token)
    out = {}

    def run_node():
        r = subprocess.run(["node", "-e", NODE_JS, url], capture_output=True, text=True,
                           timeout=150, cwd=HERE)
        out["stdout"], out["stderr"] = r.stdout, r.stderr
    th = threading.Thread(target=run_node, daemon=True)
    th.start()
    marks = {}
    t_end = time.time() + 140
    while th.is_alive() and time.time() < t_end:
        for kind, run, t, extra in b.take():
            marks.setdefault(kind, (run, t, extra))
            if kind == "go":
                b.set("grabando", new_run=True)          # el PC ya graba la prueba
            elif kind == "started":                      # en directo: una de tres lista
                b.set_devs({"phase": "directo", "sound": None, "rows": [
                    {"n": "USB2.0 PC CAMERA #3", "st": "ok", "ms": 0},
                    {"n": "Cam <B>", "st": "falta", "ms": None},
                    {"n": "Otra", "st": "falta", "ms": None}]})
            elif kind == "done":
                b.set_devs({"phase": "medido", "sound": 0, "sound_now": 489, "rows": [
                    {"n": "USB2.0 PC CAMERA #3", "st": "ok", "ms": -408, "now": 489},
                    {"n": "Cam <B>", "st": "ok", "ms": 489, "now": 489},
                    {"n": "Otra", "st": "no", "ms": None}]})
                b.set("hecho", "Prueba hecha: todo bien.")
        if "started" in marks and "done" not in marks and b.state == "grabando" and \
                time.time() > marks["started"][1] + STOP_AFTER:
            b.set("terminando")                          # «Terminar» en el PC
        time.sleep(0.05)
    th.join(5)
    line = [l for l in (out.get("stdout") or "").splitlines() if l.startswith("RESULT ")]
    assert line, (out.get("stdout"), (out.get("stderr") or "")[-2000:])
    res = json.loads(line[0][len("RESULT "):])
    assert "go" in marks and "started" in marks and "done" in marks, marks
    assert marks["started"][0] == marks["done"][0] == b.run, (marks, b.run)
    bits = marks["done"][2]
    assert bits is not None and abs(bits * BIT - (STOP_AFTER + 0.4)) < 1.5, (bits, marks)
    assert res["title"].startswith("Claqueta de cam-ui"), res["title"]
    print("1) la página abre, «Empezar» llega al PC (go), al grabar el PC empieza (started), y al "
          "pedirle el PC que termine, para y dice hasta dónde llegó (done: {0} trozos, {1:.1f} s) "
          "OK".format(bits, bits * BIT))

    fl = res["flashes"]
    ch = [(t / 1000.0, on) for t, on in fl]
    ch = ch[next(i for i, (_t, on) in enumerate(ch) if on):]            # desde el primer blanco
    t0 = ch[0][0]
    # los cambios del código (en s desde su principio), hasta los trozos que dio
    want = [(k * BIT, CODE[k] == 1) for k in range(1, bits) if CODE[k] != CODE[k - 1]]
    got = [(t - t0, on) for t, on in ch[1:] if t - t0 < (bits - 0.5) * BIT]
    assert len(got) == len(want), (len(got), len(want))
    err = [g - w for (g, _o1), (w, _o2) in zip(got, want)]
    assert all(o1 == o2 for (_g, o1), (_w, o2) in zip(got, want))
    assert max(abs(e) for e in err) <= 0.034, max(abs(e) for e in err)
    print("2) en la pantalla: {0} cambios blanco/negro, todos donde los del código (el peor, a "
          "{1:.0f} ms: un refresco de pantalla) OK".format(len(got), 1000 * max(abs(e) for e in err)))

    assert "Prueba hecha" in res["msg"] and res["button"] == "Otra vez", res
    print("3) al acabar, la página enseña lo que dice el PC y ofrece «Otra vez» OK")
    q = res["devsQuiet"]
    assert q and q[0] == "Faltan 2 de 3", q
    rgb = [int(v) for v in q[1][q[1].index("(") + 1:q[1].index(")")].split(",")[:3]]
    assert max(rgb) < 120, q                      # mientras parpadea, apagado: no estorba
    assert res["devsHead"] == "Medidas 2 de 3 \u00b7 sin medir: 1", res["devsHead"]
    rows = res["devsRows"]
    assert len(rows) == 4 and "Cam <B>" in rows[1] and "+489 ms" in rows[1], rows
    assert "no se vio" in rows[2] and rows[3].startswith("Sonido") and "a la vez" in rows[3], rows
    print("3b) el mapa en el móvil: parpadeando, «Faltan 2 de 3» en apagado; al acabar, «{0}», una "
          "línea por cámara (✓ / no se vio, lo que tarda de por sí) y el sonido «a la vez» OK".format(
              res["devsHead"]))

    b.close()
    import urllib.request
    import urllib.error
    import ssl
    try:
        urllib.request.urlopen("https://127.0.0.1:{0}/sync/state?t={1}".format(PORT, token),
                               timeout=5, context=ssl._create_unverified_context())
        raise SystemExit("debería dar 403 tras cerrar")
    except urllib.error.HTTPError as e:
        assert e.code == 403
    print("4) cerrada la sincronización en el PC, la página ya no vale (403) OK")
finally:
    hub.stop()
    shutil.rmtree(HOME, ignore_errors=True)
print("TODO OK ui_sync_page")
