# Novedades de cam-ui

## pack 52 — 2026.11.21
- **«⏱ Sincronizar»: sonido y cámaras a la vez, con un botón.** Cada cámara tarda lo suyo en
  llegar al PC (una USB poco, un móvil por wifi más, otro cam-ui encadenado bastante más) y el
  micrófono casi nada, así que en la grabación el sonido iba por delante de la imagen, y cada
  cámara por una cantidad distinta. Ahora un móvil cualquiera hace de claqueta: se abre el
  código QR de la ventana «Sincronizar», se toca «Empezar» y, mientras parpadea en blanco y
  pita, se enseña a cada cámara unos segundos. cam-ui graba una prueba (no se guarda), busca los
  pitidos en el sonido y los destellos en la casilla de cada cámara, y pone solo el «Retardo» de
  cada una y el «Ajuste A/V» del sonido para que todo vaya a la vez que la más lenta. Dice cuánto
  llegaba tarde cada cámara, y tiene «Deshacer».
  - Vale que cada cámara vea solo unos pocos destellos y que el micrófono oiga solo algunos
    pitidos (cada destello tiene su duración y su separación, y no se confunden). Una cámara que
    no lo ve se queda como estaba; si el micrófono no oye nada, se igualan solo las cámaras.
  - El botón está arriba («⏱ Sincronizar»), en el menú «Modo» y en la pestaña «Audio».
  - Con otro cam-ui encadenado: sincroniza primero el otro PC (sus cámaras entre sí) y luego
    este.
  - La vista previa va tan por detrás como la cámara más lenta: es lo que hay que esperarla.

## pack 51 — 2026.11.18
- **Las cámaras vuelven a su sitio.** Cada combinación de cámaras recuerda cómo la dejaste: orden
  y disposición («2 + 2», «1 + 2», «Auto», por filas o columnas). Cuando esas mismas cámaras
  vuelven a juntarse, el mosaico queda exactamente igual, aunque haya pasado un año: al abrir
  cam-ui, al enchufar una cámara (y pulsar «Refrescar dispositivos»), al volver un móvil o al
  marcarla en la lista. Antes, una cámara que se desenchufaba y volvía iba al final y la
  disposición pasaba a «Auto». Si esa combinación no se ha visto nunca, las demás no se mueven y
  la que llega se pone junto a la que tenía al lado. Se recuerdan las 60 combinaciones más
  recientes.
- **Los ajustes de cada cámara se guardan aunque no esté conectada**: resolución, encuadre y
  zoom, giro, espejo, color, retardo y ★. Antes se perdían si cam-ui se cerraba sin esa cámara.
- **Dos cámaras iguales** (mismo modelo, mismo nombre) conservan cada una su número, su sitio y
  sus ajustes aunque Windows las liste en otro orden: van con el puerto USB donde están
  enchufadas (si se cambian de puerto, pueden intercambiarse).
- **Un móvil que recarga la página o se reconecta vuelve a su casilla** con sus ajustes, en vez
  de añadirse otra vez al final y dejar la de antes con «Sin señal». Se reconoce por venir de la
  misma IP o porque la conexión anterior ya no manda nada. Otro móvil con el mismo nombre
  conectado a la vez va aparte, como antes.

## pack 50 — 2026.11.18
- **Grabando, si una cámara se gira, el mosaico se recoloca sin dejar de grabar.** Por ejemplo,
  cuando en otro cam-ui encadenado se gira el móvil y su mosaico pasa de apaisado a vertical. Si
  sigue girada un segundo y medio, cam-ui cierra el trozo grabado y sigue en otro con el mosaico
  recolocado, del mismo tamaño de vídeo (encajado con bandas negras si hace falta). Al parar, los
  trozos se juntan sin recodificar en un solo archivo, MP4 o MKV, y se borran. En el salto se
  pierden unas décimas de segundo (en las pruebas, 0,1 s de 15,5). Otros cambios de forma sin
  girar (de 16:9 a 4:3, por ejemplo) siguen viéndose enteros con bandas negras hasta parar. Si
  los trozos no se pudieran juntar, se quedan en la carpeta y se avisa. Solo con el motor nuevo.

## pack 49 — 2026.11.18
- **Una cámara que cambia de forma ya no se ve estirada.** Caso típico: otro cam-ui encadenado
  cuyo móvil se gira, y con él cambia la forma de todo su mosaico. Hasta que este mosaico se
  recoloca (unos segundos) esa cámara se ve entera, con bandas negras, en vez de estirada a lo
  ancho de su casilla (lo mismo en el otro PC con su propio móvil). **Grabando**, el mosaico no
  se recoloca, porque el vídeo cambiaría de forma a mitad: se queda así, con un aviso, y se
  recoloca al parar.

## pack 48 — 2026.11.18
- **El mosaico se recoloca solo cuando una cámara de red cambia de forma**, también si no es
  otro cam-ui (IP Webcam, ESP32-CAM, la mayoría de cámaras IP por MJPEG): girar el móvil o
  cambiar la resolución en su app. Antes ffmpeg la seguía dando al tamaño del principio,
  deformada, y el mosaico no cambiaba; ahora, con el motor nuevo, cam-ui lee esas cámaras él
  mismo y ve el tamaño de cada imagen (si una no es MJPEG, la sigue leyendo ffmpeg, como antes).
  La cámara del móvil y otro cam-ui ya lo hacían. Si se está grabando, se recoloca al parar.
- **★ Cámaras destacadas.** En Cámaras → Ajustes de la cámara, «★ Destacada: más grande con la
  disposición Auto» (hasta dos). Con la disposición «Auto», las destacadas van primero y al
  menos el doble de grandes que cualquiera de las demás, que se reparten a su lado o debajo
  (con pocas cámaras, encogiéndolas y dejando un hueco negro). En el mapa del mosaico llevan ★, y
  la miniatura de la galería pasa a «AUTO ★». Con una disposición elegida a mano no cambian nada.

## pack 47 — 2026.11.18
- **Pantalla completa para la vista previa.** Un modo más: en el menú Modo (y en el del icono
  de la bandeja), o con el botón «⛶ Pantalla completa» que aparece sobre la imagen del mosaico
  al mover el ratón por encima. Solo el mosaico, ocupando toda la pantalla, y la vista previa se
  pide a ffmpeg al tamaño de la pantalla (en la ventana, como mucho 880×560, para ahorrar). Para
  salir: Esc, doble clic o el botón «✕ Salir de pantalla completa», que aparece al mover el
  ratón; sin moverlo, se esconden el botón y el puntero. Si se está grabando al entrar, la
  imagen se amplía al dibujarla (pedirla más grande cortaría la grabación). cam-ui nunca
  arranca en pantalla completa.
- Rótulo de la vista previa: una cámara que es otro cam-ui sale como «cam-ui@Omen» (su nombre
  aquí), en vez de «🌐 Omen · otro cam-ui».

