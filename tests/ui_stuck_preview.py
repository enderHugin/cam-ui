"""Vista previa colgada (ver CamApp._on_stuck en camui.py): caso real reportado por el usuario
("no se ve la vista previa", pack 22) -- FFmpegEngine.start() solo comprueba que ffmpeg no se
cayera en 1.6s para darlo por arrancado ("Vista previa en marcha"), no que de verdad llegara a
entregar ni un fotograma. Si una cámara (p.ej. una réflex lenta en iniciar) deja a ffmpeg
colgado sin más -- vivo, pero sin producir nada -- is_running() sigue dando True para siempre y
el antiguo vigilante de _on_died() nunca se dispara: el usuario se queda con la vista previa en
negro, sin ningún error, viendo "Vista previa en marcha" en la barra de estado como si todo
fuera bien.

Esta prueba usa una CamApp real (bajo Xvfb) y un FFmpegEngine real (el que ya crea CamApp al
construirse), pero en vez de lanzar ffmpeg de verdad contra una cámara (no hay ninguna física en
este entorno), se le da como "proceso" un subprocess real y vivo (sleep), y se manipula
_spawn_t/frame_count a mano para simular "lleva más de STUCK_PREVIEW_SECONDS sin entregar nada"
sin tener que esperar ese tiempo de verdad en cada caso. is_running()/stop() siguen siendo los
métodos reales de FFmpegEngine, actuando sobre un proceso de verdad: lo que se simula es solo lo
que ffmpeg le reportaría (cuántos fotogramas, desde cuándo), no is_running() ni stop() en sí.

Hace falta un display (Xvfb en Linux: xvfb-run -a python3 tests/ui_stuck_preview.py), porque lo
que se comprueba incluye _set_idle()/_refresh_buttons() tocando widgets reales de la ventana.
"""
import os
import subprocess
import sys
import time

import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.join(HERE, "_ui_stuck_preview_home")
os.makedirs(HOME, exist_ok=True)
camui.UPDATE_STATE = os.path.join(HOME, "actualizacion.json")
camui._UPDATE_ST = {}
camui.HERE = HOME
camui.CONFIG = os.path.join(HOME, "config.json")
if os.path.isfile(camui.CONFIG):            # arranque limpio: sin restos de una ejecución anterior
    os.remove(camui.CONFIG)
camui.LOGFILE = os.path.join(HOME, "camui.log")

root = tk.Tk()
root.withdraw()
app = camui.CamApp(root)
app._tray_asked = True                      # evita el askyesno real de _tray_boot bajo Xvfb
# sin cámaras físicas en este entorno (ver el mismo comentario en ui_phonecam.py); esta prueba
# no usa _start_preview en absoluto, pero se deja estéril por si algún otro camino lo llamara
app.previewing = False
app._start_preview = lambda *a, **kw: None
root.update()


def pump(cond, timeout=6):
    t0 = time.time()
    while not cond() and time.time() - t0 < timeout:
        root.update()
        time.sleep(0.05)
    return cond()


class _FakeMsgBox:
    def __init__(self):
        self.errors = []

    def showerror(self, title, msg):
        self.errors.append((title, msg))

    def showinfo(self, title, msg):
        pass

    def showwarning(self, title, msg):
        pass

    def askyesno(self, title, msg):
        return False


orig_mb = camui.messagebox
fake_mb = _FakeMsgBox()
camui.messagebox = fake_mb

