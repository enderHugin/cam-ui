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
- En la vista previa (solo ahí), un rótulo sobre cada cámara: nombre, cómo llega (local, red,
  otro cam-ui, móvil), fps reales y resolución.
- Direcciones sin número de puerto cuando el 80 y el 443 de este PC están libres: «En la red» en
  `http://nombre-del-pc.local/`, el certificado de iPhone/iPad en `/certificado`, añadir un móvil
  en `https://…/movil` (escrito sin `https://` también llega). Los puertos de siempre (8554,
  8555…) siguen funcionando igual, p. ej. para otro cam-ui encadenado.
- Motor nuevo (el de serie desde el pack 45; el de siempre sigue en Ajustes → Avanzado): cada cámara en
  su propio proceso, abiertas todas a la vez y leídas siempre; un reloj único toma de cada una la
  imagen captada en cada instante y se la da a ffmpeg a ritmo fijo. Una cámara lenta, cortada o
  de otro PC ya no frena a las demás, reiniciar el mosaico (o empezar a grabar) no reabre las
  cámaras, y la de otro cam-ui con el motor nuevo se alinea por la hora a la que se captó allí,
  aunque los relojes de los dos PCs no coincidan.
- Vista previa siempre activa, grabación MP4/MKV sin cortarla.
- Sigue funcionando con el PC bloqueado: mientras hay vista previa (y con ella grabación, «En la
  red», cámara virtual), Windows no suspende el equipo por inactividad (la pantalla sí se apaga).
  Cerrar la tapa o suspenderlo a mano sí lo para; a batería, en portátiles con «Modern Standby»,
  Windows solo lo respeta unos minutos.
- Encuadre, zoom, color y formato de celda por cámara; detección y prueba de modos de captura.
- Tres modos de ventana: normal, compacto y mini (barra con Rec/Stop), con icono en la bandeja.
- Botón de pánico (F12) que cierra todos los ffmpeg.
- Cámara virtual (OBS Studio + `pyvirtualcam`) para usar el mosaico en videollamadas; solo vídeo.
- Cámara de red: un móvil por wifi (http/rtsp, p. ej. «IP Webcam» en Android) como una cámara más;
  también vale la «salida en red» de otro PC con cam-ui, para encadenar varios equipos (de
  momento solo el vídeo). Doble clic sobre ella en la lista para editar su nombre o dirección sin
  perder el encuadre ni el color. Si se desconecta (o cualquier otra cámara tarda demasiado en
  responder), no se cae todo el mosaico: queda un aviso «Sin señal: nombre» en su sitio, el resto
  sigue grabando, y se reconecta sola cuando vuelve (con el motor de siempre, se vuelve a probar
  cada pocos segundos y entra en cuanto responde; si se está grabando, al parar).
- Cámara del móvil, sin instalar nada y siempre activa (no hay que encenderla a mano): pestaña
  Ajustes → «Cámara del móvil» muestra, ya listas, todas las direcciones fijas `/movil` de este
  equipo, sin número de puerto si el 443 está libre (p. ej. `https://192.168.1.129/movil` o
  `https://ender-rog.local/movil`; con número y `/add` si no — las dos valen siempre), una por
  cada tarjeta de red y el nombre del equipo, cada una clicable para probarla
  desde este PC) para abrir cualquiera de ellas en el navegador del móvil; esa página pide
  permiso de cámara y, en cuanto lo concede, usa esa misma cámara para leer un QR. El QR que debe
  leer aparece en ese momento en la propia ventana del PC (en un diálogo flotante, al instante,
  encima de lo que sea que haya abierto en ese momento — no antes), así que la primera vez solo
  puede emparejarse quien esté delante del ordenador viendo su pantalla; de ahí en adelante, ese
  mismo móvil entra solo, sin volver a leer ningún QR, aunque se reinicie cam-ui o el propio móvil
  (en Ajustes, botón «Administrar dispositivos móviles», para ver cuáles son —«iPad», «iPhone» o
  «Android · modelo», con su última conexión—, renombrarlos u «olvidar» cualquiera si hace falta
  que vuelva a pedir el QR). Al leerlo, el móvil navega solo a la página de captura (1080p a
  20 fps, que manda por una conexión que se reutiliza y con hasta dos fotogramas en camino a la
  vez; la propia página dice a qué PC manda y a cuántos fps de verdad, y avisa si la wifi no da
  para más; un selector de calidad —«Auto», que baja la resolución en el propio móvil si la wifi
  no da para más, o 1080p/720p/540p fijas— reduce y comprime antes de enviar; su botón «📺 Ver
  lo que sale del PC» vuelve a «En la red») y aparece sola en la lista, marcada y lista para grabar. Usa HTTPS con un certificado
  propio (aviso de "conexión no privada" la primera vez, normal). En iPhone/iPad hace falta,
  además, instalar una vez el certificado de cam-ui (botón «Instalar certificado» en esa misma
  pestaña, con su propio QR, abriendo esa página en Safari: Chrome y los demás navegadores de
  iPhone/iPad solo lo descargan como un archivo) — sin eso, Safari no deja usar la cámara ahí en
  absoluto; en Android no hace falta. De momento solo vídeo, sin el sonido del móvil. Si ya tienes abierta la
  página de «En la red» (puerto
  distinto, sin certificado) en el móvil, ahí también hay un botón «Añadir cámara del móvil» que
  lleva directo a esa misma dirección `/add`, sin tener que teclear ni recordar el otro puerto.
