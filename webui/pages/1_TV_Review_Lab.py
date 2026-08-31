"""TV Review Lab: pequeña página de pruebas manuales.

Permite elegir una o varias TVs del catálogo importado del Google Sheet
(``resource/tv_specs/*.json``), lanzar la generación real del vídeo con
la pipeline de "TV reviewer" (mismo camino que ``cli.py --tv-review-specs``,
ver ``docs/tv-review-v2-howto.md``) y ver/descargar el resultado, sin tener
que escribir el comando a mano cada vez.

No sustituye al flujo genérico de ``webui/Main.py``: es una capa fina por
encima de los mismos servicios (``app.services.task``/``webui_task``) para
iterar rápido sobre casos de TV concretos durante las pruebas manuales.
"""

import glob
import os
import sys
from pathlib import Path

import streamlit as st

# Igual que en webui/Main.py: al ejecutarse como página independiente, el
# directorio raíz del proyecto debe ir por delante de dependencias de
# terceros que puedan traer su propio paquete "app".
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
if root_dir in sys.path:
    sys.path.remove(root_dir)
sys.path.insert(0, root_dir)

from app.config import config  # noqa: E402
from app.models import const  # noqa: E402
from app.models.schema import VideoAspect, VideoParams  # noqa: E402
from app.models.tv_specs import TVSpecs  # noqa: E402
from app.services import state as sm  # noqa: E402
from app.services import webui_task  # noqa: E402
from app.services.tv_review_script import (  # noqa: E402
    TV_REVIEW_SYSTEM_PROMPT,
    build_tv_review_facts_block,
    build_tv_review_subject,
)
from app.services.tv_specs import (  # noqa: E402
    LocalJSONTVSpecsProvider,
    TVSpecsProvider,
)
from app.utils import utils  # noqa: E402

st.set_page_config(page_title="TV Review Lab · MoneyPrinterTurbo", page_icon="📺")

DEFAULT_SPECS_FILE = config.app.get(
    "tv_review_specs_path", "resource/tv_specs/example.json"
)
DEFAULT_LANGUAGE = "es-ES"
DEFAULT_VOICE_NAME = "es-ES-AlvaroNeural-Male"

ANIMATION_METHOD_LABELS = {
    "ken_burns": "Ken Burns (v1, gratis — pan/zoom sobre la foto estática)",
    "wavespeed": "WaveSpeed AI (v2, ~0.10-0.15 USD/foto — anima la foto con IA)",
}


@st.cache_data(show_spinner=False)
def _discover_specs_files() -> list[str]:
    """Ficheros JSON/CSV disponibles bajo resource/tv_specs/, más recientes primero."""
    patterns = ("resource/tv_specs/*.json", "resource/tv_specs/*.csv")
    files = [f for pattern in patterns for f in glob.glob(os.path.join(root_dir, pattern))]
    files = sorted(set(files), key=os.path.getmtime, reverse=True)
    return [os.path.relpath(f, root_dir) for f in files]


def _load_specs(path: str) -> tuple[list[TVSpecs], str]:
    """Devuelve (specs, error). ``specs`` viene vacío si hubo error."""
    try:
        provider: TVSpecsProvider = LocalJSONTVSpecsProvider(path)
        return provider.list_all(), ""
    except Exception as exc:  # noqa: BLE001 - se muestra tal cual en la UI
        return [], f"{type(exc).__name__}: {exc}"


def _build_params(
    selected: list[TVSpecs],
    comparison_angle: str,
    video_language: str,
    voice_name: str,
    video_aspect: str,
    video_count: int,
    animation_method: str,
    media_method: str,
    product_images_prefix: str,
) -> VideoParams:
    """Réplica de ``cli.py::_apply_tv_review_specs`` para un caso manual de la UI."""
    params = VideoParams(
        video_subject=build_tv_review_subject(selected),
        video_script_prompt=build_tv_review_facts_block(selected, comparison_angle),
        custom_system_prompt=TV_REVIEW_SYSTEM_PROMPT,
        video_language=video_language,
        voice_name=voice_name,
        video_aspect=video_aspect,
        video_count=video_count,
        tv_product_animation_method=animation_method,
        tv_product_media_method=media_method,
    )
    prefix = product_images_prefix.strip()
    if not prefix and len(selected) == 1:
        prefix = selected[0].product_images_prefix
    params.tv_product_images_prefix = prefix
    if len(selected) == 1:
        params.tv_product_animation_brand = selected[0].brand
        params.tv_product_animation_model = selected[0].model
    return params


def _render_task_result(task_id: str, label: str) -> bool:
    """Renderiza el estado/resultado de un task_id ya conocido.

    Devuelve True mientras el task siga en curso (para que el caller decida
    si hace falta seguir haciendo polling).
    """
    task = sm.state.get_task(task_id)
    if not task:
        st.info("Esperando a que arranque la generación…")
        return True

    state = task.get("state")
    progress = max(0, min(100, int(task.get("progress", 0) or 0)))

    if state == const.TASK_STATE_PROCESSING:
        st.progress(progress, text=f"Generando «{label}»… {progress}%")
        return True

    if state == const.TASK_STATE_FAILED:
        error = str(task.get("error") or "").strip()
        stage = task.get("failed_stage", "unknown")
        st.error(f"Falló en la etapa «{stage}»: {error or 'error desconocido'}")
        return False

    video_files = task.get("videos") or []
    if state != const.TASK_STATE_COMPLETE or not video_files:
        st.error("La generación terminó sin producir ningún vídeo.")
        return False

    st.success(f"Vídeo listo para «{label}»")
    for warning in task.get("warnings") or []:
        st.warning(str(warning))
    for i, video_path in enumerate(video_files):
        st.video(video_path)
        if os.path.isfile(video_path):
            with open(video_path, "rb") as f:
                st.download_button(
                    f"Descargar vídeo {i + 1}" if len(video_files) > 1 else "Descargar vídeo",
                    data=f,
                    file_name=f"{task_id}-{i + 1}.mp4",
                    mime="video/mp4",
                    key=f"tv_lab_download_{task_id}_{i}",
                )
    return False


