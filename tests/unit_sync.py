"""«Sincronizar» (pack 52): la medida de la claqueta, con grabaciones de verdad (H.264 + AAC en MKV,
como las que graba cam-ui) hechas aquí con lo que vería y oiría cada cámara: un mosaico de 2×2 con
ruido de sensor, algo que se mueve en la imagen, el móvil-claqueta en un sitio distinto según el
momento, y sonido con zumbido, ruido de voz y los pitidos. Se sabe exactamente cuándo llega cada
cosa, y se comprueba que se mide eso:

1) el sonido: dónde está la claqueta, aunque el micrófono no oiga algunos pitidos;
2) cada cámara: lo que llega tarde, aunque solo vea unos pocos destellos y el móvil cambie de
   sitio; una que no lo ve nunca, «no visto» (nada de inventarse un número);
3) las correcciones: «Retardo ms» y «Ajuste A/V» para que todas vayan con la más lenta, partiendo
   de los ajustes con que se midió; sin sonido, solo las cámaras entre sí;
4) la página de la claqueta lleva el mismo patrón de destellos que se busca en la grabación."""
import os
import sys
import shutil
import tempfile
import subprocess

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui  # noqa: E402

FPS = 30
W, H = 1280, 720
TILES = [(0, 0, 640, 360), (640, 0, 640, 360), (0, 360, 640, 360), (640, 360, 640, 360)]
LABELS = ["USB", "Móvil", "cam-ui@omen", "Lejos"]
P = camui.SYNC_PATTERN                        # [(instante, duración)]
A_LAG = 3.0                                   # el primer pitido, en la grabación
CAM_LAG = [3.115, 3.47, 4.08, None]           # el primer destello, en cada casilla
SEES = [set(range(len(P))), set(range(12, 19)), set(range(4, 9)), set()]
HEARS = set(range(len(P))) - set(range(9, 16))   # el móvil se alejó del micro un rato
TMP = tempfile.mkdtemp(prefix="camui_sync_")


def lit(t0, t1, lag, seen):
    """Fracción de (t0, t1] con el destello encendido (lo que recoge un fotograma)."""
    if lag is None:
        return 0.0
    tot = 0.0
    for k in seen:
        a, b = lag + P[k][0], lag + P[k][0] + P[k][1]
        tot += max(0.0, min(t1, b) - max(t0, a))
    return tot / (t1 - t0)


