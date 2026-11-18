"""Memoria de colocación (pack 51), en la aplicación de verdad (CamApp con su ventana Tk; hace falta
un display: xvfb-run -a python3 tests/ui_layout_memory.py). Cada «sesión» es un cam-ui que se abre
y se cierra de verdad (otro proceso, el mismo config.json), con cámaras USB de mentira (la lista
de dispositivos que daría Windows) y móviles de verdad (PhoneCamHub, fotogramas JPEG):

1) cámaras que se desenchufan y se vuelven a enchufar: cada combinación de cámaras vuelve a
   quedar exactamente como estaba (orden, disposición «2 + 2» / «1 + 2»);
2) cerrar cam-ui con solo dos de las cuatro cámaras y volver a abrirlo «un año después» con las
   cuatro: mismo orden, misma disposición, y cada una con sus ajustes (resolución, zoom y
   encuadre, giro, color, ★) -- antes, al guardar sin ellas, se perdían;
3) una combinación que no se ha visto nunca: las demás no se mueven y la que llega se pone junto
   a la que tenía al lado;
4) dos cámaras iguales (mismo nombre): cada una conserva su número y sus ajustes aunque Windows
   las liste en otro orden;
5) un móvil que se vuelve a conectar (recarga la página) ocupa su casilla de antes, con sus
   ajustes, en vez de otra al final; y al abrir cam-ui otro día, vuelve a su sitio con sus ajustes."""
import io
import os
import sys
import json
import time
import shutil
import socket
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import camui  # noqa: E402

HOME = os.path.join(HERE, "_ui_layout_memory_home")


def cam(n):
    return {"name": "Cam " + n, "alt": "@device_pnp_\\\\?\\usb#vid_0001&pid_000{0}#{0}".format(n)}


