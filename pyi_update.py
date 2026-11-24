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

El motor no depende de ninguna interfaz. Encima hay una sola ventana de Actualizaciones y un solo
comportamiento para todos los programas, en dos versiones: tkinter (tk_install) y Qt (qt_install).
El atajo secreto Ctrl+Shift+Alt+F12 lo recibe siempre el actualizador, venga de la ventana que venga.
El actualizador tiene su propia version (PYI_VERSION), aparte de la del programa.

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

# version de py-installer (instalador + este actualizador). Va aparte de la del programa: sale en la
# ventana de Actualizaciones y en update.json ("updater"), para saber que actualizador tiene cada uno.
PYI_VERSION = '1.3.0'
PYI_UPDATE_VERSION = 3                      # (antiguo: numero entero del actualizador)
MAX_FILE = 64 * 1048576
MAX_JSON = 262144
KEEP_BACKUPS = 5
# el atajo secreto lo recibe SIEMPRE el actualizador (no cada programa): desde cualquier ventana del
# programa abre Actualizaciones con la casilla oculta "Vigilar sola, sin avisos" a la vista.
# Con o sin la tecla Win (Cmd en Mac), da igual.
SECRET_KEYS = 'Ctrl+Shift+Alt+F12'
SECRET_SHORTCUT = '<Control-Shift-Alt-F12>'        # el mismo, escrito para tkinter


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
            d = d if isinstance(d, dict) else {}
            # nombres antiguos (widget-arena): watch -> silent_watch, auto_install -> auto_apply
            for old, new in (('watch', 'silent_watch'), ('auto_install', 'auto_apply')):
                if old in d and new not in d:
                    d[new] = bool(d[old])
            self._st = d
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
        st = self.state()
        st['silent_watch'] = bool(on)
        st.pop('auto_try', None)
        self.save_state()

    def label(self, info=None):
        """Nombre corto de una version: 'pack 54', '0.1.9'... (la instalada si no se da info)."""
        if info is None:
            return ('pack %s' % self.pack) if self.pack is not None else (self.version or '?')
        if self.pack is not None and info.get('pack') is not None:
            return 'pack %s' % info['pack']
        return str(info.get('version') or '?')

    def auto_try_ok(self, info):
        """Instalar sin preguntar se intenta una sola vez por version publicada: si falla, o si tras
        reiniciar sigue pareciendo nueva, no se repite (evita bucles de reinicios)."""
        key = self.label(info)
        st = self.state()
        if st.get('auto_try') == key:
            return False
        st['auto_try'] = key
        self.save_state()
        return True

    def mark_auto_notice(self, info):
        """Tras instalar sola, al volver a abrir se avisa de a que version se ha actualizado."""
        self.state()['auto_notice'] = self.label(info)
        self.save_state()

    def pop_auto_notice(self):
        st = self.state()
        n = st.pop('auto_notice', None)
        if n is not None:
            self.save_state()
        return n if n == self.label() else None

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
# Lo que ve el usuario: la misma ventana y el mismo comportamiento en todos los programas.
#   * _Controller: la logica (al abrir, vigilar, instalar sola, reiniciar cuando se pueda, atajo
#     secreto). No depende de ninguna libreria de ventanas.
#   * _Window: la ventana de Actualizaciones (logica), con dos caras: tkinter y Qt.
#   * tk_install(...) / qt_install(...): lo montan en un programa con una sola llamada.
# =============================================================================================
TXT = {
    'title': 'Actualizaciones',
    'yours': 'Tu versión:  %s',
    'published': 'Publicada:  %s',
    'notes': 'Novedades',
    'auto': 'Avisarme al abrir el programa si hay una versión nueva',
    'auto_apply': 'Actualizar automáticamente al abrir, sin preguntar (se guarda una copia por si quieres volver)',
    'watch': 'Vigilar sola, sin avisos: actualizar en cuanto se publique una versión nueva',
    'watch_help': 'Mientras el programa está abierto mira cada rato; si hay versión nueva la instala y se '
                  'reinicia sola cuando no estés haciendo nada. Esta casilla se ve con ' + SECRET_KEYS + '.',
    'check': 'Buscar de nuevo',
    'back': 'Volver a la versión anterior',
    'go': 'Actualizar y reiniciar',
    'go_diverged': 'Sustituir por la publicada',
    'close': 'Cerrar',
    'footer': 'Actualizador py-installer %s' % PYI_VERSION,
}


