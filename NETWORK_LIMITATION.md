# Limitación de red en este entorno (Claude Code remoto)

Este proyecto trabaja en un entorno remoto de Claude Code cuya política de
egress de red (configurada a nivel de organización/entorno, no de sesión)
bloquea el tráfico saliente hacia dominios externos como:

- api.openai.com (y presumiblemente el resto de proveedores LLM/TTS/vídeo)
- api.pexels.com
- TikTok / Instagram / YouTube (publicación)

Cualquier intento de conexión devuelve un 403 en el CONNECT del proxy de
egress, con el motivo "organization policy". Esto **no es un problema de
certificados ni de configuración del proyecto**: es una restricción del
entorno de ejecución en sí, y no se puede desactivar desde dentro de la
sesión (ni cambiando `tls_verify`, ni variables de entorno, ni tocando la
config de httpx/OpenAI).

## Cómo desbloquearlo (para el usuario)

Para poder ejecutar y probar el pipeline de verdad (generar guion con LLM,
descargar clips de Pexels, subir vídeos a redes sociales), hace falta un
entorno con salida a internet completa. Opciones:

1. **Crear un nuevo Environment de Claude Code con una política de red más
   permisiva.** En claude.ai/code, al crear/editar el Environment de este
   repo, hay una opción de política de red (network policy) — cambiarla de
   "restringida" a "sin restricciones" (o añadir los dominios necesarios a
   la allowlist: `api.openai.com`, `api.pexels.com`, `pixabay.com`,
   `coverr.co`, `tiktok.com`, `graph.instagram.com`, `googleapis.com`,
   `upload-post.com`, etc., según los proveedores que uses).
   Documentación: https://code.claude.com/docs/en/claude-code-on-the-web
2. **Ejecutar el proyecto en tu propia máquina en lugar de en este entorno
   remoto.** Es la vía más simple para el testeo real; instrucciones abajo.

## Cómo correrlo tú en tu máquina/red

```bash
git clone https://github.com/IagoSantiso/MoneyPrinterTurbo.git
cd MoneyPrinterTurbo
git checkout claude/prompt-review-5plf4i

# Instala uv si no lo tienes: https://docs.astral.sh/uv/getting-started/installation/
uv sync

cp config.example.toml config.toml
# Edita config.toml y rellena:
#   llm_provider = "openai"
#   openai_api_key = "tu-key"
#   pexels_api_keys = ["tu-key"]
# (Edge TTS no necesita key, ya viene por defecto.)

# Prueba rápida por CLI (sin necesidad de la WebUI):
uv run python cli.py \
  --video-subject "Why cats sleep so much" \
  --video-language "en-US" \
  --video-source pexels \
  --video-aspect 9:16

# O levanta la WebUI:
sh webui.sh        # Linux/macOS
webui.bat          # Windows
# Se abre en http://localhost:8501
```

El `config.toml` con tus keys de prueba está ya preparado en este repo
(está en `.gitignore`, nunca se commitea) — si trabajas sobre esta misma
rama en tu máquina, sólo tienes que copiar tus keys ahí de nuevo.