# ---------------------------------------------------------------- sesión (proceso hijo)
def session(step):
    import numpy as np
    import tkinter as tk
    from PIL import Image

    for n_ in ("PHONECAM_CERT_FILE", "PHONECAM_KEY_FILE", "PHONECAM_CA_CERT_FILE",
               "PHONECAM_CA_KEY_FILE"):
        setattr(camui, n_, os.path.join(HOME, n_.lower() + ".pem"))
    camui.PHONECAM_DEVICES_FILE = os.path.join(HOME, "devices.json")
    camui.UPDATE_STATE = os.path.join(HOME, "actualizacion.json")
    camui._UPDATE_ST = {}
    camui.HERE = HOME
    camui.CONFIG = os.path.join(HOME, "config.json")
    camui.LOGFILE = os.path.join(HOME, "camui.log")
    camui.PHONECAM_REUSE_STALE = 0.6
    DEVS = json.loads(os.environ["CAMUI_TEST_DEVS"])
    camui.list_devices = lambda: (camui.label_devices([dict(d) for d in DEVS]), [])

    root = tk.Tk()
    root.withdraw()
    app = camui.CamApp(root)
    app._tray_asked = True
    app.paused = True                      # sin vista previa: aquí solo cuenta dónde va cada una

    def pump(cond, secs, step_=0.03):
        t_end = time.time() + secs
        while time.time() < t_end:
            root.update()
            if cond():
                return True
            time.sleep(step_)
        return False

    def plug(*names):
        DEVS[:] = [cam(n) for n in names]
        app._refresh_devices()
        assert pump(lambda: not app.busy and
                    sorted(d["label"] for d in app.vdevs if not d.get("net")) ==
                    sorted(d["name"] for d in camui.label_devices([dict(d) for d in DEVS])), 10)
        root.update()

    def L(*names):
        return ["Cam " + n for n in names]

    def tick(lab):                         # marcar una cámara en la lista (como _tree_click)
        app.cams[lab].use = True
        if lab not in app.order:
            app.order.append(lab)
        app._on_change()

    def arrange(order, struct):
        app.order[:] = order
        app.v_struct.set(struct)
        app._on_change()

    def jpeg(w, h, k):
        a = np.full((h, w, 3), 80, np.uint8)
        a[:, :, 1] = (k * 7) % 255
        b = io.BytesIO()
        Image.fromarray(a).save(b, format="JPEG", quality=70)
        return b.getvalue()

    def phone(name, ip, frames=3):
        sid = app.phonecam.new_session(name)
        for k in range(frames):
            app.phonecam.feed(sid, jpeg(320, 240, k + 1), seq=k + 1, ip=ip)
        assert pump(lambda: not app._phonecam_q and not app.busy, 5)
        root.update()
        return sid

    assert pump(lambda: not app.busy and app.vdevs is not None and app.cams, 10)
    root.update()
    out = []
    if step == "s1":
        for n in "ABCD":
            tick("Cam " + n)
        arrange(L("D", "A", "C", "B"), "2 + 2")
        b = app.cams["Cam B"]
        b.zoom.set(250)
        b.cx.set(30)
        b.cy.set(70)
        b.rot.set("90°")
        b.mode.set("1280x720 @ 30 fps")
        b.color["bri"].set(15)
        b.feat.set(True)
        # se desenchufa A (la del medio) y se vuelve a enchufar
        plug("A", "B", "C", "D")
        plug("B", "C", "D")
        assert app.order == L("D", "C", "B"), app.order
        plug("A", "B", "C", "D")
        assert app.order == L("D", "A", "C", "B") and app.v_struct.get() == "2 + 2", \
            (app.order, app.v_struct.get())
        out.append("1a) se desenchufa «Cam A» (la segunda) y se vuelve a enchufar: vuelve a su "
                   "sitio, D·A·C·B en «2 + 2» (antes: al final y en «Auto») OK")
        # sin A, se colocan las otras tres de otra manera; cada combinación recuerda la suya
        plug("B", "C", "D")
        arrange(L("B", "C", "D"), "1 + 2")
        plug("A", "B", "C", "D")
        assert app.order == L("D", "A", "C", "B") and app.v_struct.get() == "2 + 2", \
            (app.order, app.v_struct.get())
        plug("B", "C", "D")
        assert app.order == L("B", "C", "D") and app.v_struct.get() == "1 + 2", \
            (app.order, app.v_struct.get())
        plug("A", "B", "C", "D")
        assert app.order == L("D", "A", "C", "B") and app.v_struct.get() == "2 + 2"
        out.append("1b) sin «Cam A» las otras tres se dejan en B·C·D «1 + 2»; con A, D·A·C·B "
                   "«2 + 2»; y otra vez sin A, B·C·D «1 + 2»: cada combinación, como se dejó OK")
    elif step == "s2":                     # otro día, solo están A y C
        assert app.order == L("A", "C"), app.order
        arrange(L("C", "A"), camui.AUTO)
    elif step == "s3":                     # «un año después», las cuatro (Windows las lista al revés)
        assert app.order == L("D", "A", "C", "B") and app.v_struct.get() == "2 + 2", \
            (app.order, app.v_struct.get())
        b = app.cams["Cam B"]
        got = (b.use, b.zoom.get(), b.cx.get(), b.cy.get(), b.rot_deg(), b.mode.get(),
               b.color["bri"].get(), b.feat.get())
        assert got == (True, 250, 30, 70, 90, "1280x720 @ 30 fps", 15, True), got
        with open(camui.CONFIG, encoding="utf-8") as f:
            cc = json.load(f)["cams"]
        out.append("2) cam-ui cerrado con solo A y C y abierto «un año después» con las cuatro: "
                   "D·A·C·B en «2 + 2», y «Cam B» con su resolución, zoom 250 % centrado en "
                   "30/70, girada 90°, su brillo y su ★ OK")
        assert cc["Cam B"].get("seen") and cc["Cam B"].get("alt"), cc["Cam B"]
        # una cámara nueva (E) y luego vuelve una conocida en una combinación nunca vista
        plug("B", "C", "D")
        assert app.order == L("B", "C", "D") and app.v_struct.get() == "1 + 2", app.order
        plug("B", "C", "D", "E")
        tick("Cam E")
        assert app.order == L("B", "C", "D", "E"), app.order
        plug("A", "B", "C", "D", "E")
        assert app.order == L("B", "C", "D", "A", "E"), app.order
        out.append("3) combinación nunca vista (A vuelve con una cámara nueva, E): las demás no se "
                   "mueven y A se pone detrás de D, como estaba: B·C·D·A·E OK")
    elif step == "s4":                     # dos cámaras iguales
        lab = {d["alt"]: d["label"] for d in app.vdevs if d.get("alt")}
        x, y = lab["@pnp_x"], lab["@pnp_y"]
        assert (x, y) == ("USB Camera #1", "USB Camera #2"), (x, y)
        tick(x)
        tick(y)
        app.cams[y].zoom.set(300)
        arrange([y, x], camui.AUTO)
    elif step == "s5":                     # Windows las lista al revés
        lab = {d["alt"]: d["label"] for d in app.vdevs if d.get("alt")}
        x, y = lab["@pnp_x"], lab["@pnp_y"]
        assert (x, y) == ("USB Camera #1", "USB Camera #2"), (x, y)
        assert app.vdev[y]["spec"] == "@pnp_y" and app.cams[y].zoom.get() == 300, \
            (app.vdev[y]["spec"], app.cams[y].zoom.get())
        assert app.order == [y, x], app.order
        out.append("4) dos «USB Camera» iguales, listadas al revés: cada una sigue con su número, "
                   "su sitio y su zoom (va con su puerto USB) OK")
    elif step == "s6":                     # un móvil que se vuelve a conectar
        assert pump(lambda: app.phonecam.active, 10)
        tick("Cam A")
        phone("Pixel", "10.0.0.7")
        P = "📱 Pixel"
        assert P in app.order, app.order
        arrange([P, "Cam A"], camui.AUTO)
        app.cams[P].zoom.set(180)
        # recarga la página: sesión nueva («Pixel #2», la vieja aún no ha caducado), misma IP
        phone("Pixel", "10.0.0.7")
        assert app.order == [P, "Cam A"], app.order
        assert not any("#2" in l for l in app.vdev), list(app.vdev)
        assert app.cams[P].zoom.get() == 180
        assert app.status.cget("text").strip() == "Móvil «Pixel» conectado de nuevo: en su sitio " \
            "del mosaico.", app.status.cget("text")
        out.append("5a) el móvil recarga la página: vuelve a su casilla (la primera) con su zoom, "
                   "sin otra «📱 Pixel #2» al final: «{0}» OK".format(app.status.cget("text").strip()))
        # se cambia de wifi (otra IP): en cuanto la conexión vieja deja de mandar, igual
        time.sleep(1.0)
        phone("Pixel", "10.0.1.20")
        assert app.order == [P, "Cam A"] and len(app.vdev) == 2, (app.order, list(app.vdev))
        out.append("5b) con otra IP (otra wifi), si la conexión vieja ya no manda: a su casilla OK")
        # y otro que se llama igual, conectado a la vez, no le quita la casilla: va aparte
        phone("Pixel", "10.0.0.8")
        assert app.order[:2] == [P, "Cam A"] and len(app.order) == 3, app.order
        assert app.vdev[P]["spec"] != app.vdev[app.order[2]]["spec"]
        out.append("5c) otro móvil que se llama igual, conectado a la vez, no le quita la casilla "
                   "(va aparte: «{0}») OK".format(app.order[2]))
    elif step == "s7":                     # otro día: el móvil vuelve a su sitio con sus ajustes
        assert pump(lambda: app.phonecam.active, 10)
        assert app.order == ["Cam A"], app.order
        phone("Pixel", "10.0.0.7")
        P = "📱 Pixel"
        assert app.order == [P, "Cam A"], app.order
        assert app.cams[P].zoom.get() == 180, app.cams[P].zoom.get()
        out.append("5d) otro día, el mismo móvil: a su sitio (el primero) y con su zoom OK")
    app.on_close()
    for o in out:
        print(o)
    print("SESION OK " + step)