def _state_text(r, u):
    v = u.label(r.get('info') or {})
    return {'newer': 'Hay una versión nueva: %s.' % v,
            'same': 'Tienes la última versión.',
            'older': 'Tu versión es más nueva que la publicada.',
            'diverged': 'Tu copia no coincide con la publicada (puedes sustituirla por la publicada).'}[r['state']]


class _Controller:
    """ui: adaptador de la libreria de ventanas con after(ms, fn), bg(fn, done), ask(info, name) ->
    (ir, no_preguntar_mas), window() -> _Window, warn(texto), ui_busy() -> bool."""

    def __init__(self, ui, u, name, on_close, busy, notify, startup_delay_ms, watch_every_ms, min_interval):
        self.ui, self.u, self.name = ui, u, name
        self.on_close, self.busy, self.notify = on_close, busy or (lambda: False), notify or (lambda t: None)
        self.watch_every_ms, self.min_interval = watch_every_ms, min_interval
        self.win = None
        self._probing = False
        self._restarting = False
        if u.enabled:
            ui.after(startup_delay_ms, self._startup)
            ui.after(startup_delay_ms + 3000, self._watch_tick)

    # ---- abrir la ventana -------------------------------------------------------------------
    def show(self, check=True):
        if self.win is not None and self.win.alive():
            self.win.lift()
        else:
            self.win = self.ui.window()
        if check:
            self.win.start_check()
        return self.win

    def secret(self, *_):
        """El atajo secreto: abre Actualizaciones con la casilla oculta a la vista."""
        self.show(check=False).reveal()
        return 'break'

    # ---- al abrir ---------------------------------------------------------------------------
    def _startup(self):
        n = self.u.pop_auto_notice()
        if n:
            self.notify('%s se ha actualizado solo a %s' % (self.name, n))
        st = self.u.state()
        if (self.u.startup_check_due(self.min_interval) or st.get('silent_watch')) and not self.busy():
            self._probe(startup=True)

    def _watch_tick(self):
        try:
            if self.u.state().get('silent_watch') and not self.busy():
                self._probe(startup=False)
        finally:
            self.ui.after(self.watch_every_ms, self._watch_tick)

    def _probe(self, startup):
        if self._probing or self._restarting:
            return
        self._probing = True
        self.ui.bg(self.u.check, lambda r: self._probed(r, startup))

    def _probed(self, r, startup):
        self._probing = False
        if not r.get('ok') or r.get('state') != 'newer' or self._restarting:
            return
        st = self.u.state()
        if st.get('silent_watch') or (startup and st.get('auto_apply')):
            self.auto_install(r['info'])
        elif startup and st.get('auto', True) is not False:
            go, dont = self.ui.ask(r['info'], self.name)
            if dont:
                self.u.set_auto(False)
            if go == 'go':
                self.show(check=False).apply_result(r)
            elif go == 'notes':
                self.show(check=False).apply_result(r, ask=True)

    # ---- instalar sola ----------------------------------------------------------------------
    def auto_install(self, info):
        if not self.u.auto_try_ok(info):
            return
        self.notify('Instalando la versión nueva de %s (%s)…' % (self.name, self.u.label(info)))
        self.ui.bg(lambda: run(self.u.apply, info), lambda res: self._auto_done(res, info))

    def _auto_done(self, res, info):
        if not res.get('ok'):
            self.u._say('actualizar: la actualizacion automatica fallo: %s' % res.get('error'))
            self.notify('No se pudo actualizar solo: %s' % res.get('error'))
            return
        self.u.mark_auto_notice(info)
        self.restart_when_idle()

    # ---- reiniciar --------------------------------------------------------------------------
    def restart_when_idle(self, tries=0):
        """No reinicia mientras el programa este ocupado (busy, p.ej. grabando: espera lo que haga falta)
        ni en mitad de un arrastre, con un menu o un dialogo abierto (espera hasta 2 minutos)."""
        self._restarting = True
        if self.busy():
            self.ui.after(10000, lambda: self.restart_when_idle(tries))
            return
        if self.ui.ui_busy() and tries < 24:
            self.ui.after(5000, lambda: self.restart_when_idle(tries + 1))
            return
        self.restart()

    def restart(self):
        try:
            self.u.spawn_restart()
        except Exception as e:
            self._restarting = False
            self.ui.warn('La actualización está instalada, pero no se pudo reiniciar sola.\n'
                         'Cierra y abre de nuevo %s.\n\n(%s)' % (self.name, e))
            return
        self.on_close()


