"""«Sincronizar» (packs 52 y 55): la medida de la claqueta, con grabaciones de verdad (H.264 + AAC en
MKV, como las que graba cam-ui) hechas aquí con lo que vería y oiría cada cámara.

La claqueta es un código (SYNC_CODE): diez trozos por segundo, cada uno blanco con pitido o negro
en silencio. Aquí: un mosaico de 3×3 con ocho cámaras que la ven cada una solo unos 3,5 s (se les
va enseñando el móvil, que se ve pequeño y se mueve), cada una con su retraso (0,08 a 1,1 s) y sus
fps (10, 15, 20 o 30: las lentas se repiten en el mosaico), y cada fotograma recoge la luz de su
intervalo. En la imagen, además, ruido, algo que se mueve, la luz que sube y baja y una luz que
parpadea sola. El micrófono oye la claqueta solo al principio y al final (el móvil se alejó).
Se sabe exactamente cuándo llega cada cosa, y se comprueba que se mide eso, al milisegundo:

0) el código: 1023 trozos, mitad encendidos, ningún tramo de 10 se repite;
1) el sonido: dónde empieza la claqueta, aunque solo se oiga una parte;
2) las ocho cámaras: lo que llega tarde cada una (± 12 ms), con lo poco que ve cada una; la novena,
   que no la ve nunca, «no vista» (nada de inventarse un número);
3) las correcciones («Retardo ms» y «Ajuste A/V») para que todas vayan con la más lenta; sin
   sonido, solo las cámaras entre sí;
4) la página de la claqueta lleva el mismo código que se busca en la grabación;
5) en directo (SyncLive): ✓ en orden, según se les enseña el móvil;
6) (pack 58) tres cámaras que ven la claqueta flojita: solas no salen («se parece poco»), pero
   si salieron ✓ en directo se buscan donde se las vio y salen bien (± 30 ms); a una que no la vio
   nunca no se le inventa nada aunque se le dé una pista, y una pista equivocada no cuela."""
import os
import re
import sys
import json
import wave
import shutil
import tempfile
import subprocess

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui  # noqa: E402