# ---------------------------------------------------------------- funciones sueltas
def units():
    lay = camui.layout_remember([], ["A", "B", "C", "D"], "2 + 2", False, 1)
    lay = camui.layout_remember(lay, ["C", "A"], "Auto", False, 2)
    assert camui.layout_find(lay, {"D", "C", "B", "A"})["order"] == ["A", "B", "C", "D"]
    assert camui.layout_find(lay, {"A", "B"}) is None
    lay = camui.layout_remember(lay, ["B", "A", "C", "D"], "1 + 3", True, 3)
    assert len(lay) == 2 and lay[0]["order"] == ["B", "A", "C", "D"]
    assert camui.read_layouts(json.loads(json.dumps(lay))) == lay
    assert camui.read_layouts([{"order": ["A", "A"]}, {"order": []}, 3, {"order": "AB"}]) == []
    # la que llega, junto a la que tenía al lado
    lay = camui.layout_remember([], ["X", "B", "C"], "Auto", False, 5)
    assert camui.layout_place(["C", "B", "X"], {"X"}, lay) == ["C", "X", "B"]   # delante de su siguiente
    lay = camui.layout_remember(lay, ["B", "X", "C"], "Auto", False, 6)
    assert camui.layout_place(["C", "B", "X"], {"X"}, lay) == ["C", "B", "X"]   # detrás de B
    assert camui.layout_place(["C", "B", "N"], {"N"}, lay) == ["C", "B", "N"]   # no se conoce
    lay = camui.layout_remember(lay, ["C", "X"], "Auto", False, 7)             # más reciente, pero
    assert camui.layout_place(["C", "B", "X"], {"X"}, lay) == ["C", "B", "X"]   # se parece menos
    assert camui.layout_place(["C", "X"], {"X"}, lay) == ["C", "X"]
    many = []
    for k in range(camui.LAYOUT_MEMORY_MAX + 10):
        many = camui.layout_remember(many, ["c%d" % k], "Auto", False, k)
    assert len(many) == camui.LAYOUT_MEMORY_MAX and many[0]["order"] == ["c%d" % (k,)]
    # dos cámaras iguales: con lo que se sabe de antes, cada una conserva su número
    devs = [{"name": "Cam", "alt": "@y"}, {"name": "Cam", "alt": "@x"}, {"name": "Otra", "alt": "@o"}]
    assert [d["label"] for d in camui.label_devices([dict(d) for d in devs])] == \
        ["Cam #1", "Cam #2", "Otra"]
    got = camui.label_devices([dict(d) for d in devs], {"@x": "Cam #1", "@y": "Cam #2"})
    assert [(d["label"], d["spec"]) for d in got] == [("Cam #2", "@y"), ("Cam #1", "@x"), ("Otra", "Otra")]
    got = camui.label_devices([dict(d) for d in devs] + [{"name": "Cam", "alt": "@z"}], {"@x": "Cam #2"})
    assert [d["label"] for d in got] == ["Cam #1", "Cam #2", "Otra", "Cam #3"], got
    print("0) funciones: colocación por combinación, la que llega junto a su vecina, máximo "
          "{0} recordadas, cámaras iguales por su nombre alternativo OK".format(camui.LAYOUT_MEMORY_MAX))