class _Window:
    """La ventana de Actualizaciones, sin la libreria de ventanas (la ponen _TkWindow / _QtWindow con
    _w_status, _w_remote, _w_notes, _w_enable, _w_go_text, _w_show_watch, _w_confirm, alive, lift, later)."""

    def _init_logic(self, ctl):
        self.ctl, self.u = ctl, ctl.u
        self.info = None
        self.state = None

    def local_text(self):
        loc = self.u.local_info()
        t = self.u.label()
        if self.u.pack is not None and loc.get('version'):
            t += '  ·  ' + loc['version']
        return TXT['yours'] % t

    # casillas
    def toggled_auto(self, on):
        self.u.set_auto(on)

    def toggled_apply(self, on):
        self.u.set_auto_apply(on)
        if on:
            self._w_set_check('auto', True)

    def toggled_watch(self, on):
        self.u.set_silent_watch(on)
        if not on:
            self._w_show_watch(False)

    def reveal(self):
        self._w_show_watch(True)
        self.lift()

    # buscar / aplicar / volver
    def start_check(self):
        if not self.u.enabled:
            return
        self._w_status('Buscando la versión publicada…')
        self._w_enable('check', False)
        self.ctl.ui.bg(self.u.check, lambda r: self.apply_result(r, ask=True))   # buscar a mano = revisar antes

    def apply_result(self, r, ask=False):
        """Muestra el resultado de check(). Si hay version nueva y no se pide revisar (ask), la instala."""
        if not self.alive():
            return
        self._w_enable('check', True)
        if not r.get('ok'):
            self._w_status('No se pudo comprobar: %s\nRevisa la conexión e inténtalo de nuevo.' % r.get('error'))
            return
        self.info, self.state = r['info'], r['state']
        self._w_remote(TXT['published'] % self.u.label(self.info))
        self._w_status(_state_text(r, self.u))
        self._w_notes(self.info.get('notes') or '')
        self._w_go_text(TXT['go_diverged'] if self.state == 'diverged' else TXT['go'])
        self._w_enable('go', self.state in ('newer', 'diverged'))
        if self.state == 'newer' and not ask:
            self.apply()

    def apply(self):
        if not self.info:
            return
        self._w_status('Descargando y comprobando…')
        self._w_enable('go', False)
        self._w_enable('check', False)
        self.ctl.ui.bg(lambda: run(self.u.apply, self.info), self._applied)

    def _applied(self, res):
        if not self.alive():
            if res.get('ok'):
                self.ctl.restart_when_idle()
            return
        self._w_enable('check', True)
        if not res.get('ok'):
            self._w_status('No se pudo completar: %s\nNo se ha cambiado nada.' % res.get('error'))
            self._w_enable('go', True)
            return
        self._w_enable('back', True)
        if self.ctl.busy():
            self._w_status('Instalado. %s se reiniciará en cuanto termine lo que está haciendo.' % self.ctl.name)
        else:
            self._w_status('Listo. El programa se reinicia en unos segundos…')
        self.later(800, self.ctl.restart_when_idle)

    def rollback(self):
        if not self._w_confirm('¿Volver a la versión anterior y reiniciar %s?' % self.ctl.name):
            return
        self._w_status('Restaurando…')
        self.ctl.ui.bg(lambda: run(self.u.rollback), self._rolled)

    def _rolled(self, res):
        if not res.get('ok'):
            if self.alive():
                self._w_status('No se pudo volver atrás: %s' % res.get('error'))
            return
        if self.alive():
            self._w_status('Hecho. El programa se reinicia en unos segundos…')
        self.later(800, self.ctl.restart_when_idle)


# ---- tkinter -------------------------------------------------------------------------------
def _tk_colors(widget):
    """Colores del tema ttk del programa (para que las ventanas del actualizador vayan a juego)."""
    from tkinter import ttk
    st = ttk.Style(widget)
    bg = st.lookup('TFrame', 'background') or None
    fg = st.lookup('TLabel', 'foreground') or None
    field = st.lookup('TEntry', 'fieldbackground') or bg
    return bg, fg, field


