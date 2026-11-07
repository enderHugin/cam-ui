# Novedades de cam-ui

## pack 2 — 2026.11.07
- **Salida en red**: nueva opción «En la red» (menú Modo, botón de la barra, pestaña Ajustes y
  bandeja) para ver el mosaico en vivo desde otro PC o móvil de la misma wifi, con el navegador,
  sin instalar nada. Funciona a la vez que se graba y que la cámara virtual (OBS), de forma
  totalmente independiente: es solo para ver, sin contraseña, y la primera vez puede salir el
  aviso del Firewall de Windows (hay que permitirlo).
- Instalador para Windows: ya no hace falta tener Python instalado; se descarga e instala solo
  (igual que widget-arena), con acceso directo en el Escritorio y el menú Inicio, y aparece en
  «Aplicaciones instaladas» de Windows para desinstalarlo con un clic. Corregido un fallo real
  (`uv venv` no instalaba `pip`) que impedía terminar la instalación en algunos equipos: si te
  pasó, vuelve a ejecutar el instalador tal cual, sin borrar nada a mano, y ahora sí funcionará.

## pack 1 — 2026.11.06
- **Actualizaciones**: botón «🔄 Actualizaciones» (y el icono de la bandeja → «Buscar
  actualizaciones...»). Al abrir el programa avisa si hay una versión nueva (puedes marcar
  «no preguntar más» y actualizar a mano). Guarda una copia de la versión anterior y permite
  volver a ella con un clic. Mismo sistema que widget-arena: huella sha256 comprobada antes de
  tocar nada, copia de seguridad siempre, y el programa se reinicia solo.
- **Cámara de red**: botón «Añadir cámara de red...» en la pestaña Cámaras. Suma al mosaico un
  móvil con una app como «IP Webcam» (Android) por http/https o rtsp, en la misma wifi. Solo
  vídeo, sin audio; funciona igual en cualquier sistema (no depende de DirectShow).
