# Novedades de cam-ui

## pack 11 — 2026.11.07
- **Nuevo botón «📺 Chromecast» en la propia página de «En la red».** Quien vea el mosaico desde
  el navegador de otro equipo o móvil ya no tiene que saberlo de antes: el botón explica cómo
  usar el «Convertir en Chromecast» que ya trae el propio navegador (Chrome o Edge) para enviar
  esa misma pantalla a un Chromecast de la wifi — cam-ui no tiene que hacer nada más, el
  navegador se encarga. Avisa de que Firefox y Safari no lo traen integrado.

## pack 10 — 2026.11.07
- **Nuevo botón «▶️ Abrir en VLC» en «En la red».** VLC ya sabe leer este vídeo tal cual (se ha
  comprobado con un flujo real de cam-ui: decodifica cada fotograma igual que un navegador) y,
  lo mejor, VLC trae **de serie** el envío a Chromecast: una vez abierto ahí, su propio menú
  Reproducción → Renderizador deja elegir el Chromecast de la wifi y VLC hace la conversión él
  solo — sin tener que programar nada de eso en cam-ui. Está en la pestaña Ajustes (junto a
  «Activar en la red»), en el menú «Modo» y en el icono de la bandeja. Si VLC no está instalado,
  ofrece abrir su página de descarga. AirPlay no tiene un equivalente así: ningún reproductor de
  Windows lo trae integrado (solo existe como función del propio macOS), así que de momento
  queda fuera.

## pack 9 — 2026.11.07
- **Arreglados botones que no se veían en la ventana de «🔄 Actualizaciones».** Con las
  novedades ya largas (como estas que estás leyendo), los botones de abajo («Buscar de nuevo»,
  «Volver a la versión anterior», «Actualizar y reiniciar», «Cerrar») quedaban completamente
  recortados fuera de la ventana, sin verse ni poder pulsarse. Ahora siempre tienen su hueco
  reservado, y el cuadro de «Novedades» tiene su propia barra de desplazamiento para poder
  leerlas todas. Mismo arreglo en la ventana de informe/diagnóstico (con un informe largo,
  «Copiar informe» y «Cerrar» quedaban reducidos a una rendija de 8 píxeles).
- **La lista de cámaras ya tiene su propia barra de desplazamiento.** Antes, al hacer scroll
  sobre la lista (con varias cámaras, sobre todo añadiendo cámaras de red), se desplazaba toda
  la pestaña en vez de solo la lista, y no había forma de ver qué cámaras quedaban fuera salvo
  agrandando la ventana.

## pack 8 — 2026.11.07
- **Una cámara que falla ya no tira todo el mosaico abajo.** Hasta ahora, si una cámara de red se
  desconectaba (o cualquier cámara tardaba demasiado en responder, o dejaba de dar señal a media
  grabación), ffmpeg se paraba entero y aparecía el aviso «ffmpeg se detuvo de forma inesperada» —
  se perdían TODAS las cámaras, no solo la que falló. Ahora, al detectar cuál es la cámara
  problemática (por su nombre o dirección en el error de ffmpeg), se sustituye en su sitio del
  mosaico por un aviso «Sin señal: nombre» y el resto sigue grabando con normalidad; en vista
  previa (no grabando) ni siquiera hace falta tocar nada, se reintenta sola. Si la cámara vuelve a
  responder más adelante, basta con F5 (o reiniciar la grabación) para que vuelva a aparecer.
  Comprobado con ffmpeg real: un mosaico con una cámara caída sigue grabando fotogramas de verdad
  del resto, con el aviso bien visible en el hueco de la que falló.
- **Editar una cámara de red ya añadida, sin tener que borrarla y volver a crearla.** Doble clic
  sobre su nombre en la lista de cámaras abre el mismo cuadro que al añadirla, con el nombre y la
  dirección ya puestos; al guardar conserva el zoom, el encuadre y el color que ya tenía, aunque
  se le cambie el nombre.
- **Arreglada la pantalla completa de «En la red».** El vídeo se veía pequeño, sin ocupar la
  pantalla, en vez de aprovechar todo el espacio como debía.

## pack 7 — 2026.11.07
- **Actualizaciones: nueva casilla «Actualizar automáticamente al abrir el programa, sin
  preguntar»**, en la ventana de «🔄 Actualizaciones», junto a la de «Avisarme al abrir el
  programa si hay una versión nueva». Con ella marcada, al abrir cam-ui y encontrar una
  versión nueva ya no aparece el aviso para confirmar: se instala sola (con su copia de
  seguridad de siempre, por si hay que volver atrás) y el programa se reinicia solo pasados
  unos segundos — se ve la misma ventana de progreso de siempre, solo que sin tener que
  pulsar nada. Si estaba marcado «no preguntar más» (avisos apagados), marcar esta casilla
  los reactiva también, porque si no nunca llegaría a mirar si hay algo que instalar.