def tk_install(root, updater, app_name, on_close=None, busy=None, startup_delay_ms=12000,
               watch_every_ms=10 * 60 * 1000, min_interval=3600, notify=None):
    """Deja montado todo en un programa de tkinter y devuelve el controlador (.show() abre la
    ventana: ponlo en un menu). Hace, igual que en todos los programas:
      * al abrir (pasados unos segundos): mira si hay version nueva; pregunta, o la instala sola si
        se marco "actualizar sin preguntar";
      * con "vigilar sola, sin avisos": mira cada rato, instala y reinicia sin decir nada;
      * el atajo secreto Ctrl+Shift+Alt+F12 (con o sin Win), desde cualquier ventana del programa.
    on_close: cierra el programa guardando lo suyo (por defecto root.destroy).
    busy: devuelve True si ahora no conviene reiniciar (p.ej. grabando).
    notify: muestra un aviso breve (p.ej. en la barra de estado); opcional."""
    ui = _TkUI(root, app_name)
    ctl = _Controller(ui, updater, app_name, on_close or root.destroy, busy, notify,
                      startup_delay_ms, watch_every_ms, min_interval)
    ui.ctl = ctl
    try:
        root.bind_all(SECRET_SHORTCUT, ctl.secret, add='+')
    except Exception:
        pass
    return ctl


class _TkUI:
    def __init__(self, root, name):
        self.root, self.name, self.ctl = root, name, None

    def after(self, ms, fn):
        self.root.after(ms, fn)

    def bg(self, fn, done):
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

    def ui_busy(self):
        try:
            return bool(self.root.grab_current())
        except Exception:
            return False

    def warn(self, text):
        from tkinter import messagebox
        messagebox.showwarning(self.name, text)

    def window(self):
        return _TkWindow(self.ctl)

    def ask(self, info, name):
        import tkinter as tk
        from tkinter import ttk
        res = {'go': None}
        top = tk.Toplevel(self.root)
        top.title(name)
        bg, fg, field = _tk_colors(top)
        if bg:
            top.configure(bg=bg)
        top.transient(self.root)
        top.resizable(False, False)
        f = ttk.Frame(top, padding=14)
        f.pack(fill='both', expand=True)
        ttk.Label(f, text='Hay una versión nueva de %s (%s).' % (name, self.ctl.u.label(info)),
                  font=('', 10, 'bold')).pack(anchor='w')
        ttk.Label(f, text='¿Descargarla e instalarla ahora? Se guarda una copia de la versión actual y el '
                          'programa se reinicia; tus datos no se tocan.', wraplength=380).pack(anchor='w', pady=(4, 8))
        dont = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text='No preguntar más al abrir (prefiero actualizar a mano)', variable=dont).pack(anchor='w')
        b = ttk.Frame(f)
        b.pack(fill='x', pady=(10, 0))

        def close(go):
            res['go'] = go
            top.destroy()
        ttk.Button(b, text='Ver novedades', command=lambda: close('notes')).pack(side='left')
        ttk.Button(b, text='Ahora no', command=lambda: close(None)).pack(side='right')
        ttk.Button(b, text='Actualizar', command=lambda: close('go')).pack(side='right', padx=(0, 6))
        top.protocol('WM_DELETE_WINDOW', lambda: close(None))
        top.grab_set()
        self.root.wait_window(top)
        return res['go'], bool(dont.get())