def make(path, secs, cam_lag=CAM_LAG, sees=SEES, a_lag=A_LAG, hears=HEARS, audio=True):
    rng = np.random.default_rng(7)
    bg = [rng.integers(40, 150, (H // 2, W // 2), dtype=np.uint8) for _ in TILES]
    for b in bg:                                       # algo de textura, no ruido puro
        b[:] = (b.astype(np.int32) + np.roll(b, 1, 0) + np.roll(b, 1, 1)) // 3
    n = int(secs * FPS)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray",
           "-s", "{0}x{1}".format(W, H), "-r", str(FPS), "-i", "-"]
    wav = path + ".wav"
    if audio:
        sr = 48000
        t = np.arange(int(secs * sr)) / float(sr)
        x = 0.01 * rng.standard_normal(len(t)) + 0.05 * np.sin(2 * np.pi * 200 * t)
        for s0 in np.arange(1.0, secs - 1, 2.3):        # «voz»: ráfagas graves
            i0, i1 = int(s0 * sr), int((s0 + 0.9) * sr)
            v = rng.standard_normal(i1 - i0)
            v = np.convolve(v, np.ones(12) / 12.0, "same")
            x[i0:i1] += 0.25 * v
        for k in hears:
            a = a_lag + P[k][0]
            i0, i1 = int(a * sr), int((a + P[k][1]) * sr)
            env = np.ones(i1 - i0)
            r = int(0.005 * sr)
            env[:r] = np.linspace(0, 1, r)
            env[-r:] = np.linspace(1, 0, r)
            x[i0:i1] += 0.08 * env * np.sin(2 * np.pi * camui.SYNC_TONE_HZ * (t[i0:i1] - a))
        pcm = (np.clip(x, -1, 1) * 32000).astype("<i2")
        import wave
        with wave.open(wav, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm.tobytes())
        cmd += ["-i", wav, "-c:a", "aac", "-b:a", "128k"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-pix_fmt", "yuv420p", path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(n):
        t1 = i / float(FPS)
        t0 = t1 - 1.0 / FPS
        fr = np.empty((H, W), np.uint8)
        for c, (x0, y0, w, h) in enumerate(TILES):
            img = bg[c].astype(np.int16) + rng.integers(-4, 5, (h, w), dtype=np.int16)
            bx = int((i * 7 + c * 90) % (w - 80))                       # algo que se mueve
            img[100:180, bx:bx + 70] = 210
            if sees[c]:
                # el móvil: cambia de sitio cada pocos destellos
                k_now = int(np.searchsorted(np.asarray([q[0] for q in P]) + (cam_lag[c] or 0), t1))
                py, px = 40 + (k_now // 3 % 3) * 90, 60 + (k_now // 3 % 4) * 130
                f = lit(t0, t1, cam_lag[c], sees[c])
                img[py:py + 40, px:px + 20] = 20 + f * 220
            fr[y0:y0 + h, x0:x0 + w] = np.clip(img, 0, 255).astype(np.uint8)
        p.stdin.write(fr.tobytes())
    p.stdin.close()
    assert p.wait() == 0
    if os.path.exists(wav):
        os.remove(wav)


try:
    secs = A_LAG + camui.SYNC_PATTERN_END + camui.SYNC_TAIL_S + 1.5
    rec = os.path.join(TMP, "prueba.mkv")
    make(rec, secs)

    # 1) el sonido
    t0, x = camui.sync_read_audio(rec)
    a = camui.sync_audio_lag(t0, x, 16000)
    assert a is not None and abs(a[0] - A_LAG) < 0.008, (a, A_LAG)
    assert a[1] == len(HEARS), a
    print("1) sonido: la claqueta empieza a {0:.3f} s (de verdad {1:.3f}), {2} pitidos oídos de "
          "{3} (el micro no oyó {4}) OK".format(a[0], A_LAG, a[1], len(P), len(P) - len(HEARS)))

    # 2) cada cámara
    res = camui.analyze_sync(rec, TILES, W, H, LABELS)
    assert res["audio"] and abs(res["audio"][0] - A_LAG) < 0.008
    for lab, lag, seen in zip(LABELS, CAM_LAG, SEES):
        r = res["cams"][lab]
        if lag is None:
            assert r is None, (lab, r)
            continue
        assert r is not None, (lab, res["cams"])
        assert abs(r[0] - lag) < 0.012, (lab, r, lag)
        assert r[1] >= min(len(seen), 4), (lab, r, len(seen))
    late = dict((l, round((res["cams"][l][0] - res["audio"][0]) * 1000))
                for l in LABELS if res["cams"][l])
    print("2) cámaras (lo que llega tarde respecto al sonido): " + ", ".join(
        "{0} {1} ms (de verdad {2:.0f}, {3} destellos)".format(
            l, late[l], (g - A_LAG) * 1000, res["cams"][l][1])
        for l, g in zip(LABELS, CAM_LAG) if g is not None) +
        "; «Lejos» no lo vio: sin número OK")

    # 3) correcciones
    delays = {"USB": 0, "Móvil": 0, "cam-ui@omen": 0, "Lejos": 150}
    new, av, lt = camui.sync_corrections(res, delays, 0)
    # todas con la más lenta (omen, ~1080 ms): sonido +1080, USB +965, móvil +610, omen 0
    want = {"USB": 1080 - 115, "Móvil": 1080 - 470, "cam-ui@omen": 0}
    for l, v in want.items():
        assert abs(new[l] - v) <= 12, (l, new, want)
    assert abs(av - 1080) <= 12, av
    assert new["Lejos"] == 150 + av, (new["Lejos"], av)     # sigue igual respecto al sonido
    # partiendo de otros ajustes: lo mismo, relativo a ellos (y lo más pequeño a 0)
    new2, av2, _ = camui.sync_corrections(res, {"USB": 300, "Móvil": 300, "cam-ui@omen": 300,
                                                "Lejos": 300}, 300)
    assert new2 == new or all(abs(new2[l] - new[l]) <= 1 for l in want), (new2, new)
    # comprobación: con lo corregido, cada cámara a la vez que el sonido
    for l in want:
        shown = (res["cams"][l][0] - res["audio"][0]) * 1000 + (new[l] - delays[l])
        assert abs(shown - (av - 0)) <= 12, (l, shown, av)
    # sin sonido: solo las cámaras entre sí (respecto a la más lenta), el sonido igual
    res_ns = {"audio": None, "cams": res["cams"]}
    new3, av3, _ = camui.sync_corrections(res_ns, {"USB": 0, "Móvil": 0, "cam-ui@omen": 0}, 40)
    assert av3 == 40 and new3["cam-ui@omen"] == 0 and abs(new3["USB"] - 965) <= 12, (new3, av3)
    assert camui.sync_corrections({"audio": res["audio"], "cams": {"USB": None}}, {"USB": 0}, 0) is None
    print("3) correcciones: sonido +{0} ms; Retardo USB {1}, Móvil {2}, cam-ui@omen {3} (la más "
          "lenta); la que no se vio sigue igual respecto al sonido; sin sonido, solo entre "
          "cámaras OK".format(av, new["USB"], new["Móvil"], new["cam-ui@omen"]))

    # 3b) sin sonido en la grabación (no hay micro): se miden las cámaras igual
    rec2 = os.path.join(TMP, "sin_sonido.mkv")
    make(rec2, secs, audio=False)
    res2 = camui.analyze_sync(rec2, TILES, W, H, LABELS, has_audio=True)
    assert res2["audio"] is None
    d01 = res2["cams"]["Móvil"][0] - res2["cams"]["USB"][0]
    assert abs(d01 - (CAM_LAG[1] - CAM_LAG[0])) < 0.015, d01
    print("3b) sin micro: las cámaras entre sí ({0:.0f} ms entre USB y móvil) OK".format(d01 * 1000))

    # 4) la página de la claqueta lleva el mismo patrón
    page = camui.sync_page(token="abc", pcname="PC")
    import json
    import re
    m = re.search(rb"var P = (\[.*?\]\]);", page)
    assert m and [tuple(q) for q in json.loads(m.group(1))] == [tuple(q) for q in P], page[:300]
    assert b"__SYNC_" not in page
    print("4) la página de la claqueta: {0} destellos, los mismos que se buscan OK".format(len(P)))
finally:
    shutil.rmtree(TMP, ignore_errors=True)
print("TODO OK unit_sync")