- Salida en red: ver (y, si hay micrófono, oír) el mosaico en vivo desde otro PC o móvil de la
  misma wifi con el navegador (menú Modo, botón de la barra, pestaña Ajustes o bandeja), con
  pantalla completa; funciona a la vez que se graba y que la cámara virtual, es solo para ver
  (sin contraseña) y no depende de ffmpeg para servir el vídeo. En Ajustes se ven todas las
  direcciones donde responde (una por tarjeta de red y el nombre del equipo), cada una clicable
  para probarla desde este mismo PC. Se manda con la misma forma que el mosaico (con su
  encuadre, sin añadir barras negras) y sus mismos fps (hasta 30); el «Tamaño» elegido es solo
  el máximo (por defecto hasta 640×360, para ahorrar wifi) — así, otro cam-ui que la añada como
  «cámara de red» la recibe con el mismo formato y la misma fluidez que se ve aquí. Botón «Abrir en VLC»: si está
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
`tests/unit_update.py`, `tests/unit_chain.py`, `tests/unit_hub.py`, `tests/unit_awake.py`, `tests/unit_tiles.py`, `tests/unit_logrot.py`, `tests/geom_test.py`, `tests/unit_hls.py` (necesitan ffmpeg en el PATH,
este último con libx264 y el muxer hls); `tests/unit_phonecam.py` también necesita ffmpeg, además
del paquete `cryptography` (certificado HTTPS de prueba) y de `qrcode` (para comprobar el QR de
emparejamiento de «/add»); `tests/unit_vlc.py` y
`tests/unit_viewer_html.py` no necesitan ffmpeg. `tests/ui_netcam_edit.py`, `tests/ui_phonecam.py`
(este último también necesita `qrcode`, por el mismo motivo), `tests/ui_hub.py`, `tests/ui_netretry.py`
y `tests/ui_window_layout.py` prueban diálogos y ventanas reales con Tk: en Linux hace falta un
display (`xvfb-run -a python3 tests/ui_window_layout.py`), en Windows basta con tener Tk.
`tests/unit_hub.py` y `tests/unit_ipout.py` usan el ffmpeg del PATH; con
`CAMUI_FFMPEGS="ruta1:ruta2"` repiten sus pruebas de ffmpeg con otros (p. ej. un 7 u 8, que se
comportan distinto que el 6).
Si algo falla, adjuntar `camui.log` (se crea junto al script). Cada arranque y cada día
empiezan uno nuevo; los anteriores quedan en la carpeta `registros`, con el rango de horas y el
pack en el nombre.

## Opinar, avisar de fallos, pedir funciones
[Abre un issue](https://github.com/enderHugin/cam-ui/issues/new/choose): hay plantillas para opinión, fallo y petición de función.

## La página
`site/` es la página (HTML estático, ES/EN). `tools/build_site.sh` la monta en `_site/` junto con los archivos
descargables, y `.github/workflows/pages.yml` la publica en GitHub Pages en cada push a `main`
(Settings → Pages → Source: GitHub Actions). La versión sale del archivo `VERSION`.

## Apoyo
[Ko-fi](https://ko-fi.com/jaimeluisolguinmesina) · Licencia MIT.
