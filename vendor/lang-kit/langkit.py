# langkit.py
"""lang-kit: los textos de un programa en varios idiomas, sacados de archivos JSON.

Diccionarios: <carpeta del programa>/lang/[<dominio>.]<idioma>.json, por ejemplo
    lang/es.json          los textos propios del programa
    lang/en.json
    lang/pyi.es.json      los del instalador y el actualizador (los pone py-installer)
    lang/common.es.json   los comunes de lang-kit: Aceptar, Cancelar, Idioma...

    {
      "@meta": {"name": "Español"},
      "menu.file.open": "Abrir…",
      "status.saved": "Guardado en {path}",
      "files.count": {"one": "{n} archivo", "other": "{n} archivos"}
    }

Idioma elegido: <carpeta del programa>/lang.json ({"lang": "ca"}). Lo escribe el instalador con el
idioma de quien lo descargó y lo cambia el menú «Idioma» del programa. Sin lang.json se usa el
idioma del sistema; si el programa no lo tiene, ingles y luego el original. La variable LANGKIT_LANG
manda sobre todo (pruebas).

Si a un idioma le falta un texto, sale en inglés; si tampoco está, en el idioma original (español).

Uso:
    import langkit
    L = langkit.init(__file__)          # una vez, al arrancar
    from langkit import t
    t('menu.file.open')                 # 'Abrir…'
    t('files.count', n=3)               # '3 archivos'
    langkit.tk_menu(menubar)            # submenú «Idioma» (o qt_menu para Qt)
"""
import json
import os
import re
import sys

LANGKIT_VERSION = '1.1.0'
HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = 'lang.json'
ENV = 'LANGKIT_LANG'

# nombres de cada idioma en su propio idioma (si el diccionario no trae "@meta": {"name": ...})
NAMES = {
    'es': 'Español', 'en': 'English', 'ca': 'Català', 'fr': 'Français', 'de': 'Deutsch', 'pt': 'Português',
    'pt-BR': 'Português (Brasil)', 'it': 'Italiano', 'eu': 'Euskara', 'gl': 'Galego', 'nl': 'Nederlands',
    'pl': 'Polski', 'ru': 'Русский', 'uk': 'Українська', 'ja': '日本語', 'zh': '中文', 'ko': '한국어',
    'ar': 'العربية', 'tr': 'Türkçe', 'sv': 'Svenska', 'da': 'Dansk', 'nb': 'Norsk bokmål', 'fi': 'Suomi',
    'cs': 'Čeština', 'ro': 'Română', 'hu': 'Magyar', 'el': 'Ελληνικά',
}
_FILE = re.compile(r'^(?:(?P<dom>[A-Za-z0-9_-]+)\.)?(?P<code>[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*)\.json$')


# ---------------------------------------------------------------------------------------------
# plurales y variables
# ---------------------------------------------------------------------------------------------
def plural_form(code, n):
    """Forma de plural (CLDR) que toca para n en ese idioma: 'zero'/'one'/'few'/'many'/'other'."""
    try:
        n = abs(float(n))
    except (TypeError, ValueError):
        return 'other'
    i = int(n)
    whole = n == i
    base = (code or '').split('-')[0]
    if base in ('ja', 'zh', 'ko', 'tr', 'vi', 'th', 'id'):
        return 'other'
    if base == 'fr' or code == 'pt-BR':
        return 'one' if n < 2 else 'other'
    if base in ('ru', 'uk'):
        if not whole:
            return 'other'
        if i % 10 == 1 and i % 100 != 11:
            return 'one'
        if 2 <= i % 10 <= 4 and not 12 <= i % 100 <= 14:
            return 'few'
        return 'many'
    if base == 'pl':
        if not whole:
            return 'other'
        if i == 1:
            return 'one'
        if 2 <= i % 10 <= 4 and not 12 <= i % 100 <= 14:
            return 'few'
        return 'many'
    if base == 'cs':
        return 'one' if n == 1 else ('few' if whole and 2 <= i <= 4 else 'other')
    return 'one' if n == 1 else 'other'


class _Vars(dict):
    def __missing__(self, k):           # una variable que no se pasa se deja tal cual: {nombre}
        return '{' + k + '}'


def fmt(text, values):
    """Sustituye {variable} en el texto. Las llaves literales se escriben {{ y }}."""
    if not values or '{' not in text:
        return text.replace('{{', '{').replace('}}', '}') if '{{' in text or '}}' in text else text
    try:
        return text.format_map(_Vars(values))
    except (ValueError, IndexError, KeyError, AttributeError, TypeError):
        return text


