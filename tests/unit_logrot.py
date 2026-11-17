"""Registro por tramos (pack 44): al arrancar (y por tanto tras cada actualización) y al cambiar
de día, camui.log se guarda en «registros» con el nombre de su rango de horas y su pack, y se
empieza otro. Y los avisos de DirectShow «real-time buffer … too full» (uno por fotograma
tirado) se resumen, para que no se coman las líneas de ffmpeg que se apuntan."""
import io
import os
import sys
import time
import shutil
import logging
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui  # noqa: E402

TMP = tempfile.mkdtemp(prefix="camui_logrot_")
camui.LOGFILE = os.path.join(TMP, "camui.log")
ARCH = os.path.join(TMP, camui.LOG_ARCHIVE_DIR)
PACK = camui.APP_PACK


def reset_logging():
    for h in list(camui.log.handlers):
        if isinstance(h, logging.FileHandler):
            camui.log.removeHandler(h)
            h.close()
    if camui._hang_fh is not None:
        import faulthandler
        faulthandler.disable()
        camui._hang_fh.close()
        camui._hang_fh = None
    camui._log_ready = False
    camui._log_handler = None


def setup():
    camui.setup_logging()
    sys.excepthook = sys.__excepthook__       # que un fallo de la prueba se vea aquí, no en el registro


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


try:
    # 1) al arrancar: el registro anterior se guarda con su rango y su pack
    old = ("2026-11-16 10:00:05.001 [MainThread] INFO === CamWin32 v2 · pack 41 | Python 3.12\n"
           "2026-11-16 11:12:00.000 [MainThread] INFO algo\n"
           "Traceback sin hora (volcado de faulthandler)\n"
           "2026-11-16 12:30:59.999 [MainThread] INFO cerrando\n"
           "  línea suelta sin hora al final\n")
    with open(camui.LOGFILE, "w", encoding="utf-8") as f:
        f.write(old)
    reset_logging()
    setup()
    a1 = os.path.join(ARCH, "camui 2026-11-16 10.00 a 12.30 (pack 41).log")
    assert os.path.isfile(a1), os.listdir(ARCH)
    assert read(a1) == old
    cur = read(camui.LOGFILE)
    assert "=== CamWin32 v2 · pack {0}".format(PACK) in cur and "registro anterior guardado en" in cur, cur
    assert "cerrando" not in cur
    print("1) al arrancar: «{0}» guardado, y camui.log nuevo con su pack OK".format(os.path.basename(a1)))

    # 2) día nuevo, sin cerrar el programa: otro registro, y lo de después va al nuevo
    camui.log.info("antes de medianoche")
    assert camui.log_rotate_if_needed() is False            # mismo día: nada
    tomorrow = time.time() + 86400
    assert camui.log_rotate_if_needed(tomorrow) is True
    today = time.strftime("%Y-%m-%d")
    arch2 = [n for n in os.listdir(ARCH) if n.startswith("camui {0} ".format(today))]
    assert len(arch2) == 1 and arch2[0].endswith("(pack {0}).log".format(PACK)), os.listdir(ARCH)
    assert "antes de medianoche" in read(os.path.join(ARCH, arch2[0]))
    camui.log.info("después de medianoche")
    cur = read(camui.LOGFILE)
    assert "registro nuevo (día nuevo)" in cur and "después de medianoche" in cur, cur
    assert "antes de medianoche" not in cur
    assert camui._hang_fh is not None and not camui._hang_fh.closed and \
        os.path.samefile(camui._hang_fh.name, camui.LOGFILE)
    assert camui.log_rotate_if_needed(tomorrow) is False     # ya es «ese» día: no repite
    print("2) día nuevo: «{0}» guardado; lo de después, en el nuevo (y el volcado de pilas "
          "apunta al nuevo) OK".format(arch2[0]))

    # 3) mismo rango y nombre: no se pisa, «(2)»
    reset_logging()
    shutil.copyfile(a1, camui.LOGFILE)
    setup()
    assert os.path.isfile(a1[:-4] + " (2).log"), os.listdir(ARCH)
    print("3) dos registros con el mismo rango: el segundo, «… (2).log» OK")

    # 4) si otro proceso aún lo tiene abierto y no se puede mover (Windows): se copia y se vacía
    reset_logging()
    with open(camui.LOGFILE, "w", encoding="utf-8") as f:
        f.write("2026-11-15 08:00:00.000 [MainThread] INFO === CamWin32 v2 · pack 40 | x\n")
    real_replace = os.replace

    def busy(*a, **k):
        raise PermissionError(32, "El proceso no tiene acceso al archivo porque está siendo utilizado")
    camui.os.replace = busy
    try:
        setup()
    finally:
        camui.os.replace = real_replace
    a4 = os.path.join(ARCH, "camui 2026-11-15 08.00 a 08.00 (pack 40).log")
    assert os.path.isfile(a4) and "pack 40" in read(a4), os.listdir(ARCH)
    assert "2026-11-15 08:00:00" not in read(camui.LOGFILE)
    print("4) en uso por otro proceso: se copia a «{0}» y se vacía OK".format(os.path.basename(a4)))

    # 5) un registro vacío no deja un guardado vacío
    reset_logging()
    n_before = len(os.listdir(ARCH))
    open(camui.LOGFILE, "w").close()
    setup()
    assert len(os.listdir(ARCH)) == n_before
    print("5) registro vacío: no se guarda nada OK")

    # 6) avisos de búfer lleno de DirectShow: resumidos, sin gastar las 400 líneas de ffmpeg
    class P:
        pass
    lines = []
    for k in range(300):
        lines.append("[in#3/dshow @ 0000027f5c130440] real-time buffer [EOS Webcam Utility] [video "
                     "input] too full or near too full ({0}% of size: 512000000 [rtbufsize "
                     "parameter])! frame dropped!".format(62 + k % 4))
        if k % 3 == 0:
            lines.append("    Last message repeated 1 times")
    lines.append("[in#0/dshow @ 000001] otra cosa que sí interesa")
    p = P()
    p.stderr = io.BytesIO(("\n".join(lines) + "\n").encode())
    eng = camui.FFmpegEngine("ffmpeg")
    eng._nlog = 0
    eng._drain_err(p)
    cur = read(camui.LOGFILE)
    summ = [l for l in cur.splitlines() if "tira fotogramas" in l]
    assert len(summ) == 2, summ                    # la primera (al momento) y el resto al acabar
    n_tot = sum(int(l.split("(")[1].split(" aviso")[0]) for l in summ)
    assert n_tot == 400, (n_tot, summ)             # 300 + 100 «repeated 1 times»
    assert "hasta el 65 %" in summ[-1], summ
    assert "otra cosa que sí interesa" in cur
    assert eng._nlog == 1, eng._nlog
    assert not any("too full" in e for e in eng.err), list(eng.err)[-3:]
    print("6) 400 avisos de búfer lleno → {0} líneas de resumen («{1}»); las 400 líneas de "
          "ffmpeg, intactas OK".format(len(summ), summ[-1].split("WARNING ")[1][:90]))
finally:
    reset_logging()
    shutil.rmtree(TMP, ignore_errors=True)
print("TODO OK unit_logrot")
