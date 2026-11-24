# pyi_update.py
"""Actualizador comun de py-installer (solo biblioteca estandar).

py-installer lo instala junto al programa y lo publica en update.json, asi que se actualiza solo
igual que el resto de archivos. Lo que hace:

  * mira la version publicada (dl/update.json) y la compara con la instalada (pack o version);
  * descarga solo de la web del programa (ni siquiera sigue redirecciones a otros sitios),
    comprueba tamano y huella sha256 de cada archivo y que los .py compilan antes de tocar nada;
  * guarda una copia de seguridad de lo que cambia (se conservan las 5 ultimas) y permite volver
    atras; entiende tambien las copias del actualizador antiguo de cam-ui y widget-arena;
  * reinicia el programa;
  * recuerda las preferencias (actualizacion.json):
      auto          avisar al abrir si hay version nueva (por defecto, si)
      auto_apply    actualizar al abrir sin preguntar
      silent_watch  "vigilar sola, sin avisos": mirar cada rato mientras esta abierto, aplicar y
                    reiniciar sin decir nada (casilla oculta: se revela con un atajo secreto)

El motor no depende de ninguna interfaz. Para programas de tkinter hay una ventana y un
"cableado" ya hechos (tk_install), con todo lo anterior y el atajo Ctrl+Shift+Alt+F12.

Uso minimo:
    import pyi_update
    U = pyi_update.Updater(__file__, pack=APP_PACK)     # o version=APP_VERSION
    r = U.check()            # {'ok', 'state': newer|same|older|diverged, 'info', 'local'}
    if r['ok'] and r['state'] == 'newer':
        U.apply(r['info']); U.spawn_restart(); sys.exit()
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

PYI_UPDATE_VERSION = 2
MAX_FILE = 64 * 1048576
MAX_JSON = 262144
KEEP_BACKUPS = 5
SECRET_SHORTCUT = '<Control-Shift-Alt-F12>'


def _sha256_file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _vtuple(v):
    return tuple(int(x) if x.isdigit() else x for x in re.split(r'[.\-+]', str(v or '').strip()) if x != '')


def _safe_rel(name):
    n = str(name).replace('\\', '/')
    parts = n.split('/')
    return bool(n) and not n.startswith('/') and ':' not in n and all(p not in ('', '.', '..') for p in parts)


class UpdateError(ValueError):
    pass


class Updater:
    """Motor de actualizaciones de un programa instalado con py-installer.

    app_file     el .py principal (normalmente __file__)
    pack         numero de pack del programa (APP_PACK), si lo usa; si no, version
    version      version del programa (APP_VERSION); por defecto, el archivo VERSION de al lado
    base         URL de la carpeta dl/ publicada; por defecto, la de install_info.json
    state_path   donde guardar las preferencias (por defecto actualizacion.json junto al programa)
    backups_dir  carpeta de copias (por defecto backups_programa junto al programa)
    """

    def __init__(self, app_file, pack=None, version=None, base=None, state_path=None, backups_dir=None,
                 user_agent=None, log=None):
        self.app_file = os.path.abspath(app_file)
        self.dir = os.path.dirname(self.app_file)
        self.main = os.path.basename(self.app_file)
        self.pack = int(pack) if pack is not None else None
        self.info_file = self._read_install_info()
        if version is None:
            try:
                with open(os.path.join(self.dir, 'VERSION'), encoding='utf-8') as f:
                    version = f.read().strip()[:40]
            except OSError:
                version = self.info_file.get('version', '')
        self.version = str(version or '')
        if base is None and self.info_file.get('base_url'):
            base = self.info_file['base_url'].rstrip('/') + '/dl/'
        self.base = (base.rstrip('/') + '/') if base else ''
        self.state_path = state_path or os.path.join(self.dir, 'actualizacion.json')
        self.backups_dir = backups_dir or os.path.join(self.dir, 'backups_programa')
        self.user_agent = user_agent or (self.info_file.get('id') or os.path.splitext(self.main)[0])
        self.log = log
        self._st = None

    # ---- utilidades -------------------------------------------------------------------------
    def _read_install_info(self):
        try:
            with open(os.path.join(self.dir, 'install_info.json'), encoding='utf-8') as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _say(self, msg):
        if self.log:
            try:
                self.log(msg)
            except Exception:
                pass

    @property
    def enabled(self):
        """False si no se sabe de donde actualizar (p.ej. ejecutado desde el codigo fuente) o si
        es un ejecutable congelado."""
        return bool(self.base) and not getattr(sys, 'frozen', False)

    # ---- preferencias -----------------------------------------------------------------------
    def state(self):
        if self._st is None:
            try:
                with open(self.state_path, encoding='utf-8') as f:
                    d = json.load(f)
            except (OSError, ValueError):
                d = {}
            self._st = d if isinstance(d, dict) else {}
        return self._st

    def save_state(self):
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            tmp = self.state_path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.state(), f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.state_path)
        except OSError as e:
            self._say('actualizar: no se pudo guardar el estado: %s' % e)

    def set_auto(self, on):
        """Avisar al abrir si hay version nueva."""
        self.state()['auto'] = bool(on)
        self.save_state()

    def set_auto_apply(self, on):
        """Actualizar al abrir sin preguntar. Activarlo reactiva tambien el aviso al abrir (si no,
        nunca llegaria a mirar); apagarlo no toca el aviso."""
        st = self.state()
        st['auto_apply'] = bool(on)
        if on and st.get('auto', True) is False:
            st['auto'] = True
        self.save_state()

    def set_silent_watch(self, on):
        """Vigilar sola, sin avisos: mirar cada rato, aplicar y reiniciar sin decir nada."""
        self.state()['silent_watch'] = bool(on)
        self.save_state()

    def startup_check_due(self, min_interval=3600):
        """¿Toca mirar al abrir? (no si se pidio no avisar, ni si ya se miro hace poco)."""
        st = self.state()
        if not self.enabled or st.get('auto', True) is False:
            return False
        return time.time() - float(st.get('last_check', 0) or 0) >= min_interval

    def startup_action(self, r):
        """Que hacer con el resultado de check() al abrir: 'apply' (sin preguntar), 'ask' o None."""
        if not r.get('ok') or r.get('state') != 'newer':
            return None
        return 'apply' if self.state().get('auto_apply') else 'ask'

    # ---- red --------------------------------------------------------------------------------
    def _get(self, url, limit, timeout=20):
        import ssl
        import urllib.request
        base = self.base
        if not base or not url.startswith(base):
            raise UpdateError('direccion no permitida')

        class _Same(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                if not newurl.startswith(base):
                    raise UpdateError('redireccion a otro sitio rechazada')
                return super().redirect_request(req, fp, code, msg, headers, newurl)
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ctx = ssl.create_default_context()
        op = urllib.request.build_opener(_Same(), urllib.request.HTTPSHandler(context=ctx))
        req = urllib.request.Request(url, headers={'User-Agent': self.user_agent, 'Cache-Control': 'no-cache'})
        with op.open(req, timeout=timeout) as r:
            data = r.read(limit + 1)
        if len(data) > limit:
            raise UpdateError('la respuesta es demasiado grande')
        return data

    # ---- comprobar --------------------------------------------------------------------------
    def local_info(self):
        try:
            sha = _sha256_file(self.app_file)
        except OSError:
            sha = ''
        return {'pack': self.pack, 'version': self.version, 'label': self.version, 'sha256': sha,
                'file': self.app_file, 'frozen': bool(getattr(sys, 'frozen', False))}

    def _validate(self, info):
        files = info.get('files')
        if not isinstance(files, dict) or self.main not in files:
            raise UpdateError('update.json no incluye %s' % self.main)
        for name, meta in files.items():
            if not _safe_rel(name):
                raise UpdateError('update.json: nombre de archivo no permitido (%s)' % name)
            sha, size = str(meta.get('sha256', '')).lower(), int(meta.get('size', 0))
            if not re.fullmatch(r'[0-9a-f]{64}', sha) or size <= 0 or size > MAX_FILE:
                raise UpdateError('update.json no es valido (%s)' % name)
        if self.pack is not None and 'pack' in info:
            int(info['pack'])

    def check(self):
        """Mira la version publicada. Devuelve {ok, state: newer|same|older|diverged, info, local}
        o {ok: False, error}."""
        if not self.enabled:
            return {'ok': False, 'error': 'este programa no esta instalado con py-installer (no se sabe de donde actualizar)'}
        try:
            info = json.loads(self._get(self.base + 'update.json?t=%d' % int(time.time()), MAX_JSON).decode('utf-8'))
            self._validate(info)
        except Exception as e:
            return {'ok': False, 'error': str(e) or e.__class__.__name__}
        loc = self.local_info()
        if self.pack is not None and info.get('pack') is not None:
            a, b = int(info['pack']), self.pack
        else:
            a, b = _vtuple(info.get('version')), _vtuple(self.version)
        if a > b:
            st = 'newer'
        elif a < b:
            st = 'older'
        else:
            st = 'same' if str(info['files'][self.main]['sha256']).lower() == loc['sha256'] else 'diverged'
        self.state()['last_check'] = time.time()
        self.save_state()
        return {'ok': True, 'state': st, 'info': info, 'local': loc}

    # ---- aplicar ----------------------------------------------------------------------------
    def _download(self, name, meta):
        data = self._get(self.base + name + '?v=' + str(meta['sha256'])[:12], int(meta['size']))
        if len(data) != int(meta['size']) or hashlib.sha256(data).hexdigest() != str(meta['sha256']).lower():
            raise UpdateError('%s: lo descargado no coincide con la huella publicada (se cancela; si acaban de '
                              'publicar, intentalo de nuevo en unos minutos)' % name)
        if name.endswith('.py'):
            compile(data, name, 'exec')           # que al menos sea Python valido antes de tocar nada
        return data

    def _stamp(self):
        tag = ('pack%d' % self.pack) if self.pack is not None else ('v' + re.sub(r'[^0-9A-Za-z.]', '', self.version or '0'))
        base = os.path.join(self.backups_dir, '%s.%s.%s' % (os.path.splitext(self.main)[0], tag, time.strftime('%Y%m%d-%H%M%S')))
        p, i = base, 2
        while os.path.exists(p):
            p = '%s-%d' % (base, i)
            i += 1
        return p

    def _backup(self, names):
        """Copia de lo que se va a cambiar, en una carpeta propia. Devuelve su ruta."""
        bk = self._stamp()
        os.makedirs(bk)
        present, absent = [], []
        for n in names:
            src = os.path.join(self.dir, n)
            if os.path.isfile(src):
                dst = os.path.join(bk, n)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
                present.append(n)
            else:
                absent.append(n)
        with open(os.path.join(bk, '_copia.json'), 'w', encoding='utf-8') as f:
            json.dump({'files': present, 'absent': absent, 'pack': self.pack, 'version': self.version,
                       'made': time.time()}, f, indent=1)
        return bk

    @staticmethod
    def _put(path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.nuevo'
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, path)

    def _prune(self, keep_paths=()):
        try:
            prefix = os.path.splitext(self.main)[0] + '.'
            olds = sorted((os.path.join(self.backups_dir, f) for f in os.listdir(self.backups_dir)
                           if f.startswith(prefix) and os.path.isdir(os.path.join(self.backups_dir, f))),
                          key=lambda q: (os.path.getmtime(q), q in keep_paths))
            for p in olds[:-KEEP_BACKUPS]:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass

    def apply(self, info):
        """Descarga y comprueba la version publicada, guarda una copia de lo que cambia y lo sustituye.
        Devuelve {backup, files}. Si algo falla antes de escribir, no se toca nada."""
        if getattr(sys, 'frozen', False):
            raise UpdateError('Esta instalacion es un ejecutable: descarga el instalador nuevo desde la pagina del programa.')
        if not os.access(self.dir, os.W_OK) or not os.access(self.app_file, os.W_OK):
            raise UpdateError('No hay permiso para escribir en la carpeta del programa (%s).' % self.dir)
        self._validate(info)
        files = info['files']
        todo = {}
        for name, meta in files.items():
            dst = os.path.join(self.dir, name)
            if name == self.main or not os.path.isfile(dst) or _sha256_file(dst) != str(meta['sha256']).lower():
                todo[name] = self._download(name, meta)
        main = todo.get(self.main)
        if main is not None and self.pack is not None and info.get('pack') is not None:
            m = re.search(rb'^APP_PACK\s*=\s*(\d+)', main, re.M)
            if not m or int(m.group(1)) != int(info['pack']):
                raise UpdateError('El archivo publicado no es el de la version anunciada (se cancela).')
        names = sorted(todo) + (['VERSION'] if info.get('version') and os.path.isfile(os.path.join(self.dir, 'VERSION')) else [])
        bk = self._backup(names)
        # el principal, el ultimo: si algo falla a medias, el programa sigue arrancando con lo anterior
        for name in sorted(todo, key=lambda n: n == self.main):
            self._put(os.path.join(self.dir, name), todo[name])
        if 'VERSION' in names:
            try:
                with open(os.path.join(self.dir, 'VERSION'), 'w', encoding='utf-8') as f:
                    f.write(str(info['version']) + '\n')
            except OSError:
                pass
        st = self.state()
        st.update({'last_backup_set': bk, 'last_backup': os.path.join(bk, self.main), 'last_backup_tools': None,
                   'from_pack': self.pack, 'to_pack': info.get('pack'), 'from_version': self.version,
                   'to_version': info.get('version'), 'applied': time.time()})
        self.save_state()
        self._prune((bk,))
        self._say('actualizado: %s' % ', '.join(sorted(todo)))
        return {'backup': os.path.join(bk, self.main), 'backup_set': bk, 'files': sorted(todo)}

    # ---- volver atras -----------------------------------------------------------------------
    def backup_available(self):
        st = self.state()
        bs = st.get('last_backup_set')
        if bs:
            return os.path.isdir(bs) and os.path.abspath(os.path.dirname(bs)) == os.path.abspath(self.backups_dir)
        bk = st.get('last_backup')            # copia del actualizador antiguo (un archivo suelto)
        return bool(bk) and os.path.isfile(bk) and os.path.abspath(os.path.dirname(bk)) == os.path.abspath(self.backups_dir)

    def rollback(self):
        """Vuelve a la copia de la ultima actualizacion (y deja lo actual como copia, por si quieres volver)."""
        if getattr(sys, 'frozen', False):
            raise UpdateError('Esta instalacion es un ejecutable: no se puede cambiar la version desde aqui.')
        if not self.backup_available():
            raise UpdateError('No hay una copia anterior a la que volver.')
        st = self.state()
        bs = st.get('last_backup_set')
        if bs:
            with open(os.path.join(bs, '_copia.json'), encoding='utf-8') as f:
                meta = json.load(f)
            restore = {n: os.path.join(bs, n) for n in meta.get('files', []) if _safe_rel(n)}
            absent = [n for n in meta.get('absent', []) if _safe_rel(n)]
        else:                                  # formato antiguo: el principal y, en widget-arena, herramientas.py
            restore = {self.main: st['last_backup']}
            bt = st.get('last_backup_tools')
            if bt and os.path.isfile(bt):
                restore['herramientas.py'] = bt
            absent = []
        for n, p in restore.items():
            if n.endswith('.py'):
                with open(p, 'rb') as f:
                    compile(f.read(), n, 'exec')
        cur = self._backup(sorted(set(restore) | set(absent)))
        for n, p in restore.items():
            with open(p, 'rb') as f:
                self._put(os.path.join(self.dir, n), f.read())
        for n in absent:                       # archivos que la actualizacion anadio: se quitan
            try:
                os.remove(os.path.join(self.dir, n))
            except OSError:
                pass
        st.update({'last_backup_set': cur, 'last_backup': os.path.join(cur, self.main), 'last_backup_tools': None,
                   'applied': time.time()})
        self.save_state()
        self._prune((cur,))
        return {'backup': os.path.join(cur, self.main), 'backup_set': cur, 'files': sorted(restore)}

    # ---- reiniciar --------------------------------------------------------------------------
    def spawn_restart(self, delay=3, argv=None):
        """Lanza aparte una orden que vuelve a abrir el programa pasados unos segundos (da tiempo a que
        este termine de guardar y cerrarse). Despues, cierra el programa como siempre."""
        script = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] and os.path.exists(sys.argv[0]) else self.app_file
        cmd = [sys.executable, script] + list(sys.argv[1:] if argv is None else argv)
        flags = ', creationflags=0x08000008' if os.name == 'nt' else ', start_new_session=True'
        code = 'import subprocess, sys, time\ntime.sleep(%d)\nsubprocess.Popen(sys.argv[1:], close_fds=True%s)' % (int(delay), flags)
        kw = {'creationflags': 0x08000008} if os.name == 'nt' else {'start_new_session': True}
        subprocess.Popen([sys.executable, '-c', code] + cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, cwd=self.dir, **kw)
        return cmd


def run(fn, *a):
    """Ejecuta fn(*a) y devuelve {'ok': True, ...resultado} o {'ok': False, 'error'} (comodo en hilos)."""
    try:
        r = fn(*a)
        out = {'ok': True}
        if isinstance(r, dict):
            out.update(r)
        return out
    except Exception as e:
        return {'ok': False, 'error': str(e) or e.__class__.__name__}


# =============================================================================================
# Interfaz lista para programas de tkinter
# =============================================================================================
def _tk_colors(widget):
    """Colores del tema ttk del programa (para que las ventanas del actualizador vayan a juego)."""
    from tkinter import ttk
    st = ttk.Style(widget)
    bg = st.lookup('TFrame', 'background') or None
    fg = st.lookup('TLabel', 'foreground') or None
    field = st.lookup('TEntry', 'fieldbackground') or bg
    return bg, fg, field


def tk_install(root, updater, app_name, on_close=None, busy=None, startup_delay_ms=12000,
               watch_every_ms=10 * 60 * 1000, min_interval=3600):
    """Deja montado todo el sistema en un programa de tkinter:
      * al abrir (pasados unos segundos): mira si hay version nueva; pregunta, o aplica sola si se
        marco "actualizar sin preguntar";
      * mientras esta abierto: si esta activo "vigilar sola, sin avisos", mira cada rato, aplica y
        reinicia sin decir nada;
      * devuelve un objeto con .show() para abrir la ventana de actualizaciones (ponlo en un menu).
    on_close: funcion que cierra el programa guardando lo suyo (por defecto root.destroy).
    busy: funcion que devuelve True si ahora no conviene reiniciar (p.ej. grabando)."""
    return _TkUpdates(root, updater, app_name, on_close or root.destroy, busy or (lambda: False),
                      startup_delay_ms, watch_every_ms, min_interval)


class _TkUpdates:
    def __init__(self, root, u, name, on_close, busy, startup_delay_ms, watch_every_ms, min_interval):
        self.root, self.u, self.name, self.on_close, self.busy = root, u, name, on_close, busy
        self.watch_every_ms, self.min_interval = watch_every_ms, min_interval
        self.win = None
        # el atajo secreto funciona desde cualquier ventana del programa (no solo con la de
        # actualizaciones delante): la abre y muestra la casilla oculta. Win pulsada o no, da igual.
        try:
            root.bind_all(SECRET_SHORTCUT, self._secret, add='+')
        except Exception:
            pass
        if u.enabled:
            root.after(startup_delay_ms, self._startup)
            root.after(startup_delay_ms + 3000, self._watch_tick)

    # hilos: la red nunca bloquea la ventana
    def _bg(self, fn, done):
        import threading
        box = {}

        def work():
            box['r'] = fn()
        t = threading.Thread(target=work, daemon=True)
        t.start()

        def poll():
            if t.is_alive():
                self.root.after(150, poll)
            else:
                done(box.get('r') or {'ok': False, 'error': 'sin respuesta'})
        self.root.after(150, poll)

    def restart(self):
        try:
            self.u.spawn_restart()
        except Exception as e:
            from tkinter import messagebox
            messagebox.showwarning(self.name, 'La actualización está instalada, pero no se pudo reiniciar sola.\n'
                                   'Cierra y abre de nuevo %s.\n\n(%s)' % (self.name, e))
            return
        self.on_close()

    def _startup(self):
        if self.u.startup_check_due(self.min_interval) and not self.busy():
            self._bg(self.u.check, self._startup_checked)

    def _startup_checked(self, r):
        act = self.u.startup_action(r)
        if act == 'apply':
            self.show(check=False).apply_result(r)
        elif act == 'ask':
            go, dont_ask = self._ask(r['info'])
            if dont_ask:
                self.u.set_auto(False)
            if go:
                self.show(check=False).apply_result(r)

    def _watch_tick(self):
        try:
            if self.u.state().get('silent_watch') and not self.busy():
                self._bg(self.u.check, self._watch_checked)
        finally:
            self.root.after(self.watch_every_ms, self._watch_tick)

    def _watch_checked(self, r):
        if not r.get('ok') or r.get('state') != 'newer' or not self.u.state().get('silent_watch') or self.busy():
            return
        self._bg(lambda: run(self.u.apply, r['info']), lambda res: res.get('ok') and self.restart())

    def _ask(self, info):
        import tkinter as tk
        from tkinter import ttk
        res = {'go': False, 'dont': False}
        top = tk.Toplevel(self.root)
        top.title(self.name)
        bg, fg, field = _tk_colors(top)
        if bg:
            top.configure(bg=bg)
        top.transient(self.root)
        top.resizable(False, False)
        f = ttk.Frame(top, padding=14)
        f.pack(fill='both', expand=True)
        v = info.get('version') or ''
        ttk.Label(f, text='Hay una versión nueva de %s%s.' % (self.name, (' (' + v + ')') if v else ''),
                  font=('', 10, 'bold')).pack(anchor='w')
        ttk.Label(f, text='Se guarda una copia de la actual por si quieres volver atrás.', wraplength=360).pack(anchor='w', pady=(4, 8))
        dont = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text='No preguntar más al abrir', variable=dont).pack(anchor='w')
        b = ttk.Frame(f)
        b.pack(fill='x', pady=(10, 0))

        def close(go):
            res.update(go=go, dont=bool(dont.get()))
            top.destroy()
        ttk.Button(b, text='Ver novedades', command=lambda: (close(False), self.show(check=False).apply_result({'ok': True, 'state': 'newer', 'info': info, 'local': self.u.local_info()}, ask=True))).pack(side='left')
        ttk.Button(b, text='Ahora no', command=lambda: close(False)).pack(side='right')
        ttk.Button(b, text='Actualizar', command=lambda: close(True)).pack(side='right', padx=(0, 6))
        top.protocol('WM_DELETE_WINDOW', lambda: close(False))
        top.grab_set()
        self.root.wait_window(top)
        return res['go'], res['dont']

    def _secret(self, _e=None):
        w = self.show(check=False)
        w._reveal()
        return 'break'

    def show(self, check=True):
        if self.win is not None and self.win.alive():
            self.win.lift()
        else:
            self.win = _TkUpdateWindow(self)
        if check:
            self.win.start_check()
        return self.win


class _TkUpdateWindow:
    def __init__(self, ctl):
        import tkinter as tk
        from tkinter import ttk
        self.ctl, self.u = ctl, ctl.u
        self.info = None
        top = self.top = tk.Toplevel(ctl.root)
        top.title('Actualizaciones - ' + ctl.name)
        bg, fg, field = _tk_colors(top)
        if bg:
            top.configure(bg=bg)
        top.minsize(460, 380)
        f = ttk.Frame(top, padding=14)
        f.pack(fill='both', expand=True)
        loc = self.u.local_info()
        cur = loc['version'] + ((' (pack %s)' % loc['pack']) if loc['pack'] is not None else '')
        ttk.Label(f, text='Versión instalada: ' + (cur or '?'), font=('', 10, 'bold')).pack(anchor='w')
        self.status = ttk.Label(f, text='', wraplength=420)
        self.status.pack(anchor='w', pady=(4, 6))
        self.notes = tk.Text(f, height=10, wrap='word', relief='flat', borderwidth=0, padx=8, pady=6)
        if field and fg:
            self.notes.configure(bg=field, fg=fg, insertbackground=fg)
        self.notes.pack(fill='both', expand=True)
        self.notes.configure(state='disabled')
        st = self.u.state()
        self.v_auto = tk.BooleanVar(value=st.get('auto', True) is not False)
        self.v_apply = tk.BooleanVar(value=bool(st.get('auto_apply')))
        self.v_watch = tk.BooleanVar(value=bool(st.get('silent_watch')))
        ttk.Checkbutton(f, text='Avisarme al abrir si hay una versión nueva', variable=self.v_auto,
                        command=lambda: self.u.set_auto(self.v_auto.get())).pack(anchor='w', pady=(8, 0))
        ttk.Checkbutton(f, text='Actualizar automáticamente al abrir, sin preguntar', variable=self.v_apply,
                        command=self._apply_toggled).pack(anchor='w')
        self.chk_watch = ttk.Checkbutton(f, text='Vigilar sola, sin avisos (instala y reinicia sola)', variable=self.v_watch,
                                         command=self._watch_toggled)
        if self.v_watch.get():
            self.chk_watch.pack(anchor='w')
        b = self.buttons = ttk.Frame(f)
        b.pack(fill='x', pady=(10, 0))
        self.b_check = ttk.Button(b, text='Buscar', command=self.start_check)
        self.b_check.pack(side='left')
        self.b_back = ttk.Button(b, text='Volver a la anterior', command=self.rollback)
        self.b_back.pack(side='left', padx=6)
        if not self.u.backup_available():
            self.b_back.state(['disabled'])
        ttk.Button(b, text='Cerrar', command=top.destroy).pack(side='right')
        self.b_go = ttk.Button(b, text='Actualizar', command=self.apply)
        self.b_go.pack(side='right', padx=6)
        self.b_go.state(['disabled'])
        if not self.u.enabled:
            self._set('Este programa no está instalado con su instalador: no puede actualizarse solo.')
            self.b_check.state(['disabled'])

    def alive(self):
        try:
            return bool(self.top.winfo_exists())
        except Exception:
            return False

    def lift(self):
        self.top.deiconify()
        self.top.lift()

    def _set(self, text, notes=None):
        self.status.configure(text=text)
        if notes is not None:
            self.notes.configure(state='normal')
            self.notes.delete('1.0', 'end')
            self.notes.insert('1.0', notes)
            self.notes.configure(state='disabled')

    def _apply_toggled(self):
        self.u.set_auto_apply(self.v_apply.get())
        if self.v_apply.get():
            self.v_auto.set(True)

    def _reveal(self, _e=None):
        if not self.chk_watch.winfo_ismapped():
            self.chk_watch.pack(anchor='w', before=self.buttons)
        self.lift()
        try:
            self.chk_watch.focus_set()
        except Exception:
            pass

    def _watch_toggled(self):
        on = bool(self.v_watch.get())
        self.u.set_silent_watch(on)
        if not on:
            self.chk_watch.pack_forget()

    def start_check(self):
        self._set('Buscando…')
        self.b_check.state(['disabled'])
        self.ctl._bg(self.u.check, lambda r: self.apply_result(r, ask=True))   # buscar a mano = revisar antes

    def apply_result(self, r, ask=False):
        """Muestra el resultado de check(). Si es una version nueva y no se pide revisar (ask), aplica."""
        if not self.alive():
            return
        self.b_check.state(['!disabled'])
        if not r.get('ok'):
            self._set('No se pudo comprobar: %s' % r.get('error'))
            return
        self.info = r['info']
        v = self.info.get('version') or ''
        txt = {'newer': 'Hay una versión nueva: %s.' % v, 'same': 'Tienes la última versión.',
               'older': 'Tu versión es más nueva que la publicada.',
               'diverged': 'Tu copia no coincide con la publicada (puedes reinstalarla).'}[r['state']]
        self._set(txt, self.info.get('notes') or '')
        if r['state'] in ('newer', 'diverged'):
            self.b_go.state(['!disabled'])
        if r['state'] == 'newer' and not ask:
            self.apply()

    def apply(self):
        if not self.info:
            return
        self._set('Descargando y comprobando…')
        self.b_go.state(['disabled'])
        self.ctl._bg(lambda: run(self.u.apply, self.info), self._applied)

    def _applied(self, res):
        if not self.alive():
            return
        if not res.get('ok'):
            self._set('No se pudo actualizar: %s' % res.get('error'))
            self.b_go.state(['!disabled'])
            return
        self._set('Actualizado. Reiniciando…')
        self.top.after(800, self.ctl.restart)

    def rollback(self):
        from tkinter import messagebox
        if not messagebox.askyesno(self.ctl.name, '¿Volver a la versión anterior y reiniciar?', parent=self.top):
            return
        res = run(self.u.rollback)
        if not res.get('ok'):
            self._set('No se pudo volver atrás: %s' % res.get('error'))
            return
        self._set('Hecho. Reiniciando…')
        self.top.after(800, self.ctl.restart)


if __name__ == '__main__':
    # uso por terminal:  python pyi_update.py <programa.py> [check|apply|rollback]
    if len(sys.argv) < 2:
        sys.exit('uso: python pyi_update.py <programa.py> [check|apply|rollback]')
    U = Updater(sys.argv[1])
    cmd = sys.argv[2] if len(sys.argv) > 2 else 'check'
    r = U.check()
    if cmd == 'check' or not r.get('ok'):
        print(json.dumps({k: v for k, v in r.items() if k != 'info'}, indent=1, ensure_ascii=False))
    elif cmd == 'apply':
        print(run(U.apply, r['info']) if r['state'] in ('newer', 'diverged') else 'nada que actualizar')
    elif cmd == 'rollback':
        print(run(U.rollback))