# ---------------------------------------------------------------------------------------------
# idioma del sistema
# ---------------------------------------------------------------------------------------------
def _norm(code):
    """'es_ES.UTF-8' -> 'es-ES'; 'C'/'POSIX' -> ''."""
    code = (code or '').split('.')[0].split('@')[0].replace('_', '-').strip()
    if code in ('C', 'POSIX') or not re.fullmatch(r'[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*', code):
        return ''
    parts = code.split('-')
    return '-'.join([parts[0].lower()] + [p.upper() if len(p) == 2 else p for p in parts[1:]])


def system_languages():
    """Idiomas preferidos del sistema, del más al menos preferido (['ca-ES', 'es', 'en'...])."""
    out = []
    for var in ('LANGUAGE', 'LC_ALL', 'LC_MESSAGES', 'LANG'):
        for c in os.environ.get(var, '').split(':'):
            if _norm(c):
                out.append(_norm(c))
    if sys.platform == 'win32':
        try:
            import ctypes
            import locale
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            if locale.windows_locale.get(lcid):
                out.append(_norm(locale.windows_locale[lcid]))
        except Exception:  # noqa: BLE001
            pass
    elif sys.platform == 'darwin' and not out:
        try:
            import subprocess
            r = subprocess.run(['defaults', 'read', '-g', 'AppleLanguages'], capture_output=True, text=True, timeout=2)
            out += [_norm(c) for c in re.findall(r'"?([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]+)*)"?', r.stdout) if _norm(c)]
        except Exception:  # noqa: BLE001
            pass
    if not out:
        try:
            import locale
            c = _norm((locale.getlocale()[0] or ''))
            if c:
                out.append(c)
        except Exception:  # noqa: BLE001
            pass
    return list(dict.fromkeys(out))


def match(wanted, available):
    """El idioma de `available` que mejor sirve para `wanted` ('ca-ES' -> 'ca'; 'pt' -> 'pt-BR'), o None."""
    w = _norm(wanted)
    if not w:
        return None
    low = {a.lower(): a for a in available}
    if w.lower() in low:
        return low[w.lower()]
    base = w.split('-')[0]
    if base in low:
        return low[base]
    for a in available:
        if a.split('-')[0].lower() == base:
            return a
    return None