class _TkWindow(_Window):
    def __init__(self, ctl):
        import tkinter as tk
        from tkinter import ttk
        self._init_logic(ctl)
        u = self.u
        top = self.top = tk.Toplevel(ctl.ui.root)
        top.title('%s - %s' % (TXT['title'], ctl.name))
        bg, fg, field = _tk_colors(top)
        if bg:
            top.configure(bg=bg)
        top.minsize(520, 440)
        f = ttk.Frame(top, padding=14)
        f.pack(fill='both', expand=True)
        # botones abajo (se colocan antes para que nunca se queden fuera al encoger)
        foot = ttk.Label(f, text=TXT['footer'], font=('', 8))
        foot.pack(side='bottom', anchor='e', pady=(6, 0))
        if fg:
            foot.configure(foreground='#888888')
        b = self.buttons = ttk.Frame(f)
        b.pack(side='bottom', fill='x', pady=(10, 0))
        self.b_check = ttk.Button(b, text=TXT['check'], command=self.start_check)
        self.b_check.pack(side='left')
        self.b_back = ttk.Button(b, text=TXT['back'], command=self.rollback)
        self.b_back.pack(side='left', padx=6)
        ttk.Button(b, text=TXT['close'], command=top.destroy).pack(side='right')
        self.b_go = ttk.Button(b, text=TXT['go'], command=self.apply)
        self.b_go.pack(side='right', padx=6)
        ttk.Label(f, text=TXT['title'], font=('', 13, 'bold')).pack(anchor='w')
        ttk.Label(f, text=self.local_text()).pack(anchor='w', pady=(4, 0))
        self.l_remote = ttk.Label(f, text=TXT['published'] % '—')
        self.l_remote.pack(anchor='w')
        self.status = ttk.Label(f, text='', wraplength=480, justify='left')
        self.status.pack(anchor='w', pady=(6, 6))
        ttk.Label(f, text=TXT['notes']).pack(anchor='w')
        nf = ttk.Frame(f)
        nf.pack(fill='both', expand=True, pady=(2, 6))
        self.notes = tk.Text(nf, height=8, wrap='word', relief='flat', borderwidth=0, padx=8, pady=6)
        if field and fg:
            self.notes.configure(bg=field, fg=fg, insertbackground=fg)
        sb = ttk.Scrollbar(nf, orient='vertical', command=self.notes.yview)
        self.notes.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.notes.pack(side='left', fill='both', expand=True)
        self.notes.configure(state='disabled')
        st = u.state()
        self.v = {'auto': tk.BooleanVar(value=st.get('auto', True) is not False),
                  'apply': tk.BooleanVar(value=bool(st.get('auto_apply'))),
                  'watch': tk.BooleanVar(value=bool(st.get('silent_watch')))}
        ttk.Checkbutton(f, text=TXT['auto'], variable=self.v['auto'],
                        command=lambda: self.toggled_auto(self.v['auto'].get())).pack(anchor='w')
        ttk.Checkbutton(f, text=TXT['auto_apply'], variable=self.v['apply'],
                        command=lambda: self.toggled_apply(self.v['apply'].get())).pack(anchor='w')
        self.watch_box = ttk.Frame(f)
        ttk.Checkbutton(self.watch_box, text=TXT['watch'], variable=self.v['watch'],
                        command=lambda: self.toggled_watch(self.v['watch'].get())).pack(anchor='w')
        ttk.Label(self.watch_box, text=TXT['watch_help'], wraplength=480, justify='left',
                  font=('', 8)).pack(anchor='w', padx=(22, 0))
        self._w_show_watch(self.v['watch'].get())
        self._w_enable('go', False)
        self._w_enable('back', u.backup_available())
        top.bind('<Escape>', lambda e: top.destroy())
        if not u.enabled:
            self._w_status('Este programa no está instalado con su instalador: no puede actualizarse solo.')
            self._w_enable('check', False)

    def alive(self):
        try:
            return bool(self.top.winfo_exists())
        except Exception:
            return False

    def lift(self):
        self.top.deiconify()
        self.top.lift()
        try:
            self.top.focus_force()
        except Exception:
            pass

    def later(self, ms, fn):
        self.ctl.ui.root.after(ms, fn)

    def watch_visible(self):
        return bool(self.watch_box.winfo_ismapped())

    def _w_status(self, text):
        self.status.configure(text=text)

    def _w_remote(self, text):
        self.l_remote.configure(text=text)

    def _w_notes(self, text):
        self.notes.configure(state='normal')
        self.notes.delete('1.0', 'end')
        self.notes.insert('1.0', text)
        self.notes.configure(state='disabled')

    def _w_enable(self, which, on):
        {'check': self.b_check, 'go': self.b_go, 'back': self.b_back}[which].state(['!disabled' if on else 'disabled'])

    def _w_go_text(self, text):
        self.b_go.configure(text=text)

    def _w_set_check(self, which, on):
        self.v[which].set(on)

    def _w_show_watch(self, on):
        if on and not self.watch_box.winfo_ismapped():
            self.watch_box.pack(anchor='w', fill='x', pady=(2, 0))
        elif not on:
            self.watch_box.pack_forget()

    def _w_confirm(self, text):
        from tkinter import messagebox
        return messagebox.askyesno(self.ctl.name, text, parent=self.top)


