# unit_web_lang.py
"""Paginas que se abren en el movil u otro equipo (visor, camara del movil, emparejar, claqueta,
certificado): salen en el idioma del navegador (Accept-Language), sin marcas sin rellenar, con el
JavaScript valido (si hay node), y lo que el PC le dice a la claqueta se dice en el idioma del movil.
python3 tests/unit_web_lang.py"""
import os, re, sys, shutil, subprocess, tempfile
os.environ.pop("LANGKIT_LANG", None)        # aqui se prueba justamente la eleccion por navegador
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import camui, langkit
cur = camui.lang()
class H(dict):
    def get(self, k, d=None): return dict.get(self, k, d)
for al, want in (("ca-ES,ca;q=0.9,en;q=0.8", "ca"), ("en-US,en;q=0.9", "en"), ("es-ES", "es"),
                 ("fr-FR,fr;q=0.9", "en"), ("de;q=0.5,ca;q=0.8", "ca"), ("", "en")):
    got = camui._web_lang(H({"Accept-Language": al})).code
    assert got == want, (al, got, want)
pages = {"viewer": camui._VIEWER_TPL, "cam": camui._PHONECAM_TPL, "add": camui._PHONECAM_ADD_TPL,
         "sync": camui._SYNC_TPL, "cert": camui._PHONECAM_CERT_PAGE.replace("%%NOSAFARI%%", camui._PHONECAM_NOSAFARI)}
bad = 0
for code in ("es", "en", "ca"):
    L = langkit.Lang(camui.__file__, lang=code)
    for name, tpl in pages.items():
        out = camui._web_render(tpl, L)
        if "⟦" in out or "web." in re.sub(r"<script[^>]*src=[^>]*>", "", out).replace("cam-ui web", ""):
            left = re.findall(r"⟦[^⟧]*⟧|\bweb\.[a-z_.]+", out)[:5]
            if left: print(code, name, "QUEDAN", left); bad += 1
        for i, js in enumerate(re.findall(r"<script>(.*?)</script>", out, re.S)):
            f = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8"); f.write(js); f.close()
            r = subprocess.run(["node", "--check", f.name], capture_output=True, text=True) if shutil.which("node") else None
            os.unlink(f.name)
            if r is not None and r.returncode: print(code, name, "JS", r.stderr[:300]); bad += 1
        if code != "es":
            for es_txt in ("Empezar", "Pantalla completa", "Contraseña", "Enseña esta pantalla", "Claqueta de"):
                if es_txt in out and not (code == "ca" and es_txt in ("Pantalla completa", "Claqueta de")):
                    print(code, name, "castellano:", es_txt); bad += 1
assert not bad, bad
print("1) paginas en es/en/ca: sin marcas, JavaScript valido, sin restos en castellano OK")
# mensaje del PC para la claqueta: en el idioma del movil
m = camui._LMsg.t("sync.devs_missing", n=2, total=3)
j = camui._lt_join("\n", [m, camui._LMsg.t("sync.cancelled")])
want = {"en": "2 of 3 missing\nCancelled.", "ca": "En falten 2 de 3\nCancel·lat.", "es": "Faltan 2 de 3\nCancelado."}
for code, w in want.items():
    got = j.render(langkit.Lang(camui.__file__, lang=code))
    assert got == w, (code, got)
print("2) idioma por Accept-Language y mensajes del PC en el idioma del movil OK")
print("TODO OK unit_web_lang")
