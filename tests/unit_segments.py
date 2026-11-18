"""Grabación en trozos (pack 50): si grabando se gira una cámara, se sigue grabando en otro trozo con
el mosaico recolocado, y al parar se juntan sin recodificar en un solo vídeo. Aquí: trozos MKV de
verdad (vídeo H.264 + audio, del mismo tamaño, como los que graba cam-ui) juntados en MKV y en MP4,
con el primer trozo llamado igual que el archivo final (así graba cam-ui en MKV); y qué pasa con un
solo trozo o con trozos que no se pueden juntar."""
import os
import sys
import json
import types
import shutil
import tempfile
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui  # noqa: E402

TMP = tempfile.mkdtemp(prefix="camui_trozos_")


def part(path, secs, color):
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "color=c={0}:s=320x180:r=30:d={1}".format(color, secs),
                    "-f", "lavfi", "-i", "sine=f=440:d={0}".format(secs),
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", path], check=True)


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_type,width,height:format=duration", "-of", "json", path],
                       capture_output=True, text=True)
    d = json.loads(r.stdout)
    return float(d["format"]["duration"]), sorted(s["codec_type"] for s in d["streams"])


fake = types.SimpleNamespace(eng=types.SimpleNamespace(ffmpeg="ffmpeg"))
fake._finalize = types.MethodType(camui.CamApp._finalize, fake)
finalize_parts = types.MethodType(camui.CamApp._finalize_parts, fake)
try:
    # 1) MP4: trozos .rec.mkv -> un solo .mp4
    p1 = os.path.join(TMP, "Multivista_1.rec.mkv")
    p2 = os.path.join(TMP, "Multivista_1.parte2.rec.mkv")
    p3 = os.path.join(TMP, "Multivista_1.parte3.rec.mkv")
    part(p1, 2.0, "red")
    part(p2, 3.0, "blue")
    part(p3, 1.0, "green")
    final = os.path.join(TMP, "Multivista_1.mp4")
    ok, shown, msg = finalize_parts([p1, p2, p3], final)
    assert ok and shown == final and msg == "", (ok, shown, msg)
    dur, kinds = probe(final)
    assert 5.8 < dur < 6.3 and kinds == ["audio", "video"], (dur, kinds)
    left = sorted(os.listdir(TMP))
    assert left == ["Multivista_1.mp4"], left
    print("1) MP4: 3 trozos (2 + 3 + 1 s, vídeo y audio) -> un solo .mp4 de {0:.1f} s; no queda "
          "ningún trozo OK".format(dur))

    # 2) MKV: el primer trozo se llama como el final
    f2 = os.path.join(TMP, "Multivista_2.mkv")
    q2 = os.path.join(TMP, "Multivista_2.parte2.rec.mkv")
    part(f2, 2.0, "red")
    part(q2, 2.0, "blue")
    ok, shown, msg = finalize_parts([f2, q2], f2)
    assert ok and shown == f2, (ok, shown, msg)
    dur, kinds = probe(f2)
    assert 3.8 < dur < 4.3, dur
    assert not os.path.exists(q2) and not any("junto" in n for n in os.listdir(TMP)), os.listdir(TMP)
    print("2) MKV, con el primer trozo llamado como el final: un solo .mkv de {0:.1f} s OK".format(dur))

    # 3) un solo trozo: como siempre (y uno vacío no cuenta)
    f3 = os.path.join(TMP, "Multivista_3.rec.mkv")
    e3 = os.path.join(TMP, "Multivista_3.parte2.rec.mkv")
    part(f3, 1.0, "red")
    open(e3, "wb").close()
    ok, shown, msg = finalize_parts([f3, e3], os.path.join(TMP, "Multivista_3.mp4"))
    assert ok and shown.endswith("Multivista_3.mp4"), (ok, shown, msg)
    print("3) un trozo bueno y otro vacío: queda el bueno, en MP4 OK")

    # 4) trozos que no se pueden juntar: se dice, y no se borra nada
    f4 = os.path.join(TMP, "Multivista_4.rec.mkv")
    g4 = os.path.join(TMP, "Multivista_4.parte2.rec.mkv")
    part(f4, 1.0, "red")
    with open(g4, "wb") as f:
        f.write(b"esto no es un video" * 100)
    ok, shown, msg = finalize_parts([f4, g4], os.path.join(TMP, "Multivista_4.mp4"))
    assert os.path.exists(f4) and os.path.exists(g4), "no debe borrar nada si no se pudo juntar"
    assert shown == f4 and "no se pudieron juntar" in msg, (ok, shown, msg)
    print("4) si no se pueden juntar: se avisa y se conservan los trozos OK")
finally:
    shutil.rmtree(TMP, ignore_errors=True)
print("TODO OK unit_segments")