## pack 46 — 2026.11.18
- **Contraseña como alternativa al QR para añadir un móvil.** En Salida → Cámara del móvil hay un
  campo «Contraseña» (oculta, con «ver» para enseñarla). En el móvil, mientras la página espera
  el QR, el botón «🔑 Usar contraseña» deja escribirla en su lugar: con la buena, el móvil se
  añade igual que si hubiera leído el QR, y las siguientes veces entra solo. Vacía, solo vale el
  QR, como hasta ahora. Va cifrada (HTTPS) y nunca en la dirección ni en el registro. Para que
  nadie de la wifi pueda probar a lo loco: tras 5 fallos seguidos, ese móvil espera 30 s (que se
  doblan en cada fallo más, hasta 15 min), y más de 20 fallos entre todos en 10 min la desactivan
  un rato; el QR sigue valiendo siempre.

## pack 45 — 2026.11.17
- **El motor nuevo pasa a ser el de serie.** Cada cámara va en su propio proceso: se abren
  todas a la vez, se leen siempre a su ritmo y cada imagen lleva la hora a la que llegó (no la
  que dice la cámara), así que una cámara lenta, una que marca mal su hora, una que se corta o
  la de otro PC ya no frenan a las demás. Se aplica también donde la casilla quedó desmarcada,
  porque hasta ahora venía así de serie; si lo prefieres, se puede volver al motor de siempre en
  Ajustes → Avanzado, y desde entonces se respeta lo que elijas.

## pack 44 — 2026.11.17
- **Un registro por tramo.** Cada vez que cam-ui arranca (y por tanto tras cada actualización,
  que lo reinicia) y cada vez que cambia el día, `camui.log` se guarda en la carpeta
  «registros», junto a él, con el rango de horas que cubre y el pack que lo escribió en el
  nombre —p. ej. «camui 2026-11-17 14.53 a 15.32 (pack 43).log»—, y se empieza uno nuevo. No se
  borra ninguno (antes, al pasar de 1,5 MB se vaciaba sin más). Si en un mismo día llegara a
  20 MB, también se parte.
- En el registro, los avisos de DirectShow de fotogramas tirados por búfer lleno («real-time
  buffer … too full», uno por fotograma) se resumen en una línea por minuto con cuántos y hasta
  qué porcentaje. Antes se comían en un minuto todo lo que se apunta de ffmpeg, y lo que pasaba
  después ya no se veía.

## pack 43 — 2026.11.17
- **Con la sincronía «Marcas del dispositivo», una cámara de red ya no frena todo el mosaico.**
  ffmpeg daba por hecho que las de red (otro cam-ui, el móvil, IP Webcam…) mandan 25 fps: una
  que mandaba 12 hacía ir el mosaico entero a media velocidad, y las cámaras USB, leídas a ese
  medio ritmo, acumulaban retraso hasta llenar su búfer y tirar fotogramas (la EOS la primera;
  al cabo de horas, la vista previa casi parada). Ahora cada cámara de red va con la hora real
  a la que llega cada imagen. Con «Reloj común» (la opción recomendada) no pasaba.
- **Una cámara de red «sin señal» vuelve sola** (motor de siempre). Si no respondía al arrancar
  el mosaico (por ejemplo, porque el otro PC se estaba reiniciando, como al actualizarse los dos
  a la vez) o se corta con todo en marcha, se vuelve a probar cada 10 s (luego, más espaciado)
  y, en cuanto responde, se reinicia el mosaico para meterla. Si se está grabando, entra al
  parar. La que se corta en marcha ya no se queda congelada en su última imagen sin decir nada:
  su rótulo pone «sin señal».
- Rótulo de la vista previa: los fps «~» de una cámara ya no se contagian de la de al lado
  (los bordes de cada casilla mezclan los de la vecina al reducir la imagen).

## pack 42 — 2026.11.17
- **Sigue funcionando con el PC bloqueado.** Mientras la vista previa está en marcha (y con ella
  la grabación, «En la red» y la cámara virtual), cam-ui le pide a Windows que no suspenda el
  equipo por inactividad, como hace OBS. Bloquear (Win+L) no para los programas de escritorio por
  sí solo; lo que corta es la suspensión que llega al rato de no tocar el PC. La pantalla sí se
  apaga como siempre. En pausa (F5) o al cerrar cam-ui, el PC vuelve a poder suspenderse. Lo
  que Windows no deja evitar: suspenderlo a propósito (cerrar la tapa, el botón de encendido,
  Inicio → Suspender) y, en portátiles a batería con «Modern Standby», que la suspensión llegue
  igualmente unos 5 minutos después de lo normal. `powercfg /requests` lo muestra como «cam-ui».

## pack 41 — 2026.11.16
- Rótulo de la vista previa, con el motor de siempre: los fps con «~» se cuentan en todos los
  fotogramas que llegan de ffmpeg, no solo en los que la ventana alcanza a dibujar. Antes el
  número no podía pasar de las veces por segundo que se redibuja la ventana (menos de 30): una
  cámara de 30 fps podía salir a «~19».

## pack 40 — 2026.11.16
- **Rótulo sobre cada cámara del mosaico, solo en la vista previa** (no sale en la grabación, ni
  en la cámara virtual, ni en «En la red»): su nombre, cómo llega (local, red, otro cam-ui,
  móvil por wifi), a cuántos fps y su resolución real; «sin señal» en amarillo si no llega. Con
  el motor nuevo, los fps son los que manda de verdad cada cámara; con el de siempre, los de un
  móvil también, y los del resto se miden en la propia vista previa (con «~»: lo que se ve).
  Si la casilla es estrecha, va en dos líneas y se cae antes la resolución que los fps.

## pack 39 — 2026.11.16
- **Direcciones sin número de puerto.** Si el 80 y el 443 de este PC están libres, cam-ui
  también atiende ahí y reparte cada petición al servidor que toca:
  - «En la red»: `http://ender-rog.local/` (o con la IP), en vez de `…:8554/`;
  - añadir un móvil: `https://…/movil` (escrito sin `https://` también llega: se redirige);
  - certificado de iPhone/iPad: `http://…/certificado`, en vez de un puerto que cambiaba cada vez.
  Ajustes, los QR y los botones ya enseñan estas direcciones. Las de siempre (8554, 8555…)
  siguen funcionando igual, p. ej. para otro cam-ui encadenado. Si otro programa usa ya el 80 o el
  443, esa parte no se enciende y todo sigue con su número. El certificado de la cámara del móvil
  ahora cubre también el nombre del PC (`ender-rog` y `ender-rog.local`), así que se rehace una
  vez: en Android volverá a salir una vez el aviso de «conexión no privada»; en iPhone/iPad, con
  el certificado ya instalado, nada.
- **Calidad en la página del móvil:** selector «Auto» (por defecto) / 1080p / 720p / 540p. El
  móvil reduce y comprime antes de enviar: menos wifi y menos batería. «Auto» empieza en 1080p y
  baja un escalón si en 3 s no pasa de 14 fps; sube solo si cabe con holgura. Con una wifi
  simulada lenta (80 ms, 8 Mbit/s) pasa sola a 360p y va a 10 fps (en 1080p, 2 fps); con una
  buena (30 ms, 60 Mbit/s) se queda en 1080p a 20 fps. La página muestra la resolución en uso.

