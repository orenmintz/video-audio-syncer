"""
Video-Audio Syncer — Streamlit front-end.

Run with:
    streamlit run app.py
"""

import os
import tempfile
import time
import threading

import streamlit as st

from video_processor import (
    VideoProcessingError,
    create_temp_file,
    get_audio_duration,
    get_video_duration,
    process_video,
    seconds_to_timestamp,
)

# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Video-Audio Syncer",
    page_icon="🎬",
    layout="centered",
)

st.title("🎬 Video-Audio Syncer")
st.caption("Strip, trim, loop, and sync audio to video — export-ready MP4.")

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------

if "video_duration" not in st.session_state:
    st.session_state.video_duration = None

if "audio_duration" not in st.session_state:
    st.session_state.audio_duration = None

if "output_path" not in st.session_state:
    st.session_state.output_path = None

# ---------------------------------------------------------------------------
# Helper: save an uploaded file to a secure temp path
# ---------------------------------------------------------------------------

def save_upload(uploaded_file, suffix: str) -> str:
    path = create_temp_file(suffix)
    with open(path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return path


# ---------------------------------------------------------------------------
# Step 1 — Upload files
# ---------------------------------------------------------------------------

st.header("Step 1 — Upload Files")

col_video, col_audio = st.columns(2)

with col_video:
    st.subheader("Video")
    video_file = st.file_uploader(
        "Drag & drop or click to upload",
        type=["mp4", "mov"],
        key="video_upload",
        label_visibility="collapsed",
    )

with col_audio:
    st.subheader("Audio")
    audio_file = st.file_uploader(
        "Drag & drop or click to upload",
        type=["mp3", "wav"],
        key="audio_upload",
        label_visibility="collapsed",
    )

# ---------------------------------------------------------------------------
# Validate uploads and probe durations
# ---------------------------------------------------------------------------

video_tmp_path = None
audio_tmp_path = None

if video_file:
    video_tmp_path = save_upload(video_file, suffix=".mp4")
    try:
        st.session_state.video_duration = get_video_duration(video_tmp_path)
        st.success(
            f"Video loaded — duration: **{seconds_to_timestamp(st.session_state.video_duration)}**"
        )
    except VideoProcessingError as exc:
        st.error(f"Video error: {exc}")
        st.session_state.video_duration = None

if audio_file:
    ext = os.path.splitext(audio_file.name)[-1].lower()
    audio_tmp_path = save_upload(audio_file, suffix=ext)
    try:
        st.session_state.audio_duration = get_audio_duration(audio_tmp_path)
        st.success(
            f"Audio loaded — duration: **{seconds_to_timestamp(st.session_state.audio_duration)}**"
        )
    except VideoProcessingError as exc:
        st.error(f"Audio error: {exc}")
        st.session_state.audio_duration = None

# ---------------------------------------------------------------------------
# Step 2 — Select audio start timestamp
# ---------------------------------------------------------------------------

if audio_file and st.session_state.audio_duration and video_file and st.session_state.video_duration:

    st.divider()
    st.header("Step 2 — Select Audio Start Point")

    audio_dur = st.session_state.audio_duration
    video_dur = st.session_state.video_duration

    # Maximum sensible start: must leave at least 1 second before end of audio
    # (looping handles the rest, but we still show the full range).
    max_start = max(0.0, audio_dur - 0.01)

    start_seconds = st.slider(
        "Audio start timestamp",
        min_value=0.0,
        max_value=float(max_start),
        value=0.0,
        step=0.01,
        format="%.2f s",
        help="Choose where in the audio track playback begins.",
    )

    end_seconds = start_seconds + video_dur
    available_from_start = audio_dur - start_seconds
    needs_looping = available_from_start < video_dur

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Start", seconds_to_timestamp(start_seconds))
    col_b.metric("End (Start + Video Duration)", seconds_to_timestamp(end_seconds))
    col_c.metric(
        "Audio remaining",
        seconds_to_timestamp(available_from_start),
        delta="will loop ↺" if needs_looping else None,
        delta_color="normal",
    )

    if needs_looping:
        st.info(
            f"The audio segment is shorter than the video by "
            f"**{seconds_to_timestamp(video_dur - available_from_start)}** — "
            f"it will loop seamlessly to fill the full video duration."
        )

    # ---------------------------------------------------------------------------
    # Step 3 — Process
    # ---------------------------------------------------------------------------

    st.divider()
    st.header("Step 3 — Process Video")

    if st.button("⚙️ Sync & Export", type="primary", use_container_width=True):
        st.session_state.output_path = None  # reset previous result

        progress_bar = st.progress(0, text="Initialising…")
        status_text = st.empty()

        output_tmp = create_temp_file(suffix="_synced.mp4")

        # Threadsafe progress update via a list (avoids Streamlit cross-thread
        # widget writes; we poll the value in the main thread below).
        progress_holder = [0.0]
        done_event = threading.Event()
        error_holder = [None]

        def run_processing():
            try:
                def cb(val):
                    progress_holder[0] = val

                process_video(
                    video_path=video_tmp_path,
                    audio_path=audio_tmp_path,
                    audio_start_seconds=start_seconds,
                    output_path=output_tmp,
                    progress_callback=cb,
                )
            except Exception as exc:
                error_holder[0] = exc
            finally:
                done_event.set()

        worker = threading.Thread(target=run_processing, daemon=True)
        worker.start()

        stage_labels = {
            0.05: "Loading video…",
            0.15: "Loading audio…",
            0.25: "Building audio segment…",
            0.35: "Composing final clip…",
            0.99: "Encoding — this may take a moment…",
            1.00: "Done!",
        }

        while not done_event.is_set():
            pct = progress_holder[0]
            label = next(
                (v for k, v in sorted(stage_labels.items(), reverse=True) if pct >= k),
                "Starting…",
            )
            progress_bar.progress(min(pct, 0.99), text=label)
            time.sleep(0.25)

        # Final state
        if error_holder[0]:
            progress_bar.empty()
            status_text.empty()
            err = error_holder[0]
            if isinstance(err, VideoProcessingError):
                st.error(f"Processing failed: {err}")
            else:
                st.error(f"Unexpected error: {err}")
        else:
            progress_bar.progress(1.0, text="Done!")
            st.session_state.output_path = output_tmp
            status_text.success("Video processed successfully!")

    # ---------------------------------------------------------------------------
    # Step 4 — Preview & download
    # ---------------------------------------------------------------------------

    if st.session_state.output_path and os.path.exists(st.session_state.output_path):
        st.divider()
        st.header("Step 4 — Preview & Download")

        st.video(st.session_state.output_path)

        with open(st.session_state.output_path, "rb") as f:
            video_bytes = f.read()

        st.download_button(
            label="⬇️ Download Final Video",
            data=video_bytes,
            file_name="synced_video.mp4",
            mime="video/mp4",
            use_container_width=True,
            type="primary",
        )

elif audio_file and not video_file:
    st.info("Upload a video file to continue.")

elif video_file and not audio_file:
    st.info("Upload an audio file to continue.")