st.title("📺 TV Review Lab")
st.caption(
    "Pruebas manuales de la pipeline de review/comparativa de TVs: elige TVs "
    "del catálogo importado del Google Sheet, genera el vídeo real y "
    "revísalo aquí. Mismo camino que `cli.py --tv-review-specs` — ver "
    "`docs/tv-review-v2-howto.md`."
)

with st.expander("Fuente de datos (catálogo de TVs)", expanded=False):
    available_files = _discover_specs_files()
    options = available_files or [DEFAULT_SPECS_FILE]
    default_index = (
        options.index(DEFAULT_SPECS_FILE) if DEFAULT_SPECS_FILE in options else 0
    )
    specs_file = st.selectbox(
        "Fichero de specs (resource/tv_specs/)",
        options=options,
        index=default_index,
        help="JSON/CSV con las TVs importadas del Google Sheet, ver app/services/tv_specs.py",
    )

specs, load_error = _load_specs(specs_file)
if load_error:
    st.error(f"No se pudo leer {specs_file}: {load_error}")
    st.stop()
if not specs:
    st.warning(f"{specs_file} no contiene ninguna TV.")
    st.stop()

label_by_specs = {s.display_name(): s for s in specs}
st.dataframe(
    [
        {
            "TV": s.display_name(),
            "Panel": s.panel_type,
            "Precio": f"{s.price:g} {s.currency}" if s.price is not None else "—",
            "Ideal para": s.ideal_for,
            "Fotos reales (R2)": "✅" if s.product_images_prefix else "—",
        }
        for s in specs
    ],
    use_container_width=True,
    hide_index=True,
)

selected_labels = st.multiselect(
    "TVs a probar (1 = review individual, 2+ = comparativa)",
    options=list(label_by_specs.keys()),
    default=[next(iter(label_by_specs))] if label_by_specs else [],
)
selected_specs = [label_by_specs[label] for label in selected_labels]

comparison_angle = ""
if len(selected_specs) > 1:
    comparison_angle = st.text_input(
        "Ángulo de la comparativa",
        placeholder="p.ej. mejor opción para gaming por menos de 300€",
    )

col1, col2 = st.columns(2)
with col1:
    video_language = st.text_input("Idioma del guion", value=DEFAULT_LANGUAGE)
    video_aspect = st.selectbox(
        "Formato",
        options=[a.value for a in VideoAspect],
        index=[a.value for a in VideoAspect].index(VideoAspect.portrait.value),
    )
    video_count = st.number_input("Nº de vídeos a generar", min_value=1, max_value=5, value=1)
with col2:
    voice_name = st.text_input("Voz (TTS)", value=DEFAULT_VOICE_NAME)
    animation_method = st.selectbox(
        "Animación de fotos de producto",
        options=list(ANIMATION_METHOD_LABELS.keys()),
        format_func=lambda k: ANIMATION_METHOD_LABELS[k],
    )
    media_method = st.selectbox(
        "Cómo resolver las fotos desde R2",
        options=["api", "public_url"],
        help="'api': credenciales S3-compatibles de R2. 'public_url': HEAD a un bucket público.",
    )

single_selected = selected_specs[0] if len(selected_specs) == 1 else None
default_prefix = single_selected.product_images_prefix if single_selected else ""
product_images_prefix = st.text_input(
    "Prefijo de fotos en R2 (vacío = usar stock genérico)",
    value=default_prefix,
    help="Se auto-rellena desde el catálogo si eliges una sola TV con fotos subidas a R2.",
)
if animation_method == "wavespeed":
    st.info(
        "WaveSpeed cobra por foto animada (~0.10-0.15 USD con MiniMax H3). "
        "Revisa saldo en wavespeed.ai antes de lanzar varias pruebas seguidas."
    )

if "tv_lab_runs" not in st.session_state:
    st.session_state["tv_lab_runs"] = []  # [{task_id, label}], más reciente primero

launch = st.button(
    "🎬 Generar vídeo",
    type="primary",
    disabled=not selected_specs,
    use_container_width=True,
)
if launch:
    params = _build_params(
        selected_specs,
        comparison_angle,
        video_language,
        voice_name,
        video_aspect,
        int(video_count),
        animation_method,
        media_method,
        product_images_prefix,
    )
    task_id = utils.get_uuid()
    label = params.video_subject
    webui_task.submit_generation(task_id, params, capture_logs=True)
    st.session_state["tv_lab_runs"].insert(0, {"task_id": task_id, "label": label})
    st.rerun()

runs = st.session_state.get("tv_lab_runs", [])
if runs:
    st.divider()
    st.subheader("Resultados")
    still_running = False
    for run in runs:
        with st.expander(f"{run['label']} — {run['task_id']}", expanded=(run is runs[0])):
            if _render_task_result(run["task_id"], run["label"]):
                still_running = True
    if still_running:
        import time

        time.sleep(2)
        st.rerun()