FPS = 30
W, H = 1280, 720
TW, TH = 426, 240
TILES = [((i % 3) * TW, (i // 3) * TH, TW, TH) for i in range(9)]
LABELS = ["Cam{0}".format(i + 1) for i in range(8)] + ["Lejos"]
NBITS = 430                                       # la claqueta duró 40 s (se terminó antes)
CODE = camui.SYNC_CODE[:NBITS]
BIT = camui.SYNC_BIT_S
A_LAG = 3.0                                       # el primer trozo, en el sonido de la grabación
LAT = [0.08, 0.15, 0.31, 0.47, 0.62, 0.85, 1.10, 0.20]       # lo que llega tarde cada cámara
CFPS = [30, 30, 20, 15, 10, 30, 15, 30]                      # sus fps
SEE = [(4 * c + 2.0, 4 * c + 5.5) for c in range(8)]         # cuándo (del código) se le enseña
HEAR = [(0.0, 12.0), (31.0, 40.0)]                           # cuándo lo oye el micrófono
TMP = tempfile.mkdtemp(prefix="camui_sync_")
F = camui._sync_on_time(CODE, BIT)


def lit_fraction(a, b):
    return (F(b) - F(a)) / (b - a)


def make(path, secs, audio=True, amp=None, noise=3):
    rng = np.random.default_rng(11)
    bg = []
    for _ in TILES:
        b = rng.integers(50, 140, (TH, TW)).astype(np.float32)
        bg.append((b + np.roll(b, 1, 0) + np.roll(b, 1, 1)) / 3.0)
    n = int(secs * FPS)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray",
           "-s", "{0}x{1}".format(W, H), "-r", str(FPS), "-i", "-"]
    wav = path + ".wav"
    if audio:
        sr = 48000
        t = np.arange(int(secs * sr)) / float(sr)
        x = 0.01 * rng.standard_normal(len(t)) + 0.05 * np.sin(2 * np.pi * 200 * t)
        for s0 in np.arange(1.0, secs - 1, 2.3):               # «voz»
            i0, i1 = int(s0 * sr), int((s0 + 0.9) * sr)
            x[i0:i1] += 0.25 * np.convolve(rng.standard_normal(i1 - i0), np.ones(12) / 12.0, "same")
        rel = t - A_LAG
        k = np.floor(rel / BIT).astype(int)
        on = (k >= 0) & (k < NBITS)
        on[on] = np.asarray(CODE)[k[on]] == 1
        near = np.zeros(len(t), bool)
        for a, b in HEAR:
            near |= (rel >= a) & (rel < b)
        g = np.convolve((on & near).astype(float), np.ones(144) / 144.0, "same")   # rampas de 3 ms
        x += 0.06 * g * np.sin(2 * np.pi * camui.SYNC_TONE_HZ * t)
        pcm = (np.clip(x, -1, 1) * 32000).astype("<i2")
        with wave.open(wav, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm.tobytes())
        cmd += ["-i", wav, "-c:a", "aac", "-b:a", "128k"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-pix_fmt", "yuv420p", path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    held = [None] * 9                              # lo último que mandó cada cámara
    for i in range(n):
        ti = i / float(FPS)
        fr = np.zeros((H, W), np.uint8)
        for c, (x0, y0, w, h) in enumerate(TILES):
            per = FPS // CFPS[c] if c < 8 else 1
            if i % per == 0 or held[c] is None:   # una imagen nueva de esa cámara (las lentas se repiten)
                img = bg[c] * (1.0 + 0.08 * np.sin(2 * np.pi * ti / 7.0 + c))   # la luz sube y baja
                img = img + rng.normal(0, noise, (h, w))
                bx = int((i * 6 + c * 70) % (w - 60))                           # algo que se mueve
                img[150:200, bx:bx + 50] = 200
                if c == 8:                                                      # una luz que parpadea sola
                    img[20:40, 20:40] = 230 if (i // 7) % 2 else 30
                if c < 8:
                    s = ti - LAT[c] - A_LAG                                     # hora del código que ve
                    a, b = SEE[c]
                    if a - 0.5 <= s <= b + 0.5:                                 # el móvil, pequeño, se mueve
                        py = int(60 + 30 * np.sin((s - a) * 0.7))
                        px = int(150 + 25 * (s - a))
                        f = lit_fraction(s - per / float(FPS), s) if a <= s <= b else 0.0
                        img[py:py + 18, px:px + 9] = 15 + f * (225 if amp is None else amp[c])
                held[c] = np.clip(img, 0, 255).astype(np.uint8)
            fr[y0:y0 + h, x0:x0 + w] = held[c]
        p.stdin.write(fr.tobytes())
    p.stdin.close()
    assert p.wait() == 0
    if os.path.exists(wav):
        os.remove(wav)


try:
    # 0) el código
    code = camui.SYNC_CODE
    assert len(code) == 1023 and sum(code) == 512, (len(code), sum(code))
    wins = set(tuple(code[i:i + 10]) for i in range(len(code) - 9))
    assert len(wins) == len(code) - 9
    print("0) el código: {0} trozos ({1:.0f} s a {2:.1f} por segundo), {3} encendidos; ningún tramo de "
          "10 seguidos se repite OK".format(len(code), len(code) * BIT, 1 / BIT, sum(code)))

    secs = A_LAG + NBITS * BIT + camui.SYNC_TAIL_S + 1.0
    rec = os.path.join(TMP, "prueba.mkv")
    make(rec, secs)

    # 1) el sonido
    t0, x = camui.sync_read_audio(rec)
    a = camui.sync_audio_lag(t0, x, 16000, CODE)
    assert a is not None and abs(a[0] - A_LAG) < 0.004, (a, A_LAG)
    heard = sum(b - a_ for a_, b in HEAR)
    assert abs(a[1] - heard) <= 2, (a, heard)
    print("1) sonido: la claqueta empieza a {0:.4f} s (de verdad {1:.4f}); oída {2:.0f} s de {3:.0f} "
          "(desacuerdo {4:.1f} ms) OK".format(a[0], A_LAG, a[1], NBITS * BIT, a[2] * 1000))

    # 2) las cámaras
    res = camui.analyze_sync(rec, TILES, W, H, LABELS, nbits=NBITS)
    assert res["audio"] and abs(res["audio"][0] - A_LAG) < 0.004
    errs = []
    for c, lab in enumerate(LABELS):
        r = res["cams"][lab]
        if c == 8:
            assert r is None, (lab, r)
            continue
        assert r is not None, (lab, res["cams"])
        want = A_LAG + LAT[c]
        e = (r[0] - want) * 1000
        errs.append(e)
        assert abs(e) <= 12.0, (lab, r, want, e)
        assert 2.0 <= r[1] <= 5.0, (lab, r)
    print("2) ocho cámaras, cada una viendo la claqueta ~3,5 s: error " + ", ".join(
        "{0:+.1f}".format(e) for e in errs) + " ms (lo que llegaban tarde: " +
        ", ".join("{0:.0f}".format(l * 1000) for l in LAT) + " ms; a 30/20/15/10 fps); "
        "la que no la vio, sin número OK")

    # 3) correcciones
    delays = dict((l, 0) for l in LABELS)
    delays["Lejos"] = 150
    new, av, late = camui.sync_corrections(res, delays, 0)
    slow = max(LAT) * 1000
    for c in range(8):
        assert abs(new[LABELS[c]] - (slow - LAT[c] * 1000)) <= 20, (LABELS[c], new)
    assert abs(av - slow) <= 12 and new["Lejos"] == 150 + av, (av, new["Lejos"])
    res_ns = {"audio": None, "cams": res["cams"]}
    new3, av3, _ = camui.sync_corrections(res_ns, dict((l, 0) for l in LABELS[:8]), 40)
    assert av3 == 40 and new3["Cam7"] == 0 and abs(new3["Cam1"] - (slow - 80)) <= 20, (new3, av3)
    print("3) correcciones: sonido +{0} ms, «Retardo» de cada una lo que le falta para ir con la más "
          "lenta; la que no se vio, igual respecto al sonido; sin sonido, solo entre cámaras "
          "OK".format(av))

    # 3b) sin micro: las cámaras entre sí
    rec2 = os.path.join(TMP, "sin_sonido.mkv")
    make(rec2, secs, audio=False)
    res2 = camui.analyze_sync(rec2, TILES, W, H, LABELS, has_audio=True, nbits=NBITS)
    assert res2["audio"] is None
    rel = [(res2["cams"][LABELS[c]][0] - res2["cams"]["Cam1"][0] - (LAT[c] - LAT[0])) * 1000
           for c in range(8)]
    assert all(abs(e) <= 20 for e in rel), rel
    print("3b) sin micro: las ocho entre sí (error " + ", ".join("{0:+.1f}".format(e) for e in rel) +
          " ms) OK")

    # 4) la página de la claqueta lleva el mismo código
    page = camui.sync_page(token="abc", pcname="PC")
    m = re.search(rb'var CODE = "([01]+)";', page)
    assert m and [int(ch) for ch in m.group(1).decode()] == camui.SYNC_CODE[:camui.SYNC_MAX_BITS]
    mb = re.search(rb"var BIT = ([0-9.]+);", page)
    assert mb and float(mb.group(1)) == camui.SYNC_BIT_S, mb
    assert b"__SYNC_" not in page
    print("4) la página de la claqueta lleva el mismo código ({0} trozos) OK".format(len(m.group(1))))

    # 5) en directo (SyncLive): las imágenes de la vista previa, una a una, y cada segundo qué
    # cámaras ya han visto bastante claqueta -- ✓ en orden, según se les enseña el móvil
    PW, PH = 864, 486                                  # la vista previa (3×3 casillas de 288×162)
    ptiles = [((i % 3) * 426, (i // 3) * 240, 426, 240) for i in range(9)]
    live = camui.SyncLive(LABELS, ptiles, 1278, PW, PH)
    T0 = 1000.0                                        # hora del PC a la que empieza la claqueta
    live.t0 = T0
    LLAT = [0.4 + 0.1 * c for c in range(8)]           # en directo, lo que tarda cada casilla
    rng2 = np.random.default_rng(5)
    base = rng2.integers(40, 140, (PH, PW)).astype(np.float32)
    marks = {}
    t = T0 - 1.0
    k = 0
    while t < T0 + 36.0:
        img = base + rng2.normal(0, 2, (PH, PW))
        for c in range(8):
            x0, y0 = (c % 3) * 288, (c // 3) * 162
            s_ = t - T0 - LLAT[c]                      # hora del código que ve esa casilla
            a, b = SEE[c]
            if a - 0.3 <= s_ <= b + 0.3:
                f = lit_fraction(s_ - 1 / 30.0, s_) if a <= s_ <= b else 0.0
                px = x0 + 100 + int(6 * (s_ - a))
                img[y0 + 40:y0 + 52, px:px + 6] = 15 + 225 * f
        bx = int((k * 5) % 800)                        # algo que se mueve por todo
        img[300:330, bx:bx + 40] = 220
        g8 = np.clip(img, 0, 255).astype(np.uint8)
        live.tap(np.dstack([g8, g8, g8]), t)
        k += 1
        if k % 30 == 0:                                # cada segundo
            live.check()
            for lab in live.ok:
                marks.setdefault(lab, t - T0)
        t += 1 / 30.0
    order = [LABELS[c] for c in range(8)]
    assert list(marks) == order, (marks, live.missing())
    for c in range(8):
        lab = LABELS[c]
        start = SEE[c][0] + LLAT[c]
        assert start + 1.0 <= marks[lab] <= start + 4.5, (lab, marks[lab], start)
    assert "Lejos" not in live.ok and live.missing() == ["Lejos"]
    print("5) en directo: las ocho, ✓ una tras otra según se les enseña el móvil (a los " +
          ", ".join("{0:.0f}".format(marks[LABELS[c]] - SEE[c][0] - LLAT[c]) for c in range(8)) +
          " s de empezar a verlo); «Lejos», sin ✓ OK")

    # 6) flojas: con lo visto en directo
    WEAK = {0: 16, 3: 22, 4: 16}                       # Cam1 (30 fps), Cam4 (15), Cam5 (10)
    rec3 = os.path.join(TMP, "flojas.mkv")
    make(rec3, secs, amp=[WEAK.get(c, 225) for c in range(8)], noise=8)
    plain = camui.analyze_sync(rec3, TILES, W, H, LABELS, nbits=NBITS)
    for c in WEAK:
        assert plain["cams"][LABELS[c]] is None, (LABELS[c], plain["cams"][LABELS[c]])
    OFF = 500.0                                        # hora del PC = hora de la grabación + OFF
    hint = dict((LABELS[c], A_LAG + LAT[c] + OFF + 0.02) for c in range(8))   # (en directo, 20 ms off)
    hint["Lejos"] = A_LAG + 0.4 + OFF                  # una pista para la que no la vio nunca
    res3 = camui.analyze_sync(rec3, TILES, W, H, LABELS, nbits=NBITS, live=hint)
    e3 = []
    for c in WEAK:
        r = res3["cams"][LABELS[c]]
        assert r is not None and res3["info"][LABELS[c]].get("con_directo"), (LABELS[c], res3["info"])
        e3.append((r[0] - A_LAG - LAT[c]) * 1000)
        assert abs(e3[-1]) <= 30, (LABELS[c], e3[-1])
    assert res3["cams"]["Lejos"] is None
    bad = dict(hint, Cam4=hint["Cam4"] + 0.3)          # pista equivocada (0,3 s)
    res4 = camui.analyze_sync(rec3, TILES, W, H, LABELS, nbits=NBITS, live=bad)
    assert res4["cams"]["Cam4"] is None, res4["cams"]["Cam4"]
    print("6) tres cámaras que la ven flojita: solas, no salen; con lo visto en directo, sí (error " +
          ", ".join("{0:+.1f}".format(e) for e in e3) + " ms); a la que no la vio, nada aunque se le "
          "dé pista; y una pista equivocada (0,3 s) no cuela OK")
finally:
    shutil.rmtree(TMP, ignore_errors=True)
print("TODO OK unit_sync")
