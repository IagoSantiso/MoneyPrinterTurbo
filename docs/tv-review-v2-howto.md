# Generar un short de review de TV (v2 — producción)

Guía rápida para generar los siguientes 3-4 vídeos tú mismo, con la
configuración validada: guion hook/beneficios/CTA + fotos reales
animadas por IA (MiniMax H3 vía WaveSpeed) + tu voz + subtítulos.

## Antes de generar: sube las fotos y añade la TV al catálogo

1. Sube las fotos de la TV a R2 siguiendo `docs/r2-mobile-upload-guide.md`
   (carpeta `MARCA_MODELO/`, nombres de archivo tal cual, sin renombrar).
2. Añade la fila de la TV a tu Google Sheet, con su
   `product_images_prefix` apuntando a esa misma carpeta (`MARCA_MODELO/`).

Si usas el catálogo en vivo (`tv_review_specs_source = "google_sheets"`,
ver más abajo), este paso ya es suficiente: la CLI y la WebUI ven la fila
nueva en la siguiente lectura, sin tocar nada más. Si sigues usando el
snapshot local (`local_json`, el modo por defecto hoy), tienes que
*además* añadir la TV a mano a `resource/tv_specs/sheet_import_20260830.json`
(o al fichero que uses) — ese fichero es un export puntual, no una copia
que se mantenga sola.

## Catálogo de TVs: Sheet en vivo vs. snapshot local

Por defecto (`tv_review_specs_source = "local_json"` en `config.toml`),
tanto la CLI como la página "TV Review Lab" de la WebUI leen
`resource/tv_specs/*.json` — un export manual del Sheet. Cambios
posteriores en el Sheet (filas nuevas, ediciones, un `product_images_prefix`
nuevo) no llegan ahí hasta que alguien vuelve a exportar.

Para leer el Sheet directamente (recomendado si editas el catálogo a
menudo), configura `[google_sheets]` en `config.toml`:

```toml
[app]
tv_review_specs_source = "google_sheets"

[google_sheets]
sheet_id = "..."              # el ID de la URL del Sheet
credentials_path = "..."      # service account con acceso Viewer (ver abajo)
```

Revisa también `[google_sheets.columns]` — mapea los campos de `TVSpecs`
a los nombres reales de las columnas de tu Sheet (fila 1); los valores
por defecto son una suposición razonable, no una garantía. Detalles de
cómo crear la service account y compartir el Sheet con ella, y del modo
alternativo sin credenciales (Sheet publicado/enlace público), en los
comentarios de `config.example.toml` junto a `[google_sheets]`.

La página "TV Review Lab" de la WebUI (`webui/pages/1_TV_Review_Lab.py`)
deja elegir "Google Sheet en vivo" o "Snapshot local" en cada sesión,
independientemente de este valor por defecto, con un botón para forzar
una relectura inmediata del Sheet.

## Generar el vídeo

```bash
uv run python cli.py \
  --tv-review-specs "Marca:Modelo" \
  --tv-specs-file resource/tv_specs/sheet_import_20260830.json \
  --video-language "es-ES" \
  --voice-name "es-ES-AlvaroNeural-Male" \
  --tv-product-animation-method wavespeed \
  --video-aspect 9:16 \
  --video-count 1
```

- `--tv-review-specs "Marca:Modelo"` debe coincidir exactamente con los
  campos `brand`/`model` del JSON.
- `--tv-product-animation-method wavespeed` es lo que activa la v2 (fotos
  animadas por IA). Si lo omites, usas la v1 gratis (Ken Burns + Pexels).
- Sin esta flag, o si falla la animación de alguna foto puntual, cae
  automáticamente a Ken Burns — nunca se rompe la generación por un fallo
  de la IA en una sola foto.

El vídeo tarda ~8-10 min (animación de 5 fotos + montaje) y sale en
`storage/tasks/<task_id>/final-1.mp4`.

## Coste esperado

~0.10-0.15 USD por foto animada con MiniMax H3 → **~0.50-0.75 USD por
vídeo de 5 fotos**. Revisa saldo en wavespeed.ai si ves fallos de
"Insufficient credits".

## Modelo de animación (por si quieres comparar otra vez)

Configurado en `config.toml` → `wavespeed_image_to_video_model`. Modelos
probados con dinero real, mismo clip de referencia:

| Modelo | Precio (6s) | Calidad | Nota |
|---|---|---|---|
| `wavespeed-ai/minimax-h3/image-to-video` (actual) | $0.12 | Suficiente | 480p, sin audio (se silencia solo) |
| `alibaba/wan-3.0/image-to-video` | $0.57 | Buena, movimiento variado | 720p, hasta 30s |
| `bytedance/seedance-2.0-fast/image-to-video` | $1.44 | Floja (rotación estática) | 720p |

Para cambiar de modelo puntualmente sin tocar la config, pásalo por
código (`model_id=` en `tv_product_animation.animate_product_photos`) o
cambia `wavespeed_image_to_video_model` en `config.toml`.

## Publicación

Manual por ahora, a propósito — sin automatizar subida a TikTok/Shorts
hasta validar 3-4 vídeos y decidir cómo automatizarlo sin disparar
costes. El proyecto ya trae integración con Upload-Post
(`upload_post_*` en `config.toml`) para cuando llegue ese momento.
