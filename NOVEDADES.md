# Novedades de cam-ui

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
