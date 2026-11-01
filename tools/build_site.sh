#!/usr/bin/env bash
# Monta la página en _site/: la carpeta site/ + los archivos descargables en dl/.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="$(tr -d '[:space:]' < VERSION)"
rm -rf _site
cp -r site _site
mkdir -p _site/dl
cp camui.py _site/dl/camui.py
# el instalador se descarga como .cmd (con saltos de línea de Windows)
sed 's/\r$//; s/$/\r/' installers/install-windows.cmd > _site/dl/cam-ui-instalar.cmd
sed -i "s/__VERSION__/${VERSION}/g" _site/index.html
touch _site/.nojekyll
echo "página montada en _site/ (v${VERSION})"