## pack 38 — 2026.11.16
- **«📺 Ver lo que sale del PC» en la página de la cámara del móvil**: deja de mandar la cámara
  (la suelta de verdad) y vuelve a la página «En la red» de ese mismo PC. Desde allí, «Añadir
  cámara del móvil» trae de vuelta a la cámara sin leer ningún QR. Si en el PC «En la red» está
  apagada, el botón lo dice en vez de llevar a ninguna parte.

## pack 37 — 2026.11.16
- **«Cámara del móvil» mucho más fluida.** Tu registro lo dejó claro: el iPad mandaba ~3 fps y
  tardaba 220-400 ms en enviar cada fotograma, fuera de 200 KB o de 500 -- no era la wifi, eran
  las esperas: cada fotograma abría una conexión nueva (con su saludo de seguridad) y esperaba a
  que terminara el anterior. Ahora la conexión se reutiliza y van hasta dos fotogramas en camino
  a la vez. Medido con el navegador de verdad a través de una wifi simulada (30 ms de ida y
  vuelta, 60 Mbit/s) y fotogramas de 250 KB: de 4 fps a 19-20 fps; con una wifi peor (60 ms, 30
  Mbit/s), de 2 a 10 fps. Mismo tamaño y calidad que antes.
- **La página del móvil ya no dice «✅» si va mal:** pone «✅ Enviando a
  cam-ui@nombre-del-pc.local · 19 fps» con los fps de verdad, y «⚠️ … la wifi no da para más»
  si baja de 10.
- **Con el motor de siempre, un móvil lento ya no sale «Sin señal».** La prueba previa (y el
  «Diagnóstico») contaban «1,5 segundos» como 37 fotogramas a 25 fps supuestos: a 3 fps tardaba
  12 s, daba «tiempo agotado» y el móvil entraba como «Sin señal» aunque su imagen sí llegaba
  (lo que pasaba en tu registro cada vez). Ahora cuentan segundos de verdad, y el «Diagnóstico»
  da los fps reales de las cámaras de red (antes, 25 para cualquiera).
- **«Administrar dispositivos móviles»** (antes «Dispositivos recordados»): cada móvil aparece
  con su tipo y su última conexión, y se puede **renombrar** (doble clic o «Renombrar…»), no
  solo olvidar. El nombre es el que llevará su cámara la próxima vez que se conecte.
- **Nombre por defecto según el móvil:** «iPad» (también cuando Safari se hace pasar por un Mac),
  «iPhone» o «Android · modelo» (el modelo que da el propio Android: a veces el nombre comercial,
  a veces un código como «23124RN87G»), en vez de «Móvil». Los que ya tenías como «Móvil #3» se
  ponen al día solos al reconectar; los que hayas renombrado a mano, no.

## pack 36 — 2026.11.16
- **Diagnóstico de «Cámara del móvil»** (no cambia cómo funciona nada): cada 5 segundos,
  camui.log anota cómo llega cada móvil -- fotogramas por segundo y el mayor hueco entre dos,
  resolución, KB por fotograma y Mbit/s, y lo que el propio móvil tarda en hacer cada JPEG y en
  enviarlo --, además de desde qué navegador se conectó, cuántos de esos fotogramas le llegan de
  verdad a ffmpeg con el motor de siempre (o lo que tarda en decodificarlos el motor nuevo) y
  cuándo y por qué se corta la imagen del móvil. Es para encontrar por qué añadir un móvil frena
  todo el mosaico.
- **Instrucciones del certificado (iPhone/iPad) corregidas:** faltaba el paso de Ajustes →
  General → VPN y gestión de dispositivos → instalar el perfil descargado.

## pack 35 — 2026.11.15
- **Arreglado: «Instalar certificado» (iPhone/iPad) descargaba un archivo vacío.** Si ya usabas
  «Cámara del móvil» antes del pack 24, cam-ui seguía reutilizando el certificado de entonces
  (autofirmado), que aún valía para tu IP: el certificado raíz que hay que instalar en el iPad no
  llegaba a crearse nunca, y la descarga salía vacía («certificado.txt», 0 bytes). Ahora el
  certificado solo se reutiliza si lo firmó ese certificado raíz; si no, se rehace al activar
  «Cámara del móvil» (en Android saldrá una vez más el aviso de "conexión no privada").
- **En iPhone/iPad, el certificado solo se puede instalar desde Safari.** El lector de QR abre el
  navegador predeterminado; si es Chrome (u otro), la página ahora lo avisa arriba y muestra la
  dirección exacta para escribirla o pegarla en Safari. La descarga se llama
  «cam-ui-certificado.cer».

## pack 34 — 2026.11.15
- **Motor nuevo, experimental: cada cámara por separado.** En Ajustes → Avanzado, «Motor nuevo
  (experimental)»; el de siempre sigue por defecto. Hasta ahora un solo ffmpeg abría todas las
  cámaras, una detrás de otra, y con ffmpeg 7/8 solo leía cada una cuando el mosaico se lo pedía:
  una cámara lenta, una que se corta o la de otro PC frenaban a todas. Con el motor nuevo:
  - cada cámara se abre en su propio proceso, **todas a la vez** (cámaras simuladas que tardan
    0,5, 1,4 y 1,4 s en abrirse: imagen de las tres en 1,6 s, frente a 3,6 s una tras otra);
  - **ninguna frena a las demás**: una de 10 fps se ve a 10 y el resto a 30; una que se calla
    3 s repite su última imagen 2 s, luego «Sin señal», y vuelve sola sin reiniciar nada; el
    búfer de la EOS no se llena;
  - **la cámara de otro cam-ui cae a la vez que las de aquí**, por la hora a la que se captó allí,
    aunque los relojes de los dos PCs no coincidan (probado con 543 ms de diferencia, como los
    tuyos): de 0 a +33 ms con ffmpeg 6.1 y 7.0. En la cámara pone «alineada con su PC por la
    hora de captura». Para que sea exacto, el otro PC también tiene que llevar el motor nuevo
    (marca cada fotograma de «En la red» con la hora de lo que se ve); con el de siempre se estima;
  - **reiniciar el mosaico ya no reabre las cámaras** (cambiar un ajuste, empezar o parar de
    grabar): primera imagen en ~0,3 s;
  - **«Cámara del móvil» entra directa**, sin servidor local ni otro ffmpeg de por medio; si el
    móvil manda algo más grande que 1920, se decodifica ya reducido a 1920;
  - el sonido sigue entrando como siempre: medido a -6 ms de la imagen con ffmpeg 6.1 y a +56 ms
    con 7.0 (con el motor de siempre y 7.0, +108 ms). Alinearlo con el mismo reloj es la
    siguiente fase;
  - pausar la vista previa, los diagnósticos, el cuadro del driver y «Probar modos» sueltan las
    cámaras igual que antes.