def run(step, devs):
    env = dict(os.environ, CAMUI_TEST_DEVS=json.dumps(devs))
    r = subprocess.run([sys.executable, os.path.abspath(__file__), "--sesion", step], env=env,
                       capture_output=True, text=True, timeout=120)
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    if r.returncode != 0 or not lines or lines[-1] != "SESION OK " + step:
        print(r.stdout)
        print(r.stderr[-4000:])
        raise SystemExit("falló la sesión " + step)
    for l in lines[:-1]:
        print(l)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--sesion":
        session(sys.argv[2])
        sys.exit(0)
    units()
    shutil.rmtree(HOME, ignore_errors=True)
    os.makedirs(HOME)
    s_ = socket.socket()
    s_.bind(("127.0.0.1", 0))
    port = s_.getsockname()[1]
    s_.close()
    with open(os.path.join(HOME, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"version": 3, "fps": 30, "phonecam": {"port": port}}, f)
    run("s1", [cam(n) for n in "ABCD"])
    run("s2", [cam(n) for n in "AC"])
    run("s3", [cam(n) for n in "DCBA"])
    run("s4", [{"name": "USB Camera", "alt": "@pnp_x"}, {"name": "USB Camera", "alt": "@pnp_y"}])
    run("s5", [{"name": "USB Camera", "alt": "@pnp_y"}, {"name": "USB Camera", "alt": "@pnp_x"}])
    run("s6", [cam("A")])
    run("s7", [cam("A")])
    shutil.rmtree(HOME, ignore_errors=True)
    print("TODO OK ui_layout_memory")