# ---- Qt (PySide6, o PyQt6/PyQt5) ------------------------------------------------------------
def _qt():
    for mod in ('PySide6', 'PyQt6', 'PyQt5'):
        if mod in sys.modules:
            break
    else:
        mod = 'PySide6'
    import importlib
    return (importlib.import_module(mod + '.QtCore'), importlib.import_module(mod + '.QtWidgets'))


def qt_install(app_name, updater, on_close, parent=None, busy=None, notify=None, startup_delay_ms=12000,
               watch_every_ms=10 * 60 * 1000, min_interval=3600):
    """Lo mismo que tk_install, para programas de Qt. Llamalo con la QApplication ya creada.
    on_close: cierra el programa guardando lo suyo. parent: ventana de la que cuelgan los dialogos."""
    QtCore, QtWidgets = _qt()
    ui = _QtUI(app_name, parent)
    ctl = _Controller(ui, updater, app_name, on_close, busy, notify, startup_delay_ms, watch_every_ms, min_interval)
    ui.ctl = ctl
    ui.install_secret(ctl.secret)
    return ctl


class _QtUI:
    def __init__(self, name, parent):
        self.QtCore, self.QtWidgets = _qt()
        self.name, self.parent, self.ctl = name, parent, None
        self._keep = []

    def after(self, ms, fn):
        self.QtCore.QTimer.singleShot(int(ms), fn)

    def bg(self, fn, done):
        import threading
        box = {}

        def work():
            box['r'] = fn()
        t = threading.Thread(target=work, daemon=True)
        t.start()

        def poll():
            if t.is_alive():
                self.after(150, poll)
            else:
                done(box.get('r') or {'ok': False, 'error': 'sin respuesta'})
        self.after(150, poll)

    def ui_busy(self):
        W, C = self.QtWidgets, self.QtCore
        app = W.QApplication
        return (app.mouseButtons() != C.Qt.MouseButton.NoButton or app.activeModalWidget() is not None
                or app.activePopupWidget() is not None)

    def warn(self, text):
        self.QtWidgets.QMessageBox.warning(self.parent, self.name, text)

    def window(self):
        return _QtWindow(self.ctl)

    def install_secret(self, fn):
        """Filtro de teclas de toda la aplicacion: el atajo llega venga de la ventana que venga."""
        C = self.QtCore
        K, M = C.Qt.Key, C.Qt.KeyboardModifier
        need = M.ControlModifier | M.ShiftModifier | M.AltModifier

        class _Secret(C.QObject):
            def eventFilter(self, obj, ev):
                try:
                    if ev.type() == C.QEvent.Type.KeyPress and ev.key() == K.Key_F12 and \
                            (ev.modifiers() & need) == need and not ev.isAutoRepeat():
                        C.QTimer.singleShot(0, fn)
                        return True
                except Exception:
                    pass
                return False
        f = _Secret()
        self._keep.append(f)
        self.QtWidgets.QApplication.instance().installEventFilter(f)

    def ask(self, info, name):
        W = self.QtWidgets
        box = W.QMessageBox(W.QMessageBox.Icon.Question, name,
                            'Hay una versión nueva de %s (%s).\n\n¿Descargarla e instalarla ahora? Se guarda una '
                            'copia de la versión actual y el programa se reinicia; tus datos no se tocan.'
                            % (name, self.ctl.u.label(info)), parent=self.parent)
        chk = W.QCheckBox('No preguntar más al abrir (prefiero actualizar a mano)')
        box.setCheckBox(chk)
        go = box.addButton('Actualizar', W.QMessageBox.ButtonRole.AcceptRole)
        notes = box.addButton('Ver novedades', W.QMessageBox.ButtonRole.HelpRole)
        box.addButton('Ahora no', W.QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(go)
        box.exec()
        c = box.clickedButton()
        return ('go' if c is go else 'notes' if c is notes else None), chk.isChecked()


class _QtWindow(_Window):
    def __init__(self, ctl):
        W, C = ctl.ui.QtWidgets, ctl.ui.QtCore
        self._init_logic(ctl)
        u = self.u
        d = self.top = W.QDialog(ctl.ui.parent)
        d.setWindowTitle('%s - %s' % (TXT['title'], ctl.name))
        d.setAttribute(C.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        d.setAttribute(C.Qt.WidgetAttribute.WA_QuitOnClose, False)
        d.setMinimumSize(560, 460)
        self._closed = False
        d.destroyed.connect(lambda *a: setattr(self, '_closed', True))
        d.finished.connect(lambda *a: setattr(self, '_closed', True))
        lay = W.QVBoxLayout(d)
        t = W.QLabel(TXT['title'])
        fnt = t.font()
        fnt.setPointSizeF(fnt.pointSizeF() * 1.35)
        fnt.setBold(True)
        t.setFont(fnt)
        lay.addWidget(t)
        lay.addWidget(W.QLabel(self.local_text()))
        self.l_remote = W.QLabel(TXT['published'] % '—')
        lay.addWidget(self.l_remote)
        self.status = W.QLabel('')
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        lay.addWidget(W.QLabel(TXT['notes']))
        self.notes = W.QPlainTextEdit()
        self.notes.setReadOnly(True)
        lay.addWidget(self.notes, 1)
        st = u.state()
        self.c = {}
        for key, text, val, fn in (('auto', TXT['auto'], st.get('auto', True) is not False, self.toggled_auto),
                                   ('apply', TXT['auto_apply'], bool(st.get('auto_apply')), self.toggled_apply)):
            cb = self.c[key] = W.QCheckBox(text)
            cb.setChecked(val)
            cb.toggled.connect(fn)
            lay.addWidget(cb)
        self.watch_box = W.QWidget()
        wl = W.QVBoxLayout(self.watch_box)
        wl.setContentsMargins(0, 0, 0, 0)
        cb = self.c['watch'] = W.QCheckBox(TXT['watch'])
        cb.setChecked(bool(st.get('silent_watch')))
        cb.toggled.connect(self.toggled_watch)
        wl.addWidget(cb)
        h = W.QLabel(TXT['watch_help'])
        h.setWordWrap(True)
        h.setContentsMargins(22, 0, 0, 0)
        h.setEnabled(False)
        wl.addWidget(h)
        lay.addWidget(self.watch_box)
        bar = W.QHBoxLayout()
        self.b_check = W.QPushButton(TXT['check'])
        self.b_check.clicked.connect(self.start_check)
        self.b_back = W.QPushButton(TXT['back'])
        self.b_back.clicked.connect(self.rollback)
        self.b_go = W.QPushButton(TXT['go'])
        self.b_go.clicked.connect(self.apply)
        close = W.QPushButton(TXT['close'])
        close.clicked.connect(d.close)
        bar.addWidget(self.b_check)
        bar.addWidget(self.b_back)
        bar.addStretch(1)
        bar.addWidget(self.b_go)
        bar.addWidget(close)
        lay.addLayout(bar)
        foot = W.QLabel(TXT['footer'])
        foot.setEnabled(False)
        foot.setAlignment(C.Qt.AlignmentFlag.AlignRight)
        lay.addWidget(foot)
        self._w_show_watch(cb.isChecked())
        self._w_enable('go', False)
        self._w_enable('back', u.backup_available())
        if not u.enabled:
            self._w_status('Este programa no está instalado con su instalador: no puede actualizarse solo.')
            self._w_enable('check', False)
        d.show()
        self.lift()

    def alive(self):
        if self._closed:
            return False
        try:
            return self.top.isVisible()
        except Exception:
            return False

    def lift(self):
        self.top.show()
        self.top.raise_()
        self.top.activateWindow()

    def later(self, ms, fn):
        self.ctl.ui.after(ms, fn)

    def watch_visible(self):
        return not self.watch_box.isHidden()

    def _w_status(self, text):
        self.status.setText(text)

    def _w_remote(self, text):
        self.l_remote.setText(text)

    def _w_notes(self, text):
        self.notes.setPlainText(text)

    def _w_enable(self, which, on):
        {'check': self.b_check, 'go': self.b_go, 'back': self.b_back}[which].setEnabled(bool(on))

    def _w_go_text(self, text):
        self.b_go.setText(text)

    def _w_set_check(self, which, on):
        self.c[which].setChecked(on)

    def _w_show_watch(self, on):
        self.watch_box.setVisible(bool(on))

    def _w_confirm(self, text):
        W = self.ctl.ui.QtWidgets
        return W.QMessageBox.question(self.top, self.ctl.name, text) == W.QMessageBox.StandardButton.Yes


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