- **Arreglado, también con el motor de siempre: con ffmpeg 7/8, «En la red» y la cámara virtual
  salían a trompicones.** ffmpeg 7/8 saca todas las salidas al ritmo de la más lenta, y la vista
  previa va a 20 fps (a 12 grabando): los fotogramas salían de dos en dos o de tres en tres, y
  grabando se perdían ~10 de cada 30 por segundo al pasarlos a JPEG (medido con ffmpeg 7.0: 60 %
  de huecos de 0 ms). Ahora, con esas salidas activas, la vista previa va a su ritmo: 3 % y 0,5
  perdidos por segundo. Seguramente explica parte de la ingesta irregular entre tus dos PCs.
- *Verificado con ffmpeg 6.1 y 7.0 reales, en un solo equipo y con cámaras DirectShow simuladas
  (aquí no hay Windows ni cámaras de verdad): conviene probarlo con las tuyas. Si algo va peor
  que con el motor de siempre, desmarca la opción.*

## pack 33 — 2026.11.15
- **Desactivada la alineación automática entre PCs del pack 32: con ffmpeg 8 estropeaba las
  demás cámaras.** Se validó con ffmpeg 6.1, que lee cada cámara en cuanto tiene un fotograma;
  ffmpeg 7/8 (el que instala cam-ui en Windows) solo lee de cada una cuando el mosaico lo pide, y
  el «reloj común» marca cada fotograma al leerlo. Al adelantar la cámara encadenada, el mosaico
  se quedaba esperándola y dejaba de leer las de este PC. Visto en un equipo real grabando con
  una EOS Webcam Utility y otro PC encadenado: el búfer de la EOS no bajó del 62-79 % en toda la
  grabación (sin esto se vacía en 2-3 s), salió en negro los primeros 5 s y, después, a 1,6
  imágenes por segundo, a saltos de ~600 ms. Ahora una cámara encadenada va exactamente como en
  el pack 31. Queda pendiente una forma de alinearla que funcione con ffmpeg 7/8, probada con él.
- *El PC que envía sigue marcando la hora de cada fotograma (no molesta a nadie: comprobado con
  ffmpeg y con un navegador real).*

## pack 32 — 2026.11.12
- **«Reloj común» entre PCs encadenados.** Con «reloj común», cada fotograma de una cámara de red
  se marcaba con la hora a la que llegaba a este PC, así que una cámara que es otro cam-ui (su
  «En la red») cargaba con todo su viaje y se veía más tarde que las de aquí — medido en una
  grabación con las dos en el mismo mosaico: +88 ms, +275 ms, y más de un segundo, según la vez.
  Ahora:
  - el PC que envía marca cada fotograma con su propia hora y enseña su reloj;
  - el que recibe calcula la diferencia entre los dos relojes (aunque no marquen la misma hora:
    probado con 2,5 s de diferencia) y entrega esa cámara siempre con el mismo retraso, midiendo
    lo que tarda en llegar (los tirones de la wifi desaparecen: probado con 0-80 ms al azar,
    entregados a ±1 ms);
  - y le adelanta justo ese retraso más lo que tarda el ffmpeg del otro PC.
  Medido igual que antes: desfase de -9 a +24 ms (±1 fotograma). Es automático, solo con
  «reloj común», y solo para cámaras que son otro cam-ui de esta versión o posterior (las demás
  cámaras de red, como un móvil, quedan igual). En la cámara, junto a «recibiendo…», pone
  «alineada con su PC (se adelanta X ms)».
- De paso: si el PC que envía se reinicia un momento (p. ej. al empezar a grabar), el que recibe
  repite su último fotograma hasta 8 s en vez de darla por caída.
- *Lo que no se puede medir desde el PC que recibe (lo que tarda el ffmpeg del otro de su
  cámara a su salida en red) se calcula: ~3 fotogramas, calibrado con ffmpeg real a 15 y 30 fps.
  Verificado en un solo equipo, sin wifi real ni cámaras DirectShow: conviene confirmarlo con tus
  dos PCs.*

## pack 31 — 2026.11.12
- **«En la red» conserva la fluidez del original.** Iba siempre a 10 fps, para ahorrar wifi:
  otro cam-ui que la encadenara recibía un vídeo de 30 fps a 10. Ahora va a los mismos fps que el
  mosaico (hasta 30).
- **Y llega a ritmo regular.** Quien lo veía (navegador u otro cam-ui) miraba cada 40 ms si había
  fotograma nuevo: aun subiendo los fps, a 30 solo llegaban unos 22, a saltos, e incluso a 10 los
  huecos iban de 88 a 162 ms. Ahora se le avisa en cuanto hay uno: medido, llegan 29,8-30 fps,
  uno cada 33 ms.
- **Más calidad: JPEG 85 en vez de 70.** Medido con una grabación real de cam-ui: a 70 se parece
  al original un 0,84 (SSIM, 1 = idéntico), a 85 un 0,94. Cuesta un 58 % más de datos; codificar
  apenas cuesta CPU. Lo que sí sube es la wifi: con el tamaño por defecto (hasta 640×360), unos
  13 Mbit/s a 30 fps; a 1280 de ancho, unos 40.
- *Verificado con ffmpeg e IpServer reales en un solo equipo, sin wifi de por medio.*

## pack 30 — 2026.11.11
- **Arreglado: «En la red» añadía barras negras y cambiaba el formato del mosaico.** Siempre
  encajaba el mosaico en un formato fijo 16:9 y rellenaba con negro lo que sobraba: un mosaico
  4:3 de una sola cámara llegaba con bandas negras a los lados, y uno apaisado de dos cámaras
  con bandas arriba y abajo — tanto a quien lo viera en el navegador como a otro cam-ui que lo
  usara como «cámara de red», que además lo tomaba como si esa cámara fuera 16:9. Ahora se manda
  con la misma forma que el mosaico de verdad (con su encuadre), sin añadir nada: medido con el
  comando real, 1280×960 llega como 480×360 y 1280×548 como 640×274, sin una sola fila ni
  columna negra (antes: 160 columnas y 86 filas negras). El «Tamaño» de «En la red» pasa a ser
  un máximo («hasta 640×360», etc.): para más resolución, basta con elegir uno mayor.
- **Quitada la opción «Automático» del pack 29.** Atacaba la resolución, solo con una cámara y
  solo si se elegía a mano — no el problema real, que era este relleno de negro (y que pasaba
  igual con varias cámaras). Quien la tuviera elegida pasa al máximo más grande (hasta
  1920×1080), que es lo que buscaba.
- Si el PC que envía cambia su mosaico (p. ej. añade una cámara) mientras otro lo está
  recibiendo, el receptor sigue recibiendo sin cortarse (probado con su comando real), pero ve
  el formato nuevo encajado en su celda anterior hasta que su vista previa se reinicia (p. ej.
  al pausarla y reanudarla), que es cuando se entera del tamaño nuevo y se reajusta.
- *Verificado con ffmpeg e IpServer reales, no con dos PCs de verdad encadenados — conviene
  confirmarlo en tu caso.*

