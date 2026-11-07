# cam-ui

Página del proyecto: <https://enderhugin.github.io/cam-ui/>

Mosaico de varias cámaras USB más audio L/R en un solo vídeo, para Windows.
Interfaz Tkinter + FFmpeg (DirectShow). Un único archivo: `camui.py`.

## Instalación rápida (Windows)
Descarga `cam-ui-instalar.cmd` desde la página del proyecto y ábrelo: no necesitas tener Python instalado
(prepara el suyo), descarga ffmpeg y crea accesos en el menú Inicio y el escritorio. Para quitarlo, desde
«Aplicaciones instaladas» de Windows. Fuente: [`installers/install-windows.cmd`](installers/install-windows.cmd).

## Requisitos (instalación a mano)
- Windows, Python 3.10+ con Tkinter.
- `ffmpeg.exe` junto a `camui.py` (o en la variable `CAMWIN32_FFMPEG`).
- `pip install -r requirements.txt` (los paquetes marcados como opcionales no son obligatorios).

## Uso
    python camui.py            # interfaz
    python camui.py list       # lista dispositivos
    python camui.py multi -h   # modo consola

## Funciones principales
- Sincronía de cámaras y audio con un reloj común; vúmetros L/R.
- Vista previa siempre activa, grabación MP4/MKV sin cortarla.
- Encuadre, zoom, color y formato de celda por cámara; detección y prueba de modos de captura.
- Tres modos de ventana: normal, compacto y mini (barra con Rec/Stop), con icono en la bandeja.
- Botón de pánico (F12) que cierra todos los ffmpeg.
- Cámara virtual (OBS Studio + `pyvirtualcam`) para usar el mosaico en videollamadas; solo vídeo.
- Cámara de red: un móvil por wifi (http/rtsp, p. ej. «IP Webcam» en Android) como una cámara más.
- Salida en red: ver el mosaico en vivo desde otro PC o móvil de la misma wifi con el navegador
  (menú Modo, botón de la barra, pestaña Ajustes o bandeja); funciona a la vez que se graba y que
  la cámara virtual, es solo para ver (sin contraseña) y no depende de ffmpeg para servir el vídeo.
- Se actualiza sola (con tu permiso): avisa si hay una versión nueva, guarda una copia de la
  anterior y puede volver a ella. Botón «🔄 Actualizaciones» o icono de la bandeja.

## Atajos
F5 vista previa · F8 foto · F9 grabar · F11 normal/compacto · F12 pánico · Esc vuelve al modo normal desde mini.

## Estado
Probado con cámaras simuladas en Linux (Xvfb + ffmpeg). Lo que depende de hardware o de Windows real
(cámaras DirectShow, bandeja, ventana mini, cámara virtual de OBS) se valida en el equipo del usuario.
Pruebas sin hardware: `python tests/unit_modes.py`, `tests/unit_vcam.py`, `tests/unit_netcam.py`,
`tests/unit_update.py`, `tests/geom_test.py` (necesitan ffmpeg en el PATH).
Si algo falla, adjuntar `camui.log` (se crea junto al script).

## Opinar, avisar de fallos, pedir funciones
[Abre un issue](https://github.com/enderHugin/cam-ui/issues/new/choose): hay plantillas para opinión, fallo y petición de función.

## La página
`site/` es la página (HTML estático, ES/EN). `tools/build_site.sh` la monta en `_site/` junto con los archivos
descargables, y `.github/workflows/pages.yml` la publica en GitHub Pages en cada push a `main`
(Settings → Pages → Source: GitHub Actions). La versión sale del archivo `VERSION`.

## Apoyo
[Ko-fi](https://ko-fi.com/jaimeluisolguinmesina) · Licencia MIT.
