<!-- IDIOMAS.md -->
# cam-ui en varios idiomas: plan y cuidados

Traducción de cam-ui al inglés y al catalán con [lang-kit](https://github.com/enderHugin/lang-kit). Este documento recoge cómo se hace y qué hay que cuidar para no romper nada. Se borrará o se resumirá cuando esté terminado.

## Cómo
- Los textos de la interfaz pasan a `lang/{es,en,ca}.json`, con una frase entera por clave, variables con nombre (`{name}`, `{fps:.1f}`) y plurales.
- En el código se usa `_t("clave", nombre=valor)`. La función se llama `_t` y no `t` porque `t` ya es una variable local en unas 40 funciones.
- **El registro (`camui.log`) sigue en español:** es técnico y así se puede buscar en él. Tampoco se traducen las órdenes de ffmpeg, las cabeceras HTTP ni las expresiones regulares.
- **Las pruebas fijan el idioma original:** cada archivo de `tests/` pone `LANGKIT_LANG=es` antes de importar cam-ui, porque comprueban los textos en español. Hay además pruebas en inglés y en catalán.
- Al cambiar de idioma (*Modo ▸ Idioma / Language*), cam-ui se reabre. Si está grabando, no: avisa.

## Por partes
1. Infraestructura: lang-kit, `_t`, menú Idioma y pruebas en español. **Hecho.**
2. La ventana: pestañas, botones, menús (también el del clic derecho sobre una cámara y el de la bandeja), diálogos, «Acerca de» y diagnóstico. **Hecho** (prueba: `tests/ui_languages.py`, la ventana en los tres idiomas).
3. Los mensajes de la barra de estado y los avisos, «Sincronizar», los informes (diagnóstico, prueba de modos) y lo que se ve sobre cada cámara. **Hecho.** Quedan en castellano, como el registro, los motivos técnicos que solo van a `camui.log` (por qué no se midió una cámara, por qué se cortó una de red). Los mensajes de «Sincronizar» que se envían al móvil salen en el idioma del PC hasta el paso 4.
4. Las páginas que se sirven al móvil y al navegador (cámara del móvil, emparejar con QR, claqueta de sincronizar, visor, certificado), en el idioma del navegador del móvil. **Hecho:**
   - cada página es una plantilla (`_VIEWER_TPL`, `_PHONECAM_TPL`, `_PHONECAM_ADD_TPL`, `_SYNC_TPL`, `_PHONECAM_CERT_PAGE`) con marcas `⟦clave⟧` (texto o HTML), `⟦js:clave⟧` (cadena de JavaScript) y `⟦jsp:clave⟧` (plural, para `P(d, n)`); en la página, `T(texto, {var: valor})` pone las variables;
   - `_web_lang(cabeceras)` elige el idioma por `Accept-Language` (si no hay ninguno de los de cam-ui, inglés; con `LANGKIT_LANG`, ese) y `_web_page()` rellena la plantilla, una vez por idioma;
   - lo que el PC le dice a la claqueta (`st.msg`) es un `_LMsg` (`_LMsg.t(clave, ...)`): en el PC se ve en su idioma y `/sync/state` lo vuelve a decir en el del móvil;
   - las claves `web.*` solo se usan en las plantillas: van en `@meta.dynamic` para `lk check`;
   - prueba: `tests/unit_web_lang.py`.

## La página de descarga
`site/index.html` usa lang-kit como las de md-reader y faster-finder: `data-t-html` / `data-t-attr` con los textos en `site/lang/{es,en,ca}.json`, `site/langkit.js` y un selector de idioma. El enlace de descarga lleva al instalador del mismo idioma (`dl/pyi-lang.js`, de py-installer), también al cambiar el selector. Quien eligió «EN» en la página anterior lo conserva.

## Cuidado: textos que también son datos
Hay textos que se guardan en `config.json` o se comparan en el código. Si se traducen sin más, un `config.json` escrito en un idioma no se entendería en otro, o una comparación dejaría de cumplirse.

| Texto | Dónde | Qué se hace |
|---|---|---|
| `ADAPT` «Adaptado al contenido» | `canvas` en config | **hecho:** al leer, todo lo que no es una proporción (16:9…) es «adaptado», en cualquier idioma |
| `AUTO` «Auto» | `structure` y modo de captura | igual |
| `NO_AUDIO` «(Silencio)» | `dev` del audio | igual |
| `TILES` (formas de celda) | `tile` de cada cámara | **ya guardaba el valor** (`auto`, `4:3`…): se traduce sin más |
| `QUALITIES` | `quality` (se guarda la etiqueta) | **hecho:** `quality_label()` reconoce la etiqueta de cualquier idioma instalado |
| «Multivista» | nombre base por defecto de las grabaciones | **hecho:** se traduce solo el valor por defecto de un config nuevo |
| «Móvil» | nombre por defecto de las cámaras de móvil, con `^Móvil( #\d+)*$` | la expresión acepta el nombre en cualquier idioma |
| `_mini_set` | compara el texto de estado (`startswith("Grabando")`…) | **hecho:** `_t_prefix(clave)`, el principio del texto en el idioma de ahora |
| `_set_status` | `startswith("Grabado")` | **hecho**, igual |
| `MODE_NAMES`, `COLOR_LABELS`, `CHANS`, `SYNCS` | se guarda el valor, no la etiqueta | se traducen sin más |
| `FFmpegEngine._drain_err`, `driver_dialog_was_shown` | comparan la salida de ffmpeg (en inglés) | no se tocan |
| Desfase del sonido `{ms} ms (más tarde/antes)`, botones `📹 Virtual ●` | diálogo Sincronizar, barra | **hecho:** una frase entera por caso, la marca dentro de la frase |
| Frases montadas a trozos (`"{0} cámara{1}"`, `" · sin medir"`, `"(s)"`) | varios | **hecho:** frases enteras con plural |
| Tipo de cámara sobre cada una (`"móvil (wifi)"`, `"otro cam-ui"`, `"red"`) | `_tile_infos`, se compara | se sigue comparando en castellano; solo se traduce al mostrarlo |
