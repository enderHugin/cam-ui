# CamWin32

Mosaico de varias cámaras USB más audio L/R en un solo vídeo, para Windows.
Interfaz Tkinter + FFmpeg (DirectShow). Un único archivo: `camui.py`.

## Requisitos
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

## Atajos
F5 vista previa · F8 foto · F9 grabar · F11 normal/compacto · F12 pánico · Esc vuelve al modo normal desde mini.

## Estado
Probado con cámaras simuladas en Linux (Xvfb + ffmpeg). Lo que depende de hardware o de Windows real
(cámaras DirectShow, bandeja, ventana mini, cámara virtual de OBS) se valida en el equipo del usuario.
Pruebas sin hardware: `python tests/unit_modes.py`, `tests/unit_vcam.py`, `tests/geom_test.py` (necesitan ffmpeg en el PATH).
Si algo falla, adjuntar `camui.log` (se crea junto al script).