try:
    # ------------------------------------------------------------------------------------------
    # 1) Con fotogramas llegando de verdad (frame_count > 0), por mucho que _spawn_t sea viejo,
    #    no debe avisar de "colgado": está vivo y produciendo, así que va bien.
    # ------------------------------------------------------------------------------------------
    p1 = subprocess.Popen(["sleep", "2"])
    app.eng.proc = p1
    app.eng.frame_count = 3
    app.eng._spawn_t = time.time() - camui.STUCK_PREVIEW_SECONDS - 5
    app.previewing, app.busy, app._stuck_warned, app.paused = True, False, False, False
    time.sleep(0.5)
    pump(lambda: False, timeout=0.8)        # varias vueltas de _tick sin que nada lo interrumpa
    assert app.previewing is True and not app._stuck_warned and not fake_mb.errors, \
        "con fotogramas llegando no debería avisar de vista previa colgada"
    p1.kill()
    p1.wait()
    print("1) con fotogramas llegando de verdad, aunque _spawn_t sea viejo, no avisa de "
          "«colgada» OK")

    # ------------------------------------------------------------------------------------------
    # 2) Sin fotogramas pero todavía dentro del plazo (_spawn_t reciente): tampoco debe avisar
    #    -- toda cámara tarda un poco en arrancar, STUCK_PREVIEW_SECONDS es a propósito generoso.
    # ------------------------------------------------------------------------------------------
    p2 = subprocess.Popen(["sleep", "2"])
    app.eng.proc = p2
    app.eng.frame_count = 0
    app.eng._spawn_t = time.time()
    app.previewing, app.busy, app._stuck_warned, app.paused = True, False, False, False
    pump(lambda: False, timeout=0.8)
    assert app.previewing is True and not app._stuck_warned and not fake_mb.errors, \
        "sin fotogramas pero todavía dentro del plazo no debería avisar aún"
    p2.kill()
    p2.wait()
    print("2) sin fotogramas pero todavía dentro del plazo ({0:g}s), no avisa todavía "
          "OK".format(camui.STUCK_PREVIEW_SECONDS))

    # ------------------------------------------------------------------------------------------
    # 3) Sin fotogramas, plazo superado, pero self.busy=True (hay otra operación en marcha):
    #    tampoco debe avisar -- evita falsos avisos a mitad de un cambio en curso.
    #    En cuanto busy vuelve a False, con las mismas condiciones, SÍ debe avisar: aquí es
    #    donde se simula de verdad "colgado" (ver _on_stuck en camui.py).
    # ------------------------------------------------------------------------------------------
    p3 = subprocess.Popen(["sleep", "2"])
    app.eng.proc = p3
    app.eng.frame_count = 0
    app.eng._spawn_t = time.time() - camui.STUCK_PREVIEW_SECONDS - 5
    app.previewing, app.busy, app._stuck_warned, app.paused = True, True, False, False
    pump(lambda: False, timeout=0.8)
    assert app.previewing is True and not app._stuck_warned and not fake_mb.errors, \
        "con busy=True no debería avisar aunque el plazo esté superado"
    print("3a) con otra operación en marcha (busy=True), no avisa aunque el plazo esté "
          "superado OK")

    app.busy = False
    assert pump(lambda: app._stuck_warned, timeout=3), \
        "en cuanto busy vuelve a False debería detectar el cuelgue y avisar"
    assert app.previewing is False, "_on_stuck debería dejar previewing=False"
    assert app.paused is True, "_on_stuck debería dejar paused=True (evita reintentar en bucle)"
    assert app.eng.proc is None, "_on_stuck debería haber detenido el motor de verdad (eng.stop())"
    assert len(fake_mb.errors) == 1, fake_mb.errors
    title, msg = fake_mb.errors[0]
    assert title == "Vista previa colgada", title
    assert "{0:g}".format(camui.STUCK_PREVIEW_SECONDS) in msg and "F5" in msg
    print("3b) al quedar libre (busy=False) con el mismo cuelgue, detiene el motor, deja "
          "paused=True y avisa una vez con «Vista previa colgada», mencionando el plazo y F5 OK")

    # ------------------------------------------------------------------------------------------
    # 4) _stuck_warned evita avisar dos veces por el mismo cuelgue -- sin este guardián, cada
    #    vuelta de _tick (cada 33ms) abriría un messagebox nuevo sin parar.
    # ------------------------------------------------------------------------------------------
    p4 = subprocess.Popen(["sleep", "2"])
    app.eng.proc = p4
    app.eng.frame_count = 0
    app.eng._spawn_t = time.time() - camui.STUCK_PREVIEW_SECONDS - 5
    app.previewing, app.busy, app.paused = True, False, False
    app._stuck_warned = True                # ya se avisó antes de este mismo cuelgue
    pump(lambda: False, timeout=0.8)
    assert len(fake_mb.errors) == 1, "no debería haber avisado una segunda vez (_stuck_warned)"
    assert app.previewing is True, "sin volver a llamar a _on_stuck, previewing no cambia solo"
    p4.kill()
    p4.wait()
    print("4) _stuck_warned evita un segundo aviso por el mismo cuelgue (no se abre un "
          "messagebox nuevo en cada vuelta de _tick) OK")

    # ------------------------------------------------------------------------------------------
    # 5) Si ffmpeg de verdad ha muerto (is_running() == False), manda _on_died -- NO _on_stuck,
    #    aunque también lleve 0 fotogramas y el plazo esté superado: un proceso muerto merece el
    #    aviso de siempre ("ffmpeg se detuvo"), no el de "se quedó colgado" (son cosas distintas
    #    y el usuario necesita saber cuál de las dos pasó). Comprueba que el orden del elif en
    #    _tick() no deja que _on_stuck tape o se adelante a _on_died.
    # ------------------------------------------------------------------------------------------
    p5 = subprocess.Popen(["true"])
    p5.wait()                               # ya está muerto de verdad antes de que _tick lo vea
    app.eng.proc = p5
    app.eng.frame_count = 0
    app.eng._spawn_t = time.time() - camui.STUCK_PREVIEW_SECONDS - 5
    app.previewing, app.busy, app.paused, app.recording = True, False, False, False
    app._stuck_warned = False
    n_before = len(fake_mb.errors)
    assert pump(lambda: len(fake_mb.errors) > n_before, timeout=3), \
        "un ffmpeg ya muerto debería avisar (por _on_died), no quedarse callado"
    assert not app._stuck_warned, \
        "con el proceso muerto, _on_stuck no debería haberse disparado en absoluto"
    assert app.previewing is False
    title5, msg5 = fake_mb.errors[-1]
    assert title5 == "Error", title5           # el título de _on_died, no "Vista previa colgada"
    assert "ffmpeg se detuvo" in msg5
    print("5) con el proceso ya muerto, avisa por el camino de siempre (_on_died, «Error»/"
          "«ffmpeg se detuvo») y no por el nuevo (_on_stuck) -- el elif no se adelanta ni se "
          "confunde OK")
finally:
    camui.messagebox = orig_mb
    app.eng.proc = None
    app.previewing = False

root.destroy()
print("TODO OK ui_stuck_preview")
