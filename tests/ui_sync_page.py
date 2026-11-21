"""La página de la claqueta (pack 52) en un navegador de verdad (Chromium, con Playwright de Node:
hace falta «node» con el paquete «playwright»; si no está, se dice y no se prueba nada).

El servidor de «Cámara del móvil» de verdad (PhoneCamHub) sirve /sync; el navegador la abre, se
toca «Empezar», y aquí se hace de PC: al «go» se pone a grabar, y se apuntan «started» y «done».
En la página se anota, fotograma a fotograma, cuándo está en blanco la pantalla: los destellos
tienen que salir todos, con sus duraciones y sus separaciones (las de SYNC_PATTERN, ± un
refresco de pantalla), y al acabar la página enseña lo que dice el PC."""
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
camui.SYNC_PATTERN = camui.SYNC_PATTERN[:8]          # más corta: la prueba dura menos
P = camui.SYNC_PATTERN

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
        for kind, run, t in b.take():
            marks.setdefault(kind, (run, t))
            if kind == "go":
                b.set("grabando", new_run=True)          # el PC ya graba la prueba
            elif kind == "done":
                b.set("hecho", "Prueba hecha: todo bien.")
        time.sleep(0.05)
    th.join(5)
    line = [l for l in (out.get("stdout") or "").splitlines() if l.startswith("RESULT ")]
    assert line, (out.get("stdout"), (out.get("stderr") or "")[-2000:])
    res = json.loads(line[0][len("RESULT "):])
    assert "go" in marks and "started" in marks and "done" in marks, marks
    assert marks["started"][0] == marks["done"][0] == b.run, (marks, b.run)
    assert res["title"].startswith("Claqueta de cam-ui"), res["title"]
    print("1) la página abre, «Empezar» llega al PC (go), y al grabar el PC, la claqueta empieza "
          "y termina avisando (started / done, prueba n.º {0}) OK".format(b.run))

    fl = res["flashes"]
    ons = [t for t, on in fl if on]
    offs = [t for t, on in fl if not on and t > (ons[0] if ons else 0)]
    assert len(ons) == len(P), (len(ons), len(P), fl)
    durs = [(off - on) / 1000.0 for on, off in zip(ons, offs)]
    gaps = [(b_ - a_) / 1000.0 for a_, b_ in zip(ons, ons[1:])]
    want_g = [b_[0] - a_[0] for a_, b_ in zip(P, P[1:])]
    frame = 0.034
    for d, (_t, dk) in zip(durs, P):
        assert abs(d - dk) <= frame, (durs, [q[1] for q in P])
    for g, wg in zip(gaps, want_g):
        assert abs(g - wg) <= frame, (gaps, want_g)
    print("2) en la pantalla: {0} destellos, duraciones {1} s y separaciones como las de la "
          "claqueta (± un refresco) OK".format(len(ons), "/".join("%.2f" % d for d in durs)))

    assert "Prueba hecha" in res["msg"] and res["button"] == "Otra vez", res
    print("3) al acabar, la página enseña lo que dice el PC y ofrece «Otra vez» OK")

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