## pack 29 — 2026.11.11
- **Nueva opción «Automático» en Ajustes → Salida → «En la red» → Tamaño, para cuando se usa
  para encadenar otro cam-ui.** Hasta ahora, «En la red» siempre reducía el mosaico a un tamaño
  fijo (640×360 por defecto) para ahorrar wifi/CPU al verlo en directo — un tamaño aparte, sin
  relación con el tamaño real del mosaico/grabación. Eso está bien para verlo en un navegador,
  pero al encadenar un PC a otro (añadiendo «En la red» de uno como «cámara de red» del otro,
  algo que cam-ui ya permitía) ese tamaño reducido se notaba de más: una cámara real a buena
  resolución acababa llegando al otro equipo a 640×360, sin que hubiera forma de evitarlo.
  Ahora, con una sola cámara marcada en el mosaico, «Automático» manda esa cámara tal cual —
  con el mismo recorte, zoom, giro y ancho que ya se le aplicó en «Encuadre» — en vez de un
  tamaño aparte. Con más de una cámara en el mosaico no hay «la cámara» de la que partir, así
  que se comporta exactamente como el tamaño recomendado de siempre (640×360); esto es a
  propósito, no una limitación pendiente de arreglar — encadenar un mosaico de varias cámaras
  es un caso distinto, sin una respuesta tan clara de qué tamaño «automático» tendría sentido.
- *Como siempre: verificado con pruebas (incluida una que monta un IpServer de verdad), no con
  dos PCs reales uno encadenado al otro — conviene probarlo en tu caso real.*

## pack 28 — 2026.11.11
- **Arreglado: un bucle de caídas de ffmpeg («ffmpeg se detuvo de forma inesperada») al tener
  una cámara de red que no responde (o tarda) junto a cámaras locales reales en el mosaico.**
  ffmpeg abre todas las cámaras una tras otra ANTES de empezar a procesar nada; una cámara de
  red que acepta la conexión pero no contesta (wifi floja, dispositivo apagado, una app
  bloqueada...) podía dejar a ffmpeg esperando sin ningún límite mientras una cámara local de
  verdad (p. ej. una EOS Webcam Utility) seguía entregando fotogramas sin que nadie los
  consumiera todavía: su búfer en tiempo real acababa desbordándose y tirando abajo TODO el
  proceso — y, como el error resultante no es el típico «no se pudo abrir tal cámara», cam-ui
  no sabía reconocerlo ni recuperarse solo, así que el mosaico se quedaba cayendo y
  recuperándose en bucle (los botones de la ventana apareciendo y desapareciendo una y otra
  vez). Ahora cada cámara de red se prueba aparte, con un margen corto, justo antes de arrancar
  el mosaico de verdad — de forma automática, cada vez: al abrir la vista previa, al grabar, y
  en cada reconexión —; la que no responda a tiempo arranca de entrada como «Sin señal:
  nombre», igual que si ffmpeg ya hubiera fallado al abrirla, sin bloquear ni un instante a las
  demás. También se le ha puesto un tope de tiempo a toda cámara de red ya integrada en el
  mosaico (no solo en esta prueba previa), por si deja de responder a mitad de una sesión en
  vez de al principio.
- Cierra el pendiente del pack anterior: «añadir una prueba de red antes de integrar la
  ingesta de un dispositivo nuevo».
- *Como siempre: sin las cámaras de red reales del aviso (un ESP32-CAM, otro PC con cam-ui) ni
  una EOS Webcam Utility real para reproducir el síntoma tal cual se reportó, esto se ha
  verificado contra la causa real identificada en el código — incluido un servidor de prueba
  real que reproduce justo «acepta la conexión y no contesta», y pruebas que fallan de verdad
  sin el arreglo y pasan con él — conviene confirmar en tu equipo que el bucle de caídas no
  vuelve a aparecer.*

## pack 27 — 2026.11.11
- **«Cámara del móvil» ya no tiene interruptor: siempre está activa.** El botón «Añadir un
  dispositivo móvil como cámara» de Ajustes desaparece — sin ningún móvil conectado, tenerla
  activa apenas cuesta nada (solo escucha en su puerto, igual que «En la red»), así que el
  interruptor solo añadía un paso confuso sin ahorrar recursos de verdad. Ahora, en cuanto se
  abre cam-ui, ya se puede entrar desde el móvil directamente — sin tener que ir antes a
  Ajustes a encenderla a mano.
- **Lista de direcciones más completa, y cada una clicable.** Tanto «Cámara del móvil» como «En
  la red» mostraban una sola dirección (o varias, pero juntas en una sola línea de texto). Ahora
  se ven TODAS las direcciones donde puede responder este equipo (una por cada tarjeta de red, y
  el nombre del equipo al final), cada una en su propia línea y clicable de verdad: un clic la
  abre en el navegador de este mismo PC, para comprobar rápido que responde — útil si una
  dirección no funciona desde el móvil (p. ej. una IP de una red virtual) y hay que probar con
  otra. También se ha hecho clicable la dirección de respaldo que aparece en los diálogos del
  código QR (de «Cámara del móvil» y del certificado para iPhone/iPad) cuando falta el paquete
  «qrcode» y no hay imagen que mostrar.
- Sigue pendiente, de un aviso anterior: revisar si la variación de resolución de una cámara de
  red puede deberse también a sincronía/fps inestables (no solo al tamaño en píxeles, ya resuelto
  en el pack 26), y añadir una prueba de red antes de integrar la ingesta de un dispositivo nuevo
  — se tendrá en cuenta al trabajar el sonido del móvil/iPad, todavía sin implementar.

## pack 26 — 2026.11.11
- **Arreglado: una cámara de red (el móvil, otro PC encadenado por «En la red», una app tipo IP
  Webcam...) podía dejar el mosaico reiniciándose solo sin parar, como atascado.** La causa:
  cada vez que ffmpeg informaba del tamaño real de esa cámara, cam-ui lo comparaba píxel a
  píxel contra el que ya conocía y, si no coincidía EXACTO, reiniciaba todo el mosaico para
  encajarlo de nuevo -- pero una cámara de red puede anunciar un tamaño ligerísimamente distinto
  de una reconexión a otra (redondeo del propio códec MJPEG, un navegador pidiendo una
  resolución "ideal" en vez de una exacta...) sin que de verdad cambie nada que afecte al
  mosaico. Si ese jitter se repetía en cada reconexión, el mosaico no llegaba nunca a
  asentarse: se quedaba todo el rato "Ajustando el mosaico al formato real de las cámaras…",
  sin responder a nada mientras tanto. Ahora solo se reinicia si la FORMA de verdad cambia (más
  de un 3%, lo mismo que ya se usaba para encajar sin franjas) -- un cambio real (p. ej. girar
  el móvil de apaisado a vertical) sigue reajustando el mosaico como siempre.
- El diálogo de «Añadir/editar cámara de red» avisa ahora de que, si el origen es otro PC con
  cam-ui y va lento, se puede bajar la resolución que manda desde Ajustes → «En la red» → tamaño
  de ESE otro PC (no hay forma de pedirle a una cámara de red cualquiera que mande menos
  resolución desde aquí).
- *Como siempre: sin un segundo PC ni un móvil reales en wifi para reproducir el síntoma exacto
  tal cual se describió, esto se ha verificado contra la causa real identificada en el código
  (con pruebas que fallan de verdad sin el arreglo y pasan con él) -- conviene confirmar que
  esto resuelve el caso real.*

