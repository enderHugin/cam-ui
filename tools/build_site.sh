#!/usr/bin/env bash
# Monta la página en _site/: la carpeta site/ + los archivos descargables en dl/.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="$(tr -d '[:space:]' < VERSION)"
PACK="$(sed -n 's/^APP_PACK[[:space:]]*=[[:space:]]*\([0-9][0-9]*\).*/\1/p' camui.py | head -1)"
[ -n "$PACK" ] || { echo "camui.py no define APP_PACK" >&2; exit 1; }
APPVER="$(sed -n 's/^APP_VERSION[[:space:]]*=[[:space:]]*"\([0-9.]*\)".*/\1/p' camui.py | head -1)"
[ -z "$APPVER" ] || [ "$APPVER" = "$VERSION" ] || { echo "APP_VERSION ($APPVER) no coincide con VERSION ($VERSION)" >&2; exit 1; }
rm -rf _site
cp -r site _site
mkdir -p _site/dl
cp camui.py _site/dl/camui.py
# el instalador se descarga como .cmd (con saltos de línea de Windows)
sed 's/\r$//; s/$/\r/' installers/install-windows.cmd > _site/dl/cam-ui-instalar.cmd
sed -i "s/__VERSION__/${VERSION}/g" _site/index.html
touch _site/.nojekyll
# update.json: lo que lee «Buscar actualizaciones» (pack, huella sha256 y novedades)
python3 - "_site" "$VERSION" <<'PYEOF'
import hashlib, json, os, sys
out, ver = sys.argv[1], sys.argv[2]
sha = hashlib.sha256(open('camui.py', 'rb').read()).hexdigest()
size = os.path.getsize('camui.py')
import re
m = re.search(r'^APP_PACK\s*=\s*(\d+)', open('camui.py', encoding='utf-8').read(), re.M)
if not m:
    sys.exit('camui.py no define APP_PACK')
notes = open('NOVEDADES.md', encoding='utf-8').read()[:4000] if os.path.exists('NOVEDADES.md') else ''
with open(os.path.join(out, 'dl', 'update.json'), 'w', encoding='utf-8') as f:
    json.dump({'pack': int(m.group(1)), 'version': ver, 'notes': notes,
              'files': {'camui.py': {'sha256': sha, 'size': size}}}, f, ensure_ascii=False, indent=2)
PYEOF
echo "página montada en _site/ (v${VERSION}, pack ${PACK})"
