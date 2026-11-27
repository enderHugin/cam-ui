// langkit.js
/* lang-kit para páginas web: los mismos diccionarios JSON y las mismas reglas que langkit.py.

  <html data-langkit="lang/" data-langkit-langs="es,en,ca">
  <h1 data-t="page.title">Título</h1>                         textContent
  <p data-t-html="page.intro"></p>                            innerHTML (solo con diccionarios propios)
  <input data-t-attr="placeholder:form.name;title:form.name_tip">
  <select data-langkit-picker></select>                        selector de idioma (se rellena solo)
  <script src="langkit.js" defer></script>

  Idioma: ?lang=xx en la dirección, el elegido antes en esta página (localStorage), el del navegador
  y, si la página no lo tiene, inglés y luego el original. Si falta un texto sale en inglés y luego en el original.

  En JS: LangKit.ready.then(L => L.t('files.count', {n: 3}));  LangKit.set('ca');
  Sin descargar nada: window.LANGKIT_DICTS = {es: {...}, en: {...}} antes de cargar langkit.js.
*/
(function (root) {
  'use strict';
  var VERSION = '1.0.2';
  var NAMES = { es: 'Español', en: 'English', ca: 'Català', fr: 'Français', de: 'Deutsch', pt: 'Português',
    it: 'Italiano', eu: 'Euskara', gl: 'Galego', nl: 'Nederlands', pl: 'Polski', ru: 'Русский' };
  var STORE = 'langkit.lang';

  function norm(c) {
    c = String(c || '').split('.')[0].split('@')[0].replace(/_/g, '-').trim();
    if (!/^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$/.test(c)) return '';
    var p = c.split('-');
    return [p[0].toLowerCase()].concat(p.slice(1).map(function (x) { return x.length === 2 ? x.toUpperCase() : x; })).join('-');
  }

  function match(wanted, avail) {
    var w = norm(wanted); if (!w) return null;
    var i, low = {};
    for (i = 0; i < avail.length; i++) low[avail[i].toLowerCase()] = avail[i];
    if (low[w.toLowerCase()]) return low[w.toLowerCase()];
    var base = w.split('-')[0];
    if (low[base]) return low[base];
    for (i = 0; i < avail.length; i++) if (avail[i].split('-')[0].toLowerCase() === base) return avail[i];
    return null;
  }

  function pluralForm(code, n) {
    n = Math.abs(Number(n)); if (isNaN(n)) return 'other';
    var i = Math.floor(n), whole = n === i, base = String(code || '').split('-')[0];
    if (['ja', 'zh', 'ko', 'tr', 'vi', 'th', 'id'].indexOf(base) >= 0) return 'other';
    if (base === 'fr' || code === 'pt-BR') return n < 2 ? 'one' : 'other';
    if (base === 'ru' || base === 'uk' || base === 'pl') {
      if (!whole) return 'other';
      if (base === 'pl' && i === 1) return 'one';
      if (base !== 'pl' && i % 10 === 1 && i % 100 !== 11) return 'one';
      if (i % 10 >= 2 && i % 10 <= 4 && !(i % 100 >= 12 && i % 100 <= 14)) return 'few';
      return 'many';
    }
    return n === 1 ? 'one' : 'other';
  }

  function fmt(s, v) {
    s = String(s);
    return s.replace(/\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)\}/g, function (m, k) {
      if (m === '{{') return '{'; if (m === '}}') return '}';
      return v && Object.prototype.hasOwnProperty.call(v, k) ? String(v[k]) : m;
    });
  }

  // lo que el navegador prefiere, del más al menos preferido
  function browserLangs() {
    var out = [];
    try {
      var q = /[?&]lang=([^&#]+)/.exec(root.location.search); if (q) out.push(decodeURIComponent(q[1]));
    } catch (e) { /* sin location */ }
    try { var s = root.localStorage.getItem(STORE); if (s) out.push(s); } catch (e) { /* sin almacenamiento */ }
    var nav = root.navigator || {};
    (nav.languages || [nav.language || nav.userLanguage || '']).forEach(function (l) { out.push(l); });
    return out;
  }

  function Lang(dicts, opts) {
    opts = opts || {};
    this.dicts = dicts; this.source = opts.source || 'es';
    this.fallback = opts.fallback || ['en'];
    this.listeners = []; this.missing = {};
    var avail = Object.keys(dicts), cands = [opts.lang].concat(browserLangs(), this.fallback, [this.source]), got = null;
    for (var i = 0; i < cands.length && !got; i++) got = cands[i] ? match(cands[i], avail) : null;
    this.code = got || avail[0] || this.source;
    this._chain();
  }
  Lang.prototype._chain = function () {
    var c = [this.code], seen = {}, self = this;
    if (this.code.indexOf('-') > 0) c.push(this.code.split('-')[0]);
    this.chain = c.concat(this.fallback, [this.source]).filter(function (x) {
      if (seen[x] || !self.dicts[x]) return false; seen[x] = 1; return true;
    });
  };
  Lang.prototype.t = function (key, vars) {
    var v = null, code = this.code;
    for (var i = 0; i < this.chain.length; i++) {
      var d = this.dicts[this.chain[i]];
      if (Object.prototype.hasOwnProperty.call(d, key)) { v = d[key]; code = this.chain[i]; break; }
    }
    if (v === null) { this.missing[key] = 1; return key; }
    if (typeof v === 'object') {
      var n = vars && (vars.n !== undefined ? vars.n : vars.count);
      v = (n === 0 && v.zero) || v[pluralForm(code, n)] || v.other || key;
    }
    return fmt(v, vars);
  };
  Lang.prototype.name = function (code) {
    code = code || this.code; var m = (this.dicts[code] || {})['@meta'];
    return (m && m.name) || NAMES[code] || NAMES[code.split('-')[0]] || code;
  };
  Lang.prototype.available = function () {
    var self = this;
    return Object.keys(this.dicts).sort(function (a, b) {
      return (a !== self.source) - (b !== self.source) || self.name(a).localeCompare(self.name(b));
    }).map(function (c) { return [c, self.name(c)]; });
  };
  Lang.prototype.onChange = function (fn) { this.listeners.push(fn); return fn; };
  Lang.prototype.set = function (code, save) {
    var got = match(code, Object.keys(this.dicts)) || this.code, changed = got !== this.code;
    this.code = got; this._chain();
    if (save !== false) { try { root.localStorage.setItem(STORE, got); } catch (e) { /* sin almacenamiento */ } }
    if (root.document) this.apply(root.document);
    if (changed) this.listeners.forEach(function (fn) { try { fn(got); } catch (e) { console.error(e); } });
    return got;
  };
  // rotula todo lo que tenga data-t, data-t-html o data-t-attr dentro de `el`
  Lang.prototype.apply = function (el) {
    var self = this, doc = el.ownerDocument || el;
    if (el === doc && doc.documentElement) doc.documentElement.lang = this.code;
    Array.prototype.forEach.call(el.querySelectorAll('[data-t]'), function (n) { n.textContent = self.t(n.getAttribute('data-t'), n.dataset.tVars ? JSON.parse(n.dataset.tVars) : null); });
    Array.prototype.forEach.call(el.querySelectorAll('[data-t-html]'), function (n) { n.innerHTML = self.t(n.getAttribute('data-t-html'), n.dataset.tVars ? JSON.parse(n.dataset.tVars) : null); });
    Array.prototype.forEach.call(el.querySelectorAll('[data-t-attr]'), function (n) {
      n.getAttribute('data-t-attr').split(';').forEach(function (pair) {
        var p = pair.split(':'); if (p.length === 2) n.setAttribute(p[0].trim(), self.t(p[1].trim()));
      });
    });
    Array.prototype.forEach.call(el.querySelectorAll('select[data-langkit-picker]'), function (s) {
      if (!s._langkit) {
        s._langkit = 1;
        self.available().forEach(function (a) { var o = doc.createElement('option'); o.value = a[0]; o.textContent = a[1]; s.appendChild(o); });
        s.addEventListener('change', function () { self.set(s.value); });
      }
      s.value = self.code;
    });
  };

  function load(dir, langs, opts) {
    dir = dir.replace(/\/?$/, '/');
    var domains = (opts && opts.domains) || [''];
    var jobs = [], dicts = {};
    langs.forEach(function (c) {
      domains.forEach(function (dom) {
        jobs.push(fetch(dir + (dom ? dom + '.' : '') + c + '.json').then(function (r) { return r.ok ? r.json() : {}; })
          .catch(function () { return {}; }).then(function (d) {
            var t = dicts[c] = dicts[c] || {};
            Object.keys(d).forEach(function (k) { if (!(k in t)) t[k] = d[k]; });
          }));
      });
    });
    return Promise.all(jobs).then(function () {
      Object.keys(dicts).forEach(function (c) { if (!Object.keys(dicts[c]).length) delete dicts[c]; });
      return dicts;
    });
  }

  var api = {
    VERSION: VERSION, Lang: Lang, match: match, pluralForm: pluralForm, fmt: fmt, browserLangs: browserLangs, load: load,
    current: null,
    // elige, de una lista de idiomas, el que prefiere el visitante (para enlazar el instalador de su idioma)
    pick: function (avail, source) {
      var c = browserLangs().concat(['en', source || 'es']);
      for (var i = 0; i < c.length; i++) { var g = match(c[i], avail); if (g) return g; }
      return avail[0];
    },
    init: function (opts) {
      opts = opts || {};
      var p = opts.dicts ? Promise.resolve(opts.dicts) : load(opts.dir || 'lang/', opts.langs || ['es', 'en'], opts);
      return p.then(function (d) {
        var L = api.current = new Lang(d, opts);
        if (root.document && opts.apply !== false) L.apply(root.document);
        return L;
      });
    },
    t: function (k, v) { return api.current ? api.current.t(k, v) : k; },
    set: function (c) { return api.current && api.current.set(c); }
  };

  if (typeof module !== 'undefined' && module.exports) { module.exports = api; return; }
  root.LangKit = api;
  // arranque automático: <html data-langkit="lang/" data-langkit-langs="es,en,ca"> o window.LANGKIT_DICTS
  var html = root.document && root.document.documentElement;
  var auto = root.LANGKIT_DICTS ? { dicts: root.LANGKIT_DICTS } :
    (html && html.getAttribute('data-langkit') !== null ? { dir: html.getAttribute('data-langkit') || 'lang/',
      langs: (html.getAttribute('data-langkit-langs') || 'es,en').split(',') } : null);
  if (auto) {
    if (html.getAttribute('data-langkit-source')) auto.source = html.getAttribute('data-langkit-source');
    var go = function () { return api.init(auto); };
    api.ready = root.document.readyState === 'loading'
      ? new Promise(function (res) { root.document.addEventListener('DOMContentLoaded', function () { go().then(res); }); })
      : go();
  } else {
    api.ready = new Promise(function (res) { api._resolve = res; });
    var init0 = api.init;
    api.init = function (o) { return init0(o).then(function (L) { if (api._resolve) { api._resolve(L); api._resolve = null; } return L; }); };
  }
})(typeof window !== 'undefined' ? window : globalThis);