## pack 25 — 2026.11.11
- **Más fluido y con mejor resolución**: la cámara del móvil ahora pide 1080p (antes 720p) a
  20 fps (antes 8), con algo más de calidad de imagen. Es seguro subirlo así porque el propio
  navegador del móvil ya se autolimitaba solo (si una foto tarda en enviarse, se salta la
  siguiente en vez de acumular retraso) — en una wifi floja o un móvil viejo, sencillamente baja
  solo de fluidez, en vez de atascarse o acumular retraso.
- **Un móvil ya emparejado no hace falta volver a "emparejarlo"**: hasta ahora, cada vez que se
  apagaba y encendía «Cámara del móvil» (o se cerraba cam-ui del todo), cualquier móvil tenía que
  volver a leer el código QR desde cero. Ahora, la primera vez que un móvil se empareja guarda un
  código propio (en el propio móvil, en su navegador); la próxima vez que abra esa misma
  dirección, entra solo, sin QR de por medio. *Para que quede claro el cambio de fondo*: de ahí en
  adelante, ese móvil concreto entra sin que nadie tenga que confirmar nada desde el PC — antes,
  el QR en la pantalla del PC era precisamente lo que impedía que cualquiera en la wifi se
  añadiera solo. En Ajustes → Cámara del móvil hay un botón nuevo, «Dispositivos recordados», para
  ver cuáles son (con la fecha en que se emparejaron) y «olvidar» cualquiera — a partir de ahí, ese
  móvil vuelve a necesitar el QR de siempre, como si fuera nuevo.
- El certificado para iPhone/iPad del pack anterior no cambia con nada de esto.
- Queda pendiente, como su propio cambio aparte (más grande y con más riesgo: Safari no admite de
  forma fiable los mismos formatos de audio que Chrome/Android): que la cámara del móvil también
  mande sonido.
- *Como siempre: sin un móvil real ni un PC con wifi de verdad en este entorno, esto se ha probado
  a fondo con sockets y servidores HTTP/HTTPS reales (incluido un cliente TLS real y la ventana
  real con Tk) pero no con hardware real por wifi — conviene confirmarlo en el uso normal.*

## pack 24 — 2026.11.11
- **Arreglado: en iPhone/iPad, «Cámara del móvil» no dejaba usar la cámara en absoluto** (ni
  siquiera llegaba a pedir el permiso). La causa: Safari/WebKit, a diferencia de Chrome en
  Android, no deja usar la cámara (getUserMedia) a través de un certificado autofirmado normal,
  por mucho que se acepte el aviso de «conexión no privada» -- eso basta para ver la página,
  pero no para la cámara. Ahora cam-ui genera su propio certificado raíz (CA) una sola vez, y en
  Ajustes → Cámara del móvil hay un botón nuevo, «Instalar certificado (solo iPhone/iPad)», con
  su propio código QR: solo hace falta instalarlo una vez en el iPhone/iPad (Ajustes → General →
  Información → Confianza de certificados) y, desde entonces, la cámara del móvil funciona ahí
  igual que en Android. En Android no cambia nada, no hace falta tocar esto.

## pack 23 — 2026.11.08
- **Arreglado: la vista previa de la cámara del PC podía quedarse en negro para siempre, sin
  ningún error, mientras la barra de estado seguía diciendo tranquilamente «Vista previa en
  marcha».** La causa: al arrancar, cam-ui solo comprueba que ffmpeg no se haya caído en el
  primer segundo y medio para darlo por «arrancado» — no que de verdad haya llegado a entregar
  ni un fotograma. Si una cámara se queda colgada negociando con ffmpeg (sin llegar a fallar
  del todo, solo sin avanzar — típico de cámaras lentas en iniciar, como el software de una
  réflex/EOS, o de un atasco con otra cámara del mosaico), el proceso queda vivo para siempre
  sin producir nada, y el aviso de «ffmpeg se detuvo solo» nunca llega a dispararse porque,
  técnicamente, no se ha detenido. Ahora, si pasan más de 15 segundos sin recibir ni un
  fotograma, cam-ui para esa vista previa por su cuenta y avisa con un mensaje claro
  («Vista previa colgada»), sugiriendo desmarcar cámaras una a una para encontrar cuál es la
  que se atasca, o pulsar F5 para reintentar. *Importante: no se ha podido reproducir este
  cuelgue de verdad en un PC con varias cámaras físicas (no hay ninguno disponible para probar
  en este entorno) — el arreglo está pensado y probado contra el síntoma exacto que se
  describió (vista previa en negro + estado contradictorio, sin cambios de código deliberados
  para forzarlo), pero conviene confirmar que esto resuelve el caso real.*

## pack 22 — 2026.11.08
- **El instalador de Windows tampoco ponía `cryptography` ni `qrcode`** — el arreglo del pack
  21 cubría a quien ya tenía cam-ui instalado y se había ido actualizando solo, pero quien
  instalara cam-ui *desde cero* con el instalador oficial se iba a encontrar exactamente el
  mismo problema, porque esos dos paquetes nunca estuvieron en su lista. Ya están añadidos
  (igual que `pystray`/la cámara virtual: si fallan al instalarse no tira abajo el resto de la
  instalación, solo avisa — cam-ui ya sabe ofrecer instalarlos después igualmente).
- **El QR de emparejamiento de «Cámara del móvil», sin el paquete `qrcode`, se quedaba para
  siempre solo con la dirección en texto** (incómoda de teclear a mano en el móvil), sin forma
  de arreglarlo desde ahí. Ahora el propio diálogo del QR trae un botón «Instalar «qrcode» para
  ver el código» — si sale bien, se vuelve a mostrar el mismo QR al momento, esta vez con su
  imagen real.

## pack 21 — 2026.11.08
- **Arreglado: «Cámara del móvil» podía fallar con «No se pudo activar. No module named
  'cryptography'», un mensaje interno de Python que no decía nada útil — y sin ese paquete, el
  servidor no llegaba a arrancar en absoluto, así que ningún móvil llegaba siquiera al aviso
  de «conexión no privada», en ningún navegador.** La causa: `cryptography` (el paquete que
  genera el certificado HTTPS) se añadió como dependencia nueva cuando se creó esta función,
  pero la auto-actualización de cam-ui solo cambia el programa (`camui.py`), nunca instala
  paquetes de Python nuevos — así que quien lleva un tiempo actualizándose solo, sin reinstalar
  cam-ui desde cero, podía quedarse sin él sin enterarse. Ahora, igual que ya pasa con
  «pystray» o «pyvirtualcam», cam-ui lo detecta y ofrece instalarlo con un clic (necesita
  internet); si se acepta y sale bien, enciende la función sola a continuación, sin tener que
  volver a pulsar nada.

## pack 20 — 2026.11.08
- **Arreglado: en «En la red», el aviso de Chromecast decía que el navegador del móvil
  «debe» traer la opción «Convertir en Chromecast», pero muchos navegadores propios de
  fabricante (como el de Xiaomi/MIUI) no la traen y no hay ninguna opción que dar — el aviso
  dejaba sin ninguna salida a quien le pasaba justo eso.** Ahora el aviso nombra el caso
  (Firefox, Safari, y también navegadores de fabricante como el de Xiaomi) y da dos
  alternativas que sí funcionan: abrir esa misma dirección con Chrome, o hacerlo desde el PC
  con cam-ui («Abrir en VLC» → Renderizador).
