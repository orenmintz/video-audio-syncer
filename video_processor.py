"""
Core video/audio processing logic using MoviePy.
Designed for easy migration to a production server.
"""

import os
import math
import tempfile
import logging
from pathlib import Path

from moviepy.editor import (
    VideoFileClip,
    AudioFileClip,
    concatenate_audioclips,
)

logger = logging.getLogger(__name__)


class VideoProcessingError(Exception):
    """Raised when video processing fails due to invalid input or processing errors."""


def load_video(path: str) -> VideoFileClip:
    """Load a video file and validate it has a positive duration."""
    try:
        clip = VideoFileClip(path)
    except Exception as exc:
        raise VideoProcessingError(f"Could not read video file: {exc}") from exc

    if clip.duration is None or clip.duration <= 0:
        clip.close()
        raise VideoProcessingError("Video file has an invalid or zero duration.")

    return clip


def load_audio(path: str) -> AudioFileClip:
    """Load an audio file and validate it has a positive duration."""
    try:
        clip = AudioFileClip(path)
    except Exception as exc:
        raise VideoProcessingError(f"Could not read audio file: {exc}") from exc

    if clip.duration is None or clip.duration <= 0:
        clip.close()
        raise VideoProcessingError("Audio file has an invalid or zero duration.")

    return clip


def get_video_duration(path: str) -> float:
    """Return the duration (seconds) of a video file."""
    clip = load_video(path)
    duration = clip.duration
    clip.close()
    return duration


def get_audio_duration(path: str) -> float:
    """Return the duration (seconds) of an audio file."""
    clip = load_audio(path)
    duration = clip.duration
    clip.close()
    return duration


def build_looped_audio(audio_path: str, start_seconds: float, required_duration: float) -> AudioFileClip:
    """
    Extract audio starting at `start_seconds` and loop it until it fills
    `required_duration`. Returns an AudioFileClip ready to be set on a video.
    """
    full_audio = load_audio(audio_path)
    total_audio_duration = full_audio.duration

    if start_seconds >= total_audio_duration:
        full_audio.close()
        raise VideoProcessingError(
            f"Start timestamp ({start_seconds:.2f}s) is beyond the audio "
            f"duration ({total_audio_duration:.2f}s)."
        )

    available = total_audio_duration - start_seconds

    if available >= required_duration:
        # Enough audio without looping — simple subclip.
        segment = full_audio.subclip(start_seconds, start_seconds + required_duration)
        full_audio.close()
        return segment

    # Need to loop: first segment + N full repeats + one final partial repeat.
    first_segment = full_audio.subclip(start_seconds, total_audio_duration)

    remaining = required_duration - available
    full_loops = math.floor(remaining / total_audio_duration)
    leftover = remaining - full_loops * total_audio_duration

    segments = [first_segment]

    for _ in range(full_loops):
        segments.append(full_audio.subclip(0, total_audio_duration))

    if leftover > 0:
        segments.append(full_audio.subclip(0, leftover))

    looped = concatenate_audioclips(segments)
    full_audio.close()
    return looped


def process_video(
    video_path: str,
    audio_path: str,
    audio_start_seconds: float,
    output_path: str,
    progress_callback=None,
) -> str:
    """
    Strip the original video audio, attach the selected (and optionally looped)
    audio segment, then export to `output_path`.

    Args:
        video_path: Path to the source video file.
        audio_path: Path to the source audio file.
        audio_start_seconds: Where in the audio file to begin (seconds).
        output_path: Destination path for the rendered MP4.
        progress_callback: Optional callable(float) receiving 0.0–1.0 progress.

    Returns:
        The absolute path of the written output file.
    """
    if progress_callback:
        progress_callback(0.05)

    video = load_video(video_path)
    video_duration = video.duration

    if progress_callback:
        progress_callback(0.15)

    new_audio = build_looped_audio(audio_path, audio_start_seconds, video_duration)

    if progress_callback:
        progress_callback(0.25)

    final_video = video.without_audio().set_audio(new_audio)

    if progress_callback:
        progress_callback(0.35)

    try:
        final_video.write_videofile(
            output_path,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=output_path + ".temp_audio.m4a",
            remove_temp=True,
            logger=None,  # suppress verbose moviepy console output
        )
    except Exception as exc:
        raise VideoProcessingError(f"Failed to encode video: {exc}") from exc
    finally:
        final_video.close()
        video.close()
        new_audio.close()

    if progress_callback:
        progress_callback(1.0)

    return os.path.abspath(output_path)


def seconds_to_timestamp(seconds: float) -> str:
    """Convert a float seconds value to MM:SS.ss display format."""
    minutes = int(seconds // 60)
    secs = seconds - minutes * 60
    return f"{minutes:02d}:{secs:05.2f}"


def create_temp_file(suffix: str) -> str:
    """Create a named temporary file and return its path."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path