# ---------------------------------------------------------------------------------------------
# Lang
# ---------------------------------------------------------------------------------------------
class Lang:
    """Diccionarios de un programa y el idioma elegido.

    where:    el .py del programa o su carpeta (por defecto, la del programa que se ejecuta)
    lang:     forzar un idioma (si no, LANGKIT_LANG, lang.json, el del sistema y el original)
    dirs:     carpetas con diccionarios (por defecto <carpeta>/lang; siempre se añade la de lang-kit)
    source:   idioma en que se escribieron los textos (el último recurso), 'es'
    fallback: idiomas de reserva antes del original, ('en',)
    settings: dónde se guarda el idioma elegido (por defecto <carpeta>/lang.json)
    """

    def __init__(self, where=None, lang=None, dirs=None, source='es', fallback=('en',), settings=None):
        if where is None:
            where = sys.argv[0] if sys.argv and sys.argv[0] else os.getcwd()
        where = os.path.abspath(where)
        self.base = where if os.path.isdir(where) else os.path.dirname(where)
        self.dirs = [os.path.abspath(d) for d in (dirs or [os.path.join(self.base, 'lang')])]
        own = os.path.join(HERE, 'lang')
        if os.path.isdir(own) and own not in self.dirs:
            self.dirs.append(own)
        self.source = source
        self.fallback = tuple(fallback)
        self.settings = settings or os.path.join(self.base, SETTINGS)
        self.missing = set()
        self.errors = []
        self._listeners = []
        self._dicts, self._meta = {}, {}
        self.reload()
        self.code = self._resolve(lang)
        self._chain()

    # ---- carga ---------------------------------------------------------------------------
    def reload(self):
        """Vuelve a leer los diccionarios (el primero que define una clave manda)."""
        self._dicts, self._meta, self.errors = {}, {}, []
        for d in self.dirs:
            try:
                names = os.listdir(d)
            except OSError:
                continue
            # en cada carpeta, primero el diccionario propio (es.json) y luego los de dominio (pyi.es.json)
            for name in sorted(names, key=lambda n: (n.count('.'), n)):
                m = _FILE.match(name)
                if not m:
                    continue
                try:
                    with open(os.path.join(d, name), encoding='utf-8') as f:
                        data = json.load(f)
                    if not isinstance(data, dict):
                        raise ValueError('no es un objeto JSON')
                except (OSError, ValueError) as e:
                    self.errors.append('%s: %s' % (os.path.join(d, name), e))
                    continue
                code = m.group('code')
                dic = self._dicts.setdefault(code, {})
                meta = data.get('@meta')
                if isinstance(meta, dict) and meta.get('name') and code not in self._meta:
                    self._meta[code] = str(meta['name'])
                for k, v in data.items():
                    if not k.startswith('@') and k not in dic and isinstance(v, (str, dict)):
                        dic[k] = v

    def _saved(self):
        try:
            with open(self.settings, encoding='utf-8') as f:
                return str(json.load(f).get('lang') or '')
        except (OSError, ValueError, AttributeError):
            return ''

    def _resolve(self, lang=None):
        avail = list(self._dicts)
        # si ninguno de los del sistema esta, el de reserva (ingles) antes que el original: lo entiende mas gente
        for cand in [lang, os.environ.get(ENV), self._saved()] + system_languages() + list(self.fallback) + [self.source]:
            got = match(cand, avail) if cand else None
            if got:
                return got
        return self.source if self.source in self._dicts or not avail else avail[0]

    def _chain(self):
        c = [self.code]
        if '-' in self.code:
            c.append(self.code.split('-')[0])
        self.chain = [x for x in dict.fromkeys(c + list(self.fallback) + [self.source]) if x in self._dicts]

    # ---- consulta ------------------------------------------------------------------------
    def t(self, key, /, _default=None, **values):
        """El texto de `key` en el idioma elegido, con sus {variables} sustituidas.
        Si es un plural ({"one": ..., "other": ...}) se elige con n= (o count=). Las variables pueden
        llamarse como quieras, tambien key: t('x', key='Ctrl+V')."""
        v, code = None, self.code
        for c in self.chain:
            if key in self._dicts[c]:
                v, code = self._dicts[c][key], c
                break
        if v is None:
            self.missing.add(key)
            if _default is None:
                return key
            v = _default
        if isinstance(v, dict):
            n = values.get('n', values.get('count'))
            v = (v.get('zero') if n == 0 and 'zero' in v else None) or v.get(plural_form(code, n)) or v.get('other') or next(iter(v.values()), key)
        return fmt(str(v), values)

    __call__ = t

    def has(self, key):
        return any(key in self._dicts[c] for c in self.chain)

    def keys(self, code=None):
        return set(self._dicts.get(code or self.code, {}))

    def name(self, code=None):
        """Nombre del idioma en su propio idioma ('Català')."""
        code = code or self.code
        return self._meta.get(code) or NAMES.get(code) or NAMES.get(code.split('-')[0]) or code

    def available(self):
        """[(código, nombre)] de los idiomas con diccionario, con el original primero."""
        codes = sorted(self._dicts, key=lambda c: (c != self.source, self.name(c).lower()))
        return [(c, self.name(c)) for c in codes]

    # ---- cambiar de idioma ---------------------------------------------------------------
    def set(self, code, save=True):
        """Cambia de idioma (y lo guarda en lang.json). Devuelve el código que quedó elegido."""
        got = match(code, list(self._dicts)) or self.code
        changed = got != self.code
        self.code = got
        self._chain()
        if save:
            self.save()
        if changed:
            for fn in list(self._listeners):
                try:
                    fn(got)
                except Exception as e:  # noqa: BLE001
                    print('langkit: %s' % e, file=sys.stderr)
        return got

    def save(self):
        """Guarda el idioma elegido en lang.json (sin perder lo demás que haya). False si no se pudo."""
        data = {}
        try:
            with open(self.settings, encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            pass
        data['lang'] = self.code
        tmp = self.settings + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.settings)
            return True
        except OSError:
            return False

    def on_change(self, fn):
        """fn(código) se llama al cambiar de idioma (para volver a rotular la interfaz en vivo)."""
        self._listeners.append(fn)
        return fn

    def off_change(self, fn):
        if fn in self._listeners:
            self._listeners.remove(fn)

    def menu_label(self):
        """Rótulo del menú de idiomas: «Idioma / Language», para encontrarlo aunque no entiendas el actual."""
        lab = self.t('common.language', _default='Idioma')
        en = self._dicts.get('en', {}).get('common.language', 'Language')
        return lab if lab == en else '%s / %s' % (lab, en)


# ---------------------------------------------------------------------------------------------
# un diccionario para todo el programa
# ---------------------------------------------------------------------------------------------
_current = None


def init(where=None, **kw):
    """Carga los diccionarios del programa y los deja como los de langkit.t(). Devuelve el Lang."""
    global _current
    _current = Lang(where, **kw)
    return _current


def get():
    return _current or init()


def t(key, /, _default=None, **values):
    return get().t(key, _default, **values)


def restart_program():
    """Vuelve a abrir el programa con los mismos argumentos (quien llama cierra la ventana después)."""
    import subprocess
    exe = sys.executable
    if sys.platform == 'win32' and exe.lower().endswith('python.exe'):
        w = exe[:-10] + 'pythonw.exe'
        if os.path.exists(w):
            exe = w
    subprocess.Popen([exe] + sys.argv, close_fds=True, cwd=os.getcwd())