- **Nuevo: una casilla oculta «vigilar sola, sin avisos» para quien quiera que cam-ui se
  actualice solo en cuanto salga una versión nueva, sin esperar a cerrar y volver a abrir el
  programa.** Con el aviso normal («avisarme al abrir») o con «actualizar sola» (pack
  anterior), cam-ui solo mira si hay algo nuevo una vez, al arrancar. Esta casilla añade un
  vigilante que sigue mirando cada minuto mientras el programa esté abierto, y si encuentra
  una versión nueva la instala y reinicia sola — sin preguntar nada, sin avisar siquiera de
  que se va a guardar una copia antes (se sigue guardando igual que siempre, solo que sin
  decirlo), y sin abrir la ventana de Actualizaciones para nada.
  - Por ser algo tan silencioso, está escondida a propósito: no se ve en la ventana
    «Actualizaciones» a menos que, con esa ventana abierta y con el foco, se pulse
    **Mayús + Ctrl + Win + Alt + F12**. Una vez que se activa se queda visible (para poder
    volver a apagarla sin tener que repetir el atajo); al apagarla, se esconde de nuevo al
    instante.
  - **Aviso honesto:** la parte de la tecla «Win» del atajo no se ha podido probar en un
    Windows de verdad desde este entorno (aquí solo hay Linux) — Tk no tiene una forma
    estándar de reconocer esa tecla igual en todos los sistemas, y Windows puede interceptarla
    él mismo en ciertos casos. Si el atajo no responde en tu PC, es lo primero a revisar.

## pack 19 — 2026.11.08
- **Arreglado: el botón «Añadir cámara del móvil» (pack 18), con esa función apagada, podía
  acabar en «No se pudo encender. Reintentar» aunque sí se encendiera.** La causa: solo se
  esperaban 4 segundos antes de rendirse, pero la primera vez que se activa en todo el PC (o
  si cambió la IP detectada) hace falta generar antes un certificado propio nuevo, y eso puede
  tardar más de 4 segundos de verdad en un PC más lento — aunque siguiera encendiéndose bien
  en cuanto terminaba, el aviso ya había llegado. Ahora espera hasta 15 segundos, y si se
  alarga el botón lo dice («…la primera vez puede tardar un poco») en vez de parecer colgado.
  - Si ya te pasó esto: pulsar «Reintentar» sin más debería haber funcionado igualmente — el
    encendido sigue en marcha por detrás aunque la primera petición se rinda antes de verlo.

## pack 18 — 2026.11.08
- **El botón «Añadir cámara del móvil» de «En la red» (pack 16) ya no dice «Actívala en
  Ajustes, en el PC» si esa función está apagada: ahora la enciende él solo**, al momento, y
  lleva directo a «/add» — nunca hace falta ir antes a la pestaña Ajustes a mano. Sigue
  protegido igual que siempre: aunque cualquiera de la wifi pueda encenderla así, emparejar un
  móvil de verdad sigue haciendo falta ver el QR en la propia pantalla del PC (ver el punto
  siguiente), así que solo puede hacerlo quien esté delante de ella.
- **El QR de emparejamiento aparece ahora también en un diálogo flotante, al instante, encima
  de lo que sea que haya abierto en ese momento** — antes solo se veía dentro de la pestaña
  «Ajustes»; si no se estaba ya ahí, parecía que pulsar el botón del móvil no había hecho nada.
  Se cierra solo en cuanto ese móvil se empareja o si nadie lo lee a tiempo, con su propio
  botón «Cerrar» además.
- **En «/add», el aviso de arriba («Pidiendo permiso de cámara…», luego «Busca con la cámara
  el código QR…») oscurecía más de la cuenta la imagen en directo que hay justo debajo** — se
  ha aligerado para que se vea mejor la cámara mientras se busca el QR.

## pack 17 — 2026.11.08
- **Arreglado (en «En la red», viéndolo de verdad desde un Android o un iPad): la barra de
  botones de abajo quedaba cortada o se salía de la pantalla, y en el iPad no se veía ningún
  botón.** La causa: `100vh` en el móvil cuenta el hueco de la barra del propio navegador
  aunque esté oculta en ese momento, así que la barra de cam-ui, pegada "al fondo" de una
  pantalla más alta de lo que de verdad se ve, quedaba por debajo de lo visible. Ahora usa el
  alto real visible (`100dvh`), los botones se envuelven en más de una fila si no caben en una
  sola, y se deja hueco para la barra de gestos/el borde redondeado del propio aparato.
- **El botón «Añadir cámara del móvil» (pack 16) ahora está siempre a la vista** en «En la
  red», encendida o no esa función desde el PC — antes se escondía del todo si no lo estaba, y
  entonces parecía que el botón ni existía. Si no está encendida, el propio botón lo dice al
  pulsarlo, en vez de llevar a una dirección que todavía no existe.
- **La página de «/add» ya pide la cámara directamente al cargar**, sin tener que tocar antes
  el botón de «Escanear». Si el navegador la deniega, o si falla por cualquier otro motivo,
  aparece un botón de «Reintentar» (ese sí, con un toque, como pide cualquier navegador para
  volver a preguntar tras una negativa).
- **Renombrado: "En la red" pasa a ser "cam-ui@‹dirección del PC›"** en los sitios donde ese
  nombre se ve desde fuera del propio PC — el título de la pestaña del navegador del visor, el
  enlace de vuelta en la página de «esta dirección no existe», y el nombre que la propia tele
  mostrará al enviarlo por Chromecast (antes no mandaba ningún nombre). Así, si hay más de un
  cam-ui en la misma wifi, se distingue cuál es cuál. Dentro de la propia ventana de cam-ui, en
  el PC, sigue llamándose «En la red» como siempre — esto solo cambia lo que ven otros aparatos.
  - Lo de la tele por Chromecast no se ha podido comprobar con un Chromecast de verdad (no hay
    ninguno en este entorno); está hecho con la misma API de metadatos de Google Cast que usa
    cualquier app, pero queda pendiente de confirmar en un televisor real.

