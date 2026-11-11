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
- Cámara de red: un móvil por wifi (http/rtsp, p. ej. «IP Webcam» en Android) como una cámara más;
  también vale la «salida en red» de otro PC con cam-ui, para encadenar varios equipos (de
  momento solo el vídeo). Doble clic sobre ella en la lista para editar su nombre o dirección sin
  perder el encuadre ni el color. Si se desconecta (o cualquier otra cámara tarda demasiado en
  responder), no se cae todo el mosaico: queda un aviso «Sin señal: nombre» en su sitio, el resto
  sigue grabando, y se reconecta sola cuando vuelve.
- Cámara del móvil, sin instalar nada y siempre activa (no hay que encenderla a mano): pestaña
  Ajustes → «Cámara del móvil» muestra, ya listas, todas las direcciones fijas `/add` de este
  equipo (una por cada tarjeta de red y el nombre del equipo, cada una clicable para probarla
  desde este PC) para abrir cualquiera de ellas en el navegador del móvil; esa página pide
  permiso de cámara y, en cuanto lo concede, usa esa misma cámara para leer un QR. El QR que debe
  leer aparece en ese momento en la propia ventana del PC (en un diálogo flotante, al instante,
  encima de lo que sea que haya abierto en ese momento — no antes), así que la primera vez solo
  puede emparejarse quien esté delante del ordenador viendo su pantalla; de ahí en adelante, ese
  mismo móvil entra solo, sin volver a leer ningún QR, aunque se reinicie cam-ui o el propio móvil
  (en Ajustes, botón «Dispositivos recordados», para ver cuáles son y «olvidar» cualquiera si hace
  falta que vuelva a pedir el QR). Al leerlo, el móvil navega solo a la página de captura (1080p a
  20 fps) y aparece sola en la lista, marcada y lista para grabar. Usa HTTPS con un certificado
  propio (aviso de "conexión no privada" la primera vez, normal). En iPhone/iPad hace falta,
  además, instalar una vez el certificado de cam-ui (botón «Instalar certificado» en esa misma
  pestaña, con su propio QR) — sin eso, Safari no deja usar la cámara ahí en absoluto; en
  Android no hace falta. De momento solo vídeo, sin el sonido del móvil. Si ya tienes abierta la
  página de «En la red» (puerto
  distinto, sin certificado) en el móvil, ahí también hay un botón «Añadir cámara del móvil» que
  lleva directo a esa misma dirección `/add`, sin tener que teclear ni recordar el otro puerto.
- Salida en red: ver (y, si hay micrófono, oír) el mosaico en vivo desde otro PC o móvil de la
  misma wifi con el navegador (menú Modo, botón de la barra, pestaña Ajustes o bandeja), con
  pantalla completa; funciona a la vez que se graba y que la cámara virtual, es solo para ver
  (sin contraseña) y no depende de ffmpeg para servir el vídeo. En Ajustes se ven todas las
  direcciones donde responde (una por tarjeta de red y el nombre del equipo), cada una clicable
  para probarla desde este mismo PC. Botón «Abrir en VLC»: si está
  instalado, lo abre ahí en vez del navegador, y desde el propio menú de VLC (Reproducción →
  Renderizador) se puede enviar a un Chromecast de la misma red sin nada más que configurar.
  La propia página también trae el icono nativo de Chromecast (un segundo ffmpeg aparte, H.264
  por HLS, solo mientras se está emitiendo a la tele) y, si el navegador no lo trae integrado,
  una ayuda explicando cómo hacerlo con el «Convertir en Chromecast» del propio navegador.
- Se actualiza sola (con tu permiso): avisa si hay una versión nueva, guarda una copia de la
  anterior y puede volver a ella. Botón «🔄 Actualizaciones» o icono de la bandeja; con la
  casilla «actualizar automáticamente al abrir el programa, sin preguntar» ni siquiera hace
  falta confirmarlo.
- Iniciar con Windows: opción en el menú del icono de la bandeja.

## Atajos
F5 vista previa · F8 foto · F9 grabar · F11 normal/compacto · F12 pánico · Esc vuelve al modo normal desde mini.

## Estado
Probado con cámaras simuladas en Linux (Xvfb + ffmpeg). Lo que depende de hardware o de Windows real
(cámaras DirectShow, bandeja, ventana mini, cámara virtual de OBS) se valida en el equipo del usuario.
Pruebas sin hardware: `python tests/unit_modes.py`, `tests/unit_vcam.py`, `tests/unit_netcam.py`,
`tests/unit_update.py`, `tests/geom_test.py`, `tests/unit_hls.py` (necesitan ffmpeg en el PATH,
este último con libx264 y el muxer hls); `tests/unit_phonecam.py` también necesita ffmpeg, además
del paquete `cryptography` (certificado HTTPS de prueba) y de `qrcode` (para comprobar el QR de
emparejamiento de «/add»); `tests/unit_vlc.py` y
`tests/unit_viewer_html.py` no necesitan ffmpeg. `tests/ui_netcam_edit.py`, `tests/ui_phonecam.py`
(este último también necesita `qrcode`, por el mismo motivo)
y `tests/ui_window_layout.py` prueban diálogos y ventanas reales con Tk: en Linux hace falta un
display (`xvfb-run -a python3 tests/ui_window_layout.py`), en Windows basta con tener Tk.
Si algo falla, adjuntar `camui.log` (se crea junto al script).

## Opinar, avisar de fallos, pedir funciones
[Abre un issue](https://github.com/enderHugin/cam-ui/issues/new/choose): hay plantillas para opinión, fallo y petición de función.

## La página
`site/` es la página (HTML estático, ES/EN). `tools/build_site.sh` la monta en `_site/` junto con los archivos
descargables, y `.github/workflows/pages.yml` la publica en GitHub Pages en cada push a `main`
(Settings → Pages → Source: GitHub Actions). La versión sale del archivo `VERSION`.

## Apoyo
[Ko-fi](https://ko-fi.com/jaimeluisolguinmesina) · Licencia MIT.