# ---------------------------------------------------------------------------------------------
# menús «Idioma» listos para usar
# ---------------------------------------------------------------------------------------------
def _after_change(L, code, on_change, restart, ask):
    """Tras elegir idioma: si el programa se rotula en vivo (on_change devuelve True) no hace falta
    más; si no, se ofrece reiniciar (restart) o se avisa de que se verá al volver a abrirlo."""
    if on_change and on_change(code):
        return
    if restart:
        if ask(L.t('common.language'), L.t('common.language_restart_q'), True):
            restart()
    else:
        ask(L.t('common.language'), L.t('common.language_next_time'), False)


def _tk_fill(sub, L, on_change, restart):
    """Rellena un tk.Menu con un idioma por linea (radiobuttons) y lo que pasa al elegir."""
    import tkinter as tk
    from tkinter import messagebox
    var = tk.StringVar(master=sub, value=L.code)

    def ask(title, text, yesno):
        if yesno:
            return messagebox.askyesno(title, text, parent=sub.winfo_toplevel())
        messagebox.showinfo(title, text, parent=sub.winfo_toplevel())
        return False

    def pick(code):
        if code == L.code:
            return
        L.set(code)
        var.set(L.code)
        _after_change(L, L.code, on_change, restart, ask)

    for code, name in L.available():
        sub.add_radiobutton(label=name, value=code, variable=var, command=lambda c=code: pick(c))
    sub._langkit_var = var       # que la variable no se pierda
    return sub


def tk_menu(menu, lang=None, on_change=None, restart=None, label=None):
    """Añade a un menú de tkinter el submenú «Idioma» con los idiomas disponibles.
    on_change(código) -> True si el programa ya se ha rotulado solo con el idioma nuevo.
    restart(): cerrar guardando y volver a abrir (langkit.restart_program() + root.destroy(), p.ej.)."""
    import tkinter as tk
    L = lang or get()
    sub = _tk_fill(tk.Menu(menu, tearoff=0), L, on_change, restart)
    menu.add_cascade(label=label or L.menu_label(), menu=sub)
    return sub


def tk_button(parent, lang=None, on_change=None, restart=None, label=None, **kw):
    """Para programas sin barra de menus: un boton «Idioma / Language» (ttk.Menubutton) que despliega
    los idiomas. Lo demas, como tk_menu. Devuelve el boton (sin colocar: .pack() o .grid())."""
    import tkinter as tk
    from tkinter import ttk
    L = lang or get()
    b = ttk.Menubutton(parent, text=label or L.menu_label(), **kw)
    b["menu"] = _tk_fill(tk.Menu(b, tearoff=0), L, on_change, restart)
    return b


def _qt():
    for mod in ('PySide6', 'PyQt6', 'PyQt5'):
        if mod in sys.modules:
            break
    else:
        mod = 'PySide6'
    QtGui = __import__(mod + '.QtGui', fromlist=['x'])
    QtWidgets = __import__(mod + '.QtWidgets', fromlist=['x'])
    return QtGui, QtWidgets


def qt_menu(menu, lang=None, on_change=None, restart=None, label=None, parent=None):
    """Lo mismo que tk_menu para Qt: `menu` es un QMenu o un QMenuBar. Devuelve el submenú."""
    QtGui, W = _qt()
    QAction = getattr(QtGui, 'QAction', None) or getattr(W, 'QAction')
    QActionGroup = getattr(QtGui, 'QActionGroup', None) or getattr(W, 'QActionGroup')
    L = lang or get()
    sub = menu.addMenu(label or L.menu_label())
    group = QActionGroup(sub)
    group.setExclusive(True)
    acts = {}

    def ask(title, text, yesno):
        B = W.QMessageBox.StandardButton if hasattr(W.QMessageBox, 'StandardButton') else W.QMessageBox
        if yesno:
            return W.QMessageBox.question(parent, title, text, B.Yes | B.No) == B.Yes
        W.QMessageBox.information(parent, title, text)
        return False

    def pick(code):
        if code == L.code:
            return
        L.set(code)
        for c, a in acts.items():
            a.setChecked(c == L.code)
        _after_change(L, L.code, on_change, restart, ask)

    for code, name in L.available():
        a = QAction(name, sub)
        a.setCheckable(True)
        a.setChecked(code == L.code)
        a.triggered.connect(lambda _=False, c=code: pick(c))
        group.addAction(a)
        sub.addAction(a)
        acts[code] = a
    sub._langkit_keep = (group, acts)
    return sub


if __name__ == '__main__':
    # python langkit.py [carpeta]: qué idiomas hay y cuál se elegiría
    L = Lang(sys.argv[1] if len(sys.argv) > 1 else os.getcwd())
    print('lang-kit %s | elegido: %s | disponibles: %s' % (LANGKIT_VERSION, L.code, ', '.join('%s (%s)' % a for a in L.available())))
    for e in L.errors:
        print('error:', e)