## pack 16 — 2026.11.08
- **Arreglado: pedir «/add» en la dirección equivocada (o cualquier otra que no existe) hacía
  que Safari o Chrome en el iPhone ofrecieran "descargar" un archivo vacío con un nombre
  inventado, en vez de avisar sin más de que esa dirección no existe.** La causa: esa respuesta
  se servía sin ningún `Content-Type` ni cuerpo, y así es como algunos navegadores de móvil
  tratan una página que no saben interpretar. Ahora da un 404 normal, con su página y un enlace
  de vuelta.
  - La confusión más probable que llevaba hasta ahí: «En la red» (ver el mosaico desde el
    móvil) y «Cámara del móvil» (añadir el móvil como cámara, ver pack 15) usan direcciones con
    puertos distintos y parecidos (terminan en 4 y en 5) — fácil de mezclar al teclearla a mano.
  - Para que no haga falta acordarse de ningún puerto nuevo: si la página de «En la red» ya
    está abierta en el móvil, ahí aparece un botón **«Añadir cámara del móvil»** que lleva
    directo a la dirección de «/add» correcta, de un toque.
  - La propia página de «/add» tenía un párrafo largo explicando lo que iba a pasar antes de
    siquiera tocar nada; se ha quitado, y se deja solo un botón sencillo («Escanear código QR»).
  - De paso, arreglado otro fallo que llevaba ahí desde antes (en la página de «En la red»): al
    pulsar «Activar sonido», el icono de silenciar y el control de volumen no llegaban a verse
    nunca, aunque el sonido sí sonaba — por el mismo motivo de fondo (un valor que no bastaba
    para contradecir una regla de estilo). Ahora sí aparecen.

## pack 15 — 2026.11.08
- **Rehecho: cómo se añade la cámara de un móvil (ver pack 14).** Antes, activarlo dejaba en
  pantalla una dirección fija con un código de sesión de un solo uso, pensada para no adivinarse
  a la ligera — pero esa dirección había que escribirla a mano (o leerla de un QR ya mostrado) sin
  más control que el propio código. Ahora el emparejamiento es en dos pasos:
  - El botón de Ajustes (ahora «Añadir un dispositivo móvil como cámara») muestra una dirección
    **fija**, sin ningún código: `http://<ip o nombre del PC>:<puerto>/add`. Por sí sola esa
    dirección no añade nada: solo abre, en el navegador del móvil, el típico recuadro de lectura
    de QR (pide permiso de cámara para ello).
  - En cuanto el navegador concede ese permiso, en la propia ventana del PC aparece un QR nuevo,
    de un solo uso. El móvil lo lee con su misma cámara (ya en la página de `/add`, sin salir de
    ella) y, al reconocerlo, navega él solo a la captura — sin teclear nada.
  - La seguridad ya no depende de un código difícil de adivinar, sino de algo más simple: **hay
    que estar delante de la pantalla del PC** para ver el QR con el que emparejar. Nadie más en la
    wifi puede añadirse sin que el propietario lo vea ocurrir.
  - El QR de emparejamiento se retira solo de la ventana en cuanto el móvil lo lee (la cámara ya
    apareció en la lista) o, si nadie lo escanea, a los 90 segundos.
  - La lectura de QR la hace el propio navegador del móvil con una librería (`jsQR`, con su
    licencia) incluida en cam-ui — sin conexión a internet ni servicios externos, igual que el
    resto de esta función.
  - El resto sigue igual que en el pack 14: HTTPS con certificado propio (mismo aviso de "conexión
    no privada" la primera vez), solo vídeo por ahora, y no se guarda entre reinicios.
  - Probado de punta a punta con un navegador real (Chromium) leyendo un QR de verdad con una
    cámara simulada — incluida la propia lectura por `jsQR` y la navegación automática resultante
    — y con ffmpeg leyendo la imagen final tal cual lee cualquier otra cámara de red; queda
    pendiente confirmarlo con un Android o un iPhone de verdad.

## pack 14 — 2026.11.08
- **Nuevo: «Cámara del móvil», en Ajustes.** Se puede usar la cámara de un Android como una
  cámara más del mosaico sin instalar nada en el teléfono: al activarlo, cam-ui muestra una
  dirección (y, si está instalado el paquete opcional `qrcode`, también un código QR) para abrir
  en el navegador del móvil. Tras conceder el permiso de cámara, el teléfono empieza a mandar
  imagen y la cámara **aparece sola** en la lista, ya marcada y lista para grabar — sin tocar
  nada más en el PC.
  - Es necesario que el navegador del móvil pida el permiso de cámara por HTTPS (lo exige el
    propio Android/Chrome), así que cam-ui genera un certificado propio para esa dirección; la
    primera vez el navegador avisará de que la conexión "no es privada" — es ese certificado
    propio, no un fallo: en Chrome basta con «Avanzado → Continuar» una sola vez.
  - Por ahora **solo vídeo** (sin el micrófono del móvil); puede añadirse más adelante sin tener
    que rehacer lo de ahora.
  - La dirección lleva un código de un solo uso por sesión: a diferencia de «En la red» (que
    solo deja ver), esta función puede añadir una cámara de verdad a la grabación, así que la
    dirección no se adivina a la ligera.
  - Si el teléfono se queda sin mandar imagen (wifi cortada, navegador cerrado…) la cámara pasa a
    "Sin señal" como cualquier otra, y vuelve sola si el móvil reconecta. No se guarda entre
    reinicios de cam-ui (la dirección es de esa sesión), así que hay que volver a abrir el
    enlace en el móvil cada vez.
  - Probado de punta a punta con un navegador real (Chromium, con una cámara simulada) y con
    ffmpeg leyendo la imagen tal cual lee cualquier otra cámara de red; queda pendiente
    confirmarlo con un Android de verdad.

## pack 13 — 2026.11.07
- **Arreglado: el icono nativo de Chromecast aceptaba la orden de reproducir pero la tele se
  quedaba sin imagen, sin avisar de ningún error.** La causa: a los ojos de Google, la tele pide
  la lista y los trozos de vídeo directamente a cam-ui (no a través del navegador), y eso cuenta
  como una petición «de otro origen» — sin las cabeceras CORS que ese caso exige, algunos
  receptores aceptan la orden (de ahí que pareciera que iba a reproducir) pero luego no consiguen
  bajarse nada, y se quedan así sin decir por qué. Ya van las cabeceras que hacían falta.
- **También: pista de audio muda añadida al HLS** (algunos Chromecast, sobre todo los más
  antiguos como el de 1.ª generación, pueden quedarse igual de callados ante un vídeo sin NINGUNA
  pista de sonido) y **un aviso en pantalla si, aun así, la tele no consigue reproducirlo** — el
  mismo hueco donde antes aparecía la ayuda del botón, ahora en rojo, con el motivo que de verdad
  reporta el Chromecast. Si vuelve a fallar, ese mensaje es la pista que hace falta para seguir
  afinándolo.

## pack 12 — 2026.11.07
- **El icono de verdad de Chromecast, en la propia página de «En la red».** El botón «📺
  Chromecast» del pack anterior pasa ahora a ser el icono nativo de Chromecast (el mismo de
  YouTube) cuando el navegador lo trae integrado (Chrome o Edge, con internet): un clic, eliges
  la tele, y ya está — sin menús que explicar. Por dentro, cam-ui enciende un segundo ffmpeg
  aparte (H.264 por HLS, el formato que el Chromecast entiende de fábrica) *solo* mientras se
  está viendo en la tele; el MJPEG de siempre para el navegador no cambia en nada ni se ve
  afectado. De momento va sin sonido (como las cámaras de red). Si el navegador no trae ese
  icono integrado (sin internet, u otro navegador), se queda el botón de ayuda del pack
  anterior, explicando el «Convertir en Chromecast» de Chrome/Edge — nunca se queda sin nada
  que hacer.

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