## pack 6 — 2026.11.07
- **Arreglado un fallo real en «Añadir cámara de red...» con la sincronía por defecto.** Con el
  modo de sincronía normal («por reloj común», el que se usa salvo que se cambie a mano), una
  cámara de red (el móvil con «IP Webcam», u otro PC con cam-ui en «la red» — ver más abajo) no
  grababa ningún fotograma: el vídeo se veía en la vista previa pero la grabación salía vacía o
  casi, sin avisar de ningún error. La causa: una opción de ffmpeg con la que ese modo calcula la
  hora real de cada fotograma no la respeta el tipo de flujo que usan las cámaras de red, así que
  todos los fotogramas se consideraban "de antes de empezar" y se descartaban. Comprobado de
  verdad con ffmpeg real (antes del arreglo: 0 fotogramas grabados pese a verse en la vista
  previa; después: fotogramas reales grabados y verificados con ffprobe).
- **Se pueden encadenar varias cam-ui.** Con el arreglo de arriba ya se puede añadir la dirección
  de la «salida en red» de un PC (algo como `http://192.168.1.50:8554/stream.mjpg`) como «cámara
  de red» en OTRO PC con cam-ui, para combinarla en su propio mosaico, grabarla, verla en red otra
  vez, etc. Por ahora solo el vídeo: la cámara de red sigue sin traer audio consigo (está en la
  lista para una próxima vez).
- **Salida en red: nueva opción 1920×1080** en el desplegable de tamaño (antes el máximo era
  1280×720).

## instalador (2) — 2026.11.07
- La descarga de ffmpeg (la más pesada, ~100 MB) no mostraba ningún progreso mientras bajaba, así
  que una conexión lenta se veía exactamente igual que una colgada de verdad — sin forma de
  distinguirlas. Ahora avisa cada pocos segundos cuántos MB lleva, y si de verdad se queda
  atascada sin avanzar (no solo lenta) pasado un buen rato, lo cuenta como un intento fallido y
  reintenta, en vez de quedarse esperando para siempre. Si aun así no consigue bajarlo, el aviso
  de error ya trae las instrucciones para bajarlo a mano con el navegador y dejarlo donde el
  instalador lo espera, sin tener que preguntar.

## instalador — 2026.11.07
- Arreglado un fallo real del instalador (`cam-ui-instalar.cmd`) que podía dejar la instalación
  a medias con solo «Traceback (most recent call last):» y nada más: si una instalación anterior
  dejaba el entorno de Python sin `pip` (por ejemplo, tras otro fallo a mitad de instalar), la
  comprobación que debía detectarlo y rehacerlo se topaba con un problema de PowerShell (un
  mensaje de error de un programa escribiendo en su «stderr» paraba todo el instalador en vez de
  dejar que el código siguiera, aun estando redirigido) y el instalador se caía en vez de arreglar
  el entorno solo. Si te pasó esto, vuelve a descargar el instalador (este fallo ya está
  corregido) y ejecútalo tal cual, sin borrar nada a mano: seguirá donde lo dejó. También: si
  fallan los paquetes opcionales (icono de bandeja o cámara virtual), ahora el instalador avisa de
  cuál fue y sigue adelante, en vez de arriesgarse al mismo problema.

## pack 5 — 2026.11.07
- **Salida en red: ahora también se oye.** Si hay un micrófono activado, la página del navegador
  incluye un botón «🔊 Activar sonido» (los navegadores no dejan reproducir sonido solo al abrir
  la página, hay que pulsar algo) con control de silencio y volumen propios. El vídeo va igual
  que antes; el sonido viaja aparte, codificado en MP3, que entienden todos los navegadores
  sin plugins.
- **Salida en red: pantalla completa.** Botón «⛶ Pantalla completa» en la página del navegador.
- La dirección de la barra de estado ahora, al hacer clic, abre directamente el navegador
  predeterminado en esa dirección (antes la copiaba al portapapeles).

## pack 4 — 2026.11.07
- **Salida en red**: la barra de estado y la bandeja ahora muestran la dirección por IP *y* por
  nombre del equipo (antes solo una de las dos), para cuando una de las dos no resuelve en el
  otro aparato.
- **Iniciar con Windows**: nueva opción en el menú del icono de la bandeja (clic derecho). Pone
  o quita cam-ui del inicio de sesión de Windows; si el programa se reinstala en otra carpeta, se
  corrige sola la próxima vez que se active.

## pack 3 — 2026.11.07
- **Salida en red: la dirección se ve sin buscarla.** Mientras está activa, la esquina de la
  barra de estado (abajo) y el icono de la bandeja (al dejar el ratón encima) muestran la
  dirección exacta que hay que abrir en el otro equipo o móvil. Clic en la barra de estado para
  copiarla.

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
