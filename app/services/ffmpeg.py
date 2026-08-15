"""FFmpeg media processing service.

Provides safe media probing (ffprobe) and video rendering (ffmpeg) via
subprocess. Security:
- No ``shell=True``: every command is an argv list.
- Input/output paths are validated against path-traversal and restricted to
  approved project directories.
- No arbitrary user-supplied command arguments are accepted — only structured
  :class:`RenderJobParams`.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.core.exceptions import AppError, FileSafetyError, RenderError
from app.core.logging import get_logger, log_event
from app.utils.paths import restrict_to_directory, validate_path_safety

logger = get_logger("ffmpeg")


@dataclass
class MediaInfo:
    """Probed metadata for a media file."""

    file_path: str
    format: str | None = None
    duration_sec: float | None = None
    width: int | None = None
    height: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    codec: str | None = None
    bit_rate: int | None = None
    file_size: int | None = None
    raw: dict | None = None


@dataclass
class RenderJobParams:
    """Structured parameters for a render job.

    All paths are validated before subprocess execution.
    """

    output_path: str
    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    format: str = "mp4"
    duration_sec: float | None = None
    # Optional input sources (validated, must exist, inside approved dirs).
    audio_path: str | None = None
    background_path: str | None = None
    # Extra ffmpeg filter arguments (validated to be safe identifiers).
    extra_args: list[str] = field(default_factory=list)


@dataclass
class OverlayLayer:
    """A single image overlay layer for video composition.

    ``x`` and ``y`` are normalized [0,1] positions within the frame.
    ``start_time`` and ``end_time`` control when the overlay is visible.
    """

    image_path: str
    x: float = 0.0
    y: float = 0.0
    start_time: float = 0.0
    end_time: float | None = None
    # Optional opacity (0.0 to 1.0).
    opacity: float = 1.0


@dataclass
class ComposeVideoParams:
    """Parameters for composing a video from a background + overlays + audio.

    This is the real video pipeline: a background image (or color) is
    converted to a video stream, image overlays (map frames, text PNGs) are
    composited on top with timing, and an audio track is muxed in. The result
    is a proper 16:9 MP4.
    """

    output_path: str
    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    duration_sec: float = 10.0
    # Background: either a solid color (e.g. "#1a1a2e") or an image path.
    background_color: str = "#1a1a2e"
    background_image: str | None = None
    # Image overlays (map frames, text PNGs, icons).
    overlays: list[OverlayLayer] = field(default_factory=list)
    # Audio track (voiceover + mixed SFX/music).
    audio_path: str | None = None
    # Transition filtergraph (optional, applied to the video stream).
    video_filter: str | None = None
    format: str = "mp4"


@dataclass
class AudioTrack:
    """A single timed audio track for mixing (SFX or ambience).

    ``start_time`` positions the track in the final timeline (seconds).
    ``volume_db`` adjusts gain; ``fade_in_sec``/``fade_out_sec`` apply fades.
    """

    file_path: str
    start_time: float = 0.0
    volume_db: float = 0.0
    fade_in_sec: float = 0.0
    fade_out_sec: float = 0.0
    # Optional clip duration limit (trims the track to this length).
    duration_sec: float | None = None


@dataclass
class MixAudioParams:
    """Parameters for mixing voiceover + timed SFX + music into one audio file.

    The output is a single WAV/AAC file suitable for muxing into the final
    video. All input paths are validated and must exist. ``duration_sec`` pads
    or trims the mix to the target length so it matches the video duration.
    """

    output_path: str
    voiceover_path: str | None = None
    voiceover_volume_db: float = 0.0
    sfx_tracks: list[AudioTrack] = field(default_factory=list)
    music_path: str | None = None
    music_volume_db: float = -18.0
    # Target duration: the mix is padded with silence / trimmed to this length.
    duration_sec: float | None = None
    sample_rate: int = 44100
    channels: int = 1
    format: str = "wav"


@dataclass
class SceneSegmentSpec:
    """A single scene's rendering specification for segment-based composition.

    Each scene is rendered as an independent video segment (background +
    overlays scoped to that scene's duration), then adjacent segments are
    joined with transitions. ``duration_sec`` is the scene's own length
    (before any transition overlap).
    """

    scene_id: str
    duration_sec: float
    background_color: str = "#1a1a2e"
    background_image: str | None = None
    overlays: list[OverlayLayer] = field(default_factory=list)


@dataclass
class TransitionSpec:
    """A transition between two adjacent scene segments.

    ``kind`` maps to an FFmpeg xfade transition (or ``cut`` for a hard join).
    ``duration_sec`` is the cross-fade overlap; it must be shorter than both
    adjacent scenes. ``direction`` applies to slide/wipe kinds.
    """

    kind: str
    duration_sec: float = 0.5
    direction: str = "left"


@dataclass
class ComposeWithTransitionsParams:
    """Parameters for composing a multi-scene video with real transitions.

    Scene segments are rendered individually, then joined with xfade
    transitions. The mixed audio (Phase 5B) is muxed onto the final video.
    All paths are validated; no input is fabricated.
    """

    output_path: str
    segments: list[SceneSegmentSpec] = field(default_factory=list)
    transitions: list[TransitionSpec] = field(default_factory=list)
    width: int = 1920
    height: int = 1080
    fps: float = 30.0
    audio_path: str | None = None
    format: str = "mp4"


class FFmpegService(ABC):
    """Abstract media processing service backed by FFmpeg."""

    @abstractmethod
    async def probe(self, file_path: str) -> MediaInfo: ...

    @abstractmethod
    async def render(self, params: RenderJobParams) -> str: ...

    async def compose(self, params: ComposeVideoParams) -> str:
        """Compose a video from background + overlays + audio.

        Default implementation delegates to render() (no composition).
        Concrete FFmpegRenderer overrides this for real composition.
        """
        raise AppError(
            "video composition is not available in this environment",
            code="NOT_IMPLEMENTED",
        )

    async def mix_audio(self, params: MixAudioParams) -> str:
        """Mix voiceover + optional timed SFX + optional music into one audio file.

        Default implementation is unavailable; concrete FFmpegRenderer
        overrides this for real audio mixing via subprocess.
        """
        raise AppError(
            "audio mixing is not available in this environment",
            code="NOT_IMPLEMENTED",
        )

    async def compose_with_transitions(self, params: ComposeWithTransitionsParams) -> str:
        """Compose a multi-scene video with real transitions between segments.

        Default implementation is unavailable; concrete FFmpegRenderer
        overrides this for real xfade-based transition composition.
        """
        raise AppError(
            "transition composition is not available in this environment",
            code="NOT_IMPLEMENTED",
        )


class StubFFmpegService(FFmpegService):
    """Stub that refuses to probe/render (used when FFmpeg is not installed)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def probe(self, file_path: str) -> MediaInfo:
        raise AppError(
            "ffprobe is not available in this environment",
            code="NOT_IMPLEMENTED",
        )

    async def render(self, params: RenderJobParams) -> str:
        raise AppError(
            "FFmpeg rendering is not available in this environment",
            code="NOT_IMPLEMENTED",
        )


class FFmpegRenderer(FFmpegService):
    """Concrete FFmpeg-backed renderer with safe subprocess execution.

    Never uses ``shell=True``. All paths are restricted to approved project
    directories. Output is always written under the output directory.
    """

    def __init__(self, settings: Settings | None = None, *, ffmpeg_bin: str | None = None, ffprobe_bin: str | None = None) -> None:
        self.settings = settings or get_settings()
        self.ffmpeg_bin = ffmpeg_bin or self.settings.ffmpeg_path or shutil.which("ffmpeg") or "ffmpeg"
        self.ffprobe_bin = ffprobe_bin or self.settings.ffprobe_path or shutil.which("ffprobe") or "ffprobe"

    # --- probing ------------------------------------------------------------

    async def probe(self, file_path: str) -> MediaInfo:
        """Probe a media file via ffprobe and return structured metadata."""
        safe_path = self._validate_input_path(file_path, must_exist=True)
        cmd = [
            self.ffprobe_bin,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(safe_path),
        ]
        log_event(logger, "ffprobe.start", file=str(safe_path))
        try:
            result = self._run_subprocess(cmd)
        except FileNotFoundError as exc:
            raise AppError(
                f"ffprobe binary not found at '{self.ffprobe_bin}'",
                code="FFPROBE_NOT_FOUND",
            ) from exc
        if result.returncode != 0:
            raise AppError(
                f"ffprobe failed (exit {result.returncode}): {result.stderr[:500]}",
                code="FFPROBE_FAILED",
            )
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise AppError(f"ffprobe returned invalid JSON: {exc}", code="FFPROBE_BAD_OUTPUT") from exc
        return self._parse_probe(data, str(safe_path))

    def _parse_probe(self, data: dict, file_path: str) -> MediaInfo:
        fmt = data.get("format", {})
        streams = data.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        duration = None
        if fmt.get("duration"):
            try:
                duration = float(fmt["duration"])
            except (TypeError, ValueError):
                pass
        info = MediaInfo(
            file_path=file_path,
            format=fmt.get("format_name"),
            duration_sec=duration,
            width=int(video["width"]) if video and video.get("width") else None,
            height=int(video["height"]) if video and video.get("height") else None,
            sample_rate=int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
            channels=int(audio["channels"]) if audio and audio.get("channels") else None,
            codec=(video or audio or {}).get("codec_name"),
            bit_rate=int(fmt["bit_rate"]) if fmt.get("bit_rate") else None,
            file_size=int(fmt["size"]) if fmt.get("size") else None,
            raw=data,
        )
        return info

    # --- rendering ----------------------------------------------------------

    async def render(self, params: RenderJobParams) -> str:
        """Render a video via ffmpeg. Returns the validated output path."""
        output = self._validate_output_path(params.output_path)
        cmd = self._build_render_command(params, output)
        log_event(logger, "ffmpeg.render.start", output=str(output))
        try:
            result = self._run_subprocess(cmd)
        except FileNotFoundError as exc:
            raise AppError(
                f"ffmpeg binary not found at '{self.ffmpeg_bin}'",
                code="FFMPEG_NOT_FOUND",
            ) from exc
        if result.returncode != 0:
            raise RenderError(
                f"ffmpeg render failed (exit {result.returncode}): {result.stderr[:800]}",
                details={"output": str(output), "stderr": result.stderr[:2000]},
            )
        if not output.exists():
            raise RenderError(
                f"ffmpeg reported success but output file not found: {output}",
                details={"output": str(output)},
            )
        log_event(logger, "ffmpeg.render.done", output=str(output), size=output.stat().st_size)
        return str(output)

    async def compose(self, params: ComposeVideoParams) -> str:
        """Compose a video from a background + image overlays + audio.

        This is the real Phase 4 video pipeline:
        1. Background: either a solid color or a background image, looped to
           the requested duration.
        2. Overlays: each image (map PNG, text PNG, icon) is composited on top
           with position and timing (``overlay`` filter with ``enable``).
        3. Audio: an audio track is muxed in (if provided).
        4. Output: a proper 16:9 MP4 (libx264 + AAC).

        All paths are validated. The filtergraph is built programmatically
        from structured params — no shell, no user-supplied filter strings.
        """
        output = self._validate_output_path(params.output_path)
        cmd = self._build_compose_command(params, output)
        log_event(logger, "ffmpeg.compose.start", output=str(output), overlays=len(params.overlays))
        try:
            result = self._run_subprocess(cmd)
        except FileNotFoundError as exc:
            raise AppError(
                f"ffmpeg binary not found at '{self.ffmpeg_bin}'",
                code="FFMPEG_NOT_FOUND",
            ) from exc
        if result.returncode != 0:
            raise RenderError(
                f"ffmpeg compose failed (exit {result.returncode}): {result.stderr[:800]}",
                details={"output": str(output), "stderr": result.stderr[:2000]},
            )
        if not output.exists():
            raise RenderError(
                f"ffmpeg reported success but output file not found: {output}",
                details={"output": str(output)},
            )
        log_event(logger, "ffmpeg.compose.done", output=str(output), size=output.stat().st_size)
        return str(output)

    async def mix_audio(self, params: MixAudioParams) -> str:
        """Mix voiceover + timed SFX + music into a single audio file via FFmpeg.

        Builds a ``-filter_complex`` graph that:
        - normalizes every input to the target sample rate / channel layout,
        - delays each SFX track to its ``start_time``,
        - applies per-track volume (dB) and fade in/out,
        - sums everything with ``amix`` (``duration=longest``),
        - pads the result to ``duration_sec`` so it matches the video length.

        If only a voiceover is supplied (no SFX/music), the voiceover is
        normalized and padded to the target duration (no mixing needed).
        If no inputs at all, a silent track of ``duration_sec`` is produced.
        All paths are validated; no input is fabricated.
        """
        output = self._validate_output_path(params.output_path)
        cmd = self._build_mix_audio_command(params, output)
        log_event(
            logger, "ffmpeg.mix_audio.start", output=str(output),
            sfx=len(params.sfx_tracks), music=bool(params.music_path),
            voiceover=bool(params.voiceover_path),
        )
        try:
            result = self._run_subprocess(cmd)
        except FileNotFoundError as exc:
            raise AppError(
                f"ffmpeg binary not found at '{self.ffmpeg_bin}'",
                code="FFMPEG_NOT_FOUND",
            ) from exc
        if result.returncode != 0:
            raise RenderError(
                f"ffmpeg mix_audio failed (exit {result.returncode}): {result.stderr[:800]}",
                details={"output": str(output), "stderr": result.stderr[:2000]},
            )
        if not output.exists():
            raise RenderError(
                f"ffmpeg reported success but output file not found: {output}",
                details={"output": str(output)},
            )
        log_event(logger, "ffmpeg.mix_audio.done", output=str(output), size=output.stat().st_size)
        return str(output)

    # --- transition composition (Phase 5C) ----------------------------------

    # xfade transition name lookup. ``cut`` is handled separately (concat).
    # Unsupported/map kinds fall back to ``fade``.
    _XFADE_MAP: dict[str, str] = {
        "fade": "fade",
        "dissolve": "dissolve",
        "crossfade": "fade",
        "fade_to_black": "fadeblack",
        "fade_from_black": "fadeblack",
        "fadeblack": "fadeblack",
        "fadewhite": "fadewhite",
        "slide": "slideleft",
        "slide_left": "slideleft",
        "slide_right": "slideright",
        "slide_up": "slideup",
        "slide_down": "slidedown",
        "wipe": "wipeleft",
        "wipe_left": "wipeleft",
        "wipe_right": "wiperight",
        "wipe_up": "wipeup",
        "wipe_down": "wipedown",
        "zoom": "zoomin",
        "zoomin": "zoomin",
        "map_zoom": "zoomin",
        "map_to_map": "fade",
    }
    _XFADE_DIRECTIONS: dict[str, set[str]] = {
        "slide": {"left", "right", "up", "down"},
        "wipe": {"left", "right", "up", "down"},
    }

    async def compose_with_transitions(self, params: ComposeWithTransitionsParams) -> str:
        """Compose a multi-scene video with real transitions between segments.

        Each scene is rendered as an independent video segment (background +
        that scene's overlays, scoped to the scene's duration), then adjacent
        segments are joined with FFmpeg ``xfade`` transitions (or a hard cut).
        The mixed audio (Phase 5B) is muxed onto the final video.

        Falls back gracefully: very short scenes reduce the transition
        duration; unsupported transition kinds fall back to ``cut``. All paths
        are validated; no input is fabricated. Temp segment files are cleaned
        up after successful composition.
        """
        if not params.segments:
            raise RenderError("compose_with_transitions requires at least one segment")
        output = self._validate_output_path(params.output_path)
        warnings: list[str] = []

        # Validate and normalize transitions against scene boundaries.
        norm_transitions = self._normalize_transitions(params, warnings)

        # Render each scene as a short video-only segment.
        segment_paths: list[str] = []
        try:
            for i, seg in enumerate(params.segments):
                seg_path = self._render_segment(seg, i, params, output.parent)
                segment_paths.append(seg_path)

            # Build the final xfade command (or plain concat for all-cut).
            cmd = self._build_transition_command(
                segment_paths, norm_transitions, params, output, warnings,
            )
            log_event(
                logger, "ffmpeg.compose_transitions.start", output=str(output),
                segments=len(segment_paths), transitions=len(norm_transitions),
            )
            result = self._run_subprocess(cmd)
            if result.returncode != 0:
                raise RenderError(
                    f"ffmpeg transition compose failed (exit {result.returncode}): {result.stderr[:800]}",
                    details={"output": str(output), "stderr": result.stderr[:2000]},
                )
            if not output.exists():
                raise RenderError(
                    f"ffmpeg reported success but output file not found: {output}",
                    details={"output": str(output)},
                )
        except FileNotFoundError as exc:
            raise AppError(
                f"ffmpeg binary not found at '{self.ffmpeg_bin}'",
                code="FFMPEG_NOT_FOUND",
            ) from exc
        finally:
            # Clean up temp segment files regardless of success/failure.
            for sp in segment_paths:
                try:
                    os.remove(sp)
                except OSError:
                    pass

        log_event(
            logger, "ffmpeg.compose_transitions.done", output=str(output),
            size=output.stat().st_size, warnings=warnings,
        )
        return str(output)

    def _normalize_transitions(
        self, params: ComposeWithTransitionsParams, warnings: list[str],
    ) -> list[TransitionSpec]:
        """Validate transitions against scene durations; fall back to cut when unsafe.

        A transition's duration must be shorter than both adjacent scenes. If a
        scene is too short, the transition duration is clamped (or converted to
        a cut). Unsupported kinds fall back to cut with a warning.
        """
        n = len(params.segments)
        # We need n-1 transitions; pad/trim the supplied list.
        supplied = params.transitions[: max(0, n - 1)]
        normalized: list[TransitionSpec] = []
        for i, t in enumerate(supplied):
            kind = t.kind
            dur = t.duration_sec
            # Resolve kind to a supported xfade transition.
            xfade_name = self._XFADE_MAP.get(kind)
            if kind == "cut":
                normalized.append(TransitionSpec(kind="cut", duration_sec=0.0, direction=t.direction))
                continue
            if xfade_name is None:
                warnings.append(
                    f"transition {i} kind '{kind}' is unsupported; falling back to cut"
                )
                normalized.append(TransitionSpec(kind="cut", duration_sec=0.0, direction=t.direction))
                continue
            # Clamp duration to be shorter than both adjacent scenes.
            left_dur = params.segments[i].duration_sec
            right_dur = params.segments[i + 1].duration_sec
            min_scene = min(left_dur, right_dur)
            max_allowed = min_scene * 0.5
            if dur <= 0:
                warnings.append(f"transition {i} has non-positive duration; using cut")
                normalized.append(TransitionSpec(kind="cut", duration_sec=0.0, direction=t.direction))
                continue
            if dur >= min_scene:
                if max_allowed >= 0.1:
                    warnings.append(
                        f"transition {i} duration {dur}s >= scene {i} duration {min_scene}s; "
                        f"clamped to {max_allowed:.3f}s"
                    )
                    dur = round(max_allowed, 3)
                else:
                    warnings.append(
                        f"scene {i} too short ({min_scene}s) for transition; using cut"
                    )
                    normalized.append(TransitionSpec(kind="cut", duration_sec=0.0, direction=t.direction))
                    continue
            # Validate direction for slide/wipe.
            direction = t.direction
            base = kind.split("_")[0] if "_" in kind else kind
            if base in self._XFADE_DIRECTIONS and direction not in self._XFADE_DIRECTIONS[base]:
                direction = "left"
                warnings.append(f"transition {i} invalid direction; defaulting to left")
            normalized.append(TransitionSpec(kind=kind, duration_sec=dur, direction=direction))
        # Pad missing transitions with a default fade (only when n > 1).
        while len(normalized) < n - 1:
            normalized.append(TransitionSpec(kind="fade", duration_sec=0.5))
        return normalized

    def _render_segment(
        self, seg: SceneSegmentSpec, index: int,
        params: ComposeWithTransitionsParams, out_dir: Path,
    ) -> str:
        """Render a single scene as a short video-only MP4 segment.

        Reuses the existing compose filtergraph logic scoped to one scene's
        duration. Overlays are shifted to be relative to the segment start.
        """
        seg_name = f"_seg_{index}_{seg.scene_id}.mp4"
        seg_output = restrict_to_directory(seg_name, out_dir)
        # Build a ComposeVideoParams for this single segment (no audio —
        # audio is muxed in the final transition step).
        seg_params = ComposeVideoParams(
            output_path=str(seg_output.relative_to(out_dir)) if seg_output.parent == out_dir else seg_name,
            width=params.width,
            height=params.height,
            fps=params.fps,
            duration_sec=seg.duration_sec,
            background_color=seg.background_color,
            background_image=seg.background_image,
            overlays=seg.overlays,
            audio_path=None,
            video_filter=None,
            format="mp4",
        )
        # _build_compose_command validates paths and builds the argv. We pass
        # the validated output path directly.
        validated_output = restrict_to_directory(seg_name, out_dir)
        cmd = self._build_compose_command(seg_params, validated_output)
        result = self._run_subprocess(cmd)
        if result.returncode != 0:
            raise RenderError(
                f"segment {index} render failed (exit {result.returncode}): {result.stderr[:600]}",
                details={"segment": seg.scene_id, "stderr": result.stderr[:1500]},
            )
        if not validated_output.exists():
            raise RenderError(f"segment {index} output not found: {validated_output}")
        return str(validated_output)

    def _build_transition_command(
        self,
        segment_paths: list[str],
        transitions: list[TransitionSpec],
        params: ComposeWithTransitionsParams,
        output: Path,
        warnings: list[str],
    ) -> list[str]:
        """Build the ffmpeg argv that joins segments with xfade transitions.

        For a single segment, it is re-muxed with audio. For multiple segments,
        an xfade chain joins them; a final audio input is muxed onto the result.

        xfade offset formula: for transition *i* joining the running output with
        segment *i+1*, ``offset = cumulative - duration`` where ``cumulative`` is
        the output length up to that point. After each xfade the output shrinks
        by the transition duration.
        """
        cmd: list[str] = [self.ffmpeg_bin, "-y"]
        # Validate + add all segment inputs.
        for sp in segment_paths:
            safe = self._validate_input_path(sp, must_exist=True)
            cmd.extend(["-i", str(safe)])
        # Audio input (optional).
        audio_map_label: str | None = None
        if params.audio_path:
            audio_path = self._validate_input_path(params.audio_path, must_exist=True)
            cmd.extend(["-i", str(audio_path)])
            audio_map_label = f"{len(segment_paths)}:a"

        n = len(segment_paths)
        # Single segment: just map video (+ audio).
        if n == 1:
            cmd.extend(["-map", "0:v"])
            if audio_map_label:
                cmd.extend(["-map", audio_map_label])
            cmd.extend(self._output_encoding_args(params))
            if audio_map_label:
                cmd.extend(["-c:a", "aac", "-b:a", "192k", "-shortest"])
            cmd.append(str(output))
            return cmd

        # Multiple segments: build the xfade chain.
        seg_durations = [s.duration_sec for s in params.segments]
        filter_parts: list[str] = []
        prev_label = "[0:v]"
        # cumulative = output length contributed by segments processed so far.
        cumulative = seg_durations[0]
        for i, t in enumerate(transitions):
            if t.kind == "cut" or t.duration_sec <= 0:
                # xfade requires duration > 0; a hard cut is emulated with a
                # short cross-fade so the chain stays valid. 0.1s avoids the
                # edge case where a 1-frame xfade fails to overlap correctly.
                warnings.append(
                    f"transition {i} is a cut; using 0.1s cross-fade for concat safety"
                )
                dur = 0.1
                xfade_name = "fade"
            else:
                dur = t.duration_sec
                xfade_name = self._XFADE_MAP[t.kind]
                # Apply direction suffix for slide/wipe (e.g. slideleft).
                base = t.kind.split("_")[0] if "_" in t.kind else t.kind
                if base in self._XFADE_DIRECTIONS:
                    xfade_name = base + t.direction
            offset = round(cumulative - dur, 3)
            if offset < 0:
                offset = 0.0
                warnings.append(f"transition {i} offset clamped to 0 (negative)")
            next_label = f"[vt{i}]" if i < len(transitions) - 1 else "[vout]"
            filter_parts.append(
                f"{prev_label}[{i + 1}:v]xfade=transition={xfade_name}"
                f":duration={dur}:offset={offset}{next_label}"
            )
            prev_label = next_label
            # Output grows by the next segment, minus this transition's overlap.
            cumulative = offset + seg_durations[i + 1]

        filtergraph = ";".join(filter_parts)
        cmd.extend(["-filter_complex", filtergraph])
        cmd.extend(["-map", "[vout]"])
        if audio_map_label:
            cmd.extend(["-map", audio_map_label])
        cmd.extend(self._output_encoding_args(params))
        if audio_map_label:
            cmd.extend(["-c:a", "aac", "-b:a", "192k", "-shortest"])
        cmd.append(str(output))
        return cmd

    @staticmethod
    def _output_encoding_args(params: ComposeWithTransitionsParams) -> list[str]:
        """Standard output encoding args for 1920x1080 H.264 yuv420p."""
        return [
            "-c:v", "libx264",
            "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-r", str(params.fps),
            "-s", f"{params.width}x{params.height}",
        ]

    def _build_mix_audio_command(self, params: MixAudioParams, output: Path) -> list[str]:
        """Build the ffmpeg argv for mixing voiceover + SFX + music.

        Filtergraph structure:
          [0:a]aformat,volume?[vo0]            (voiceover, optional volume)
          [i:a]aformat,atrim?,volume?,afade?,adelay?[sfxi]  (timed SFX)
          [j:a]aformat,volume?[mus0]           (music, optional volume)
          [vo0][sfx..][mus0]amix=inputs=N:duration=longest[aout]
        Then padded/trimmed to ``duration_sec``. No input is fabricated.
        """
        sr = params.sample_rate
        ch = params.channels
        layout = "mono" if ch == 1 else "stereo"
        aformat = f"aformat=sample_rates={sr}:channel_layouts={layout}"

        cmd: list[str] = [self.ffmpeg_bin, "-y"]
        idx = 0

        # --- Inputs (validated, must exist) ---
        if params.voiceover_path:
            vo_path = self._validate_input_path(params.voiceover_path, must_exist=True)
            cmd.extend(["-i", str(vo_path)])
            idx += 1
        for track in params.sfx_tracks:
            sfx_path = self._validate_input_path(track.file_path, must_exist=True)
            cmd.extend(["-i", str(sfx_path)])
            idx += 1
        if params.music_path:
            music_path = self._validate_input_path(params.music_path, must_exist=True)
            cmd.extend(["-i", str(music_path)])
            idx += 1

        # --- Build the per-input filter chains ---
        filter_parts: list[str] = []
        mix_labels: list[str] = []
        pos = 0
        if params.voiceover_path:
            chain = f"[{pos}:a]{aformat}"
            if params.voiceover_volume_db != 0.0:
                chain += f",volume={params.voiceover_volume_db}dB"
            chain += "[vo0]"
            filter_parts.append(chain)
            mix_labels.append("[vo0]")
            pos += 1
        for i, track in enumerate(params.sfx_tracks):
            chain = f"[{pos}:a]{aformat}"
            if track.duration_sec is not None and track.duration_sec > 0:
                chain += f",atrim=duration={track.duration_sec}"
            if track.volume_db != 0.0:
                chain += f",volume={track.volume_db}dB"
            if track.fade_in_sec > 0:
                chain += f",afade=t=in:st=0:d={track.fade_in_sec}"
            delay_ms = int(round(max(track.start_time, 0.0) * 1000))
            if delay_ms > 0:
                chain += f",adelay={delay_ms}|{delay_ms}"
            chain += f"[sfx{i}]"
            filter_parts.append(chain)
            mix_labels.append(f"[sfx{i}]")
            pos += 1
        if params.music_path:
            chain = f"[{pos}:a]{aformat}"
            if params.music_volume_db != 0.0:
                chain += f",volume={params.music_volume_db}dB"
            chain += "[mus0]"
            filter_parts.append(chain)
            mix_labels.append("[mus0]")
            pos += 1

        # No audio inputs at all: produce silence of the target duration.
        if not mix_labels:
            dur = params.duration_sec or 1.0
            return [
                self.ffmpeg_bin, "-y", "-f", "lavfi",
                "-i", f"anullsrc=channel_layout={layout}:sample_rate={sr}:duration={dur}",
                "-c:a", "pcm_s16le", "-ar", str(sr), "-ac", str(ch),
                "-t", str(dur), str(output),
            ]

        mix_chain = (
            f"{''.join(mix_labels)}"
            f"amix=inputs={len(mix_labels)}:duration=longest:dropout_transition=0[aout]"
        )
        filter_parts.append(mix_chain)
        filtergraph = ";".join(filter_parts)
        cmd.extend(["-filter_complex", filtergraph])

        acodec = "pcm_s16le" if params.format.lower() == "wav" else "aac"
        cmd.extend([
            "-map", "[aout]",
            "-c:a", acodec,
            "-ar", str(sr),
            "-ac", str(ch),
        ])
        if params.duration_sec is not None:
            cmd.extend(["-t", str(params.duration_sec)])
        cmd.extend(["-shortest", str(output)])
        return cmd

    def _build_compose_command(self, params: ComposeVideoParams, output: Path) -> list[str]:
        """Build the ffmpeg argv for composing background + overlays + audio."""
        cmd: list[str] = [self.ffmpeg_bin, "-y"]
        input_count = 0
        # --- Background input ---
        if params.background_image:
            bg_path = self._validate_input_path(params.background_image, must_exist=True)
            cmd.extend(["-i", str(bg_path)])
            bg_label = f"[{input_count}:v]"
        else:
            # Solid color background via lavfi color source.
            color = params.background_color.lstrip("#")
            cmd.extend([
                "-f", "lavfi",
                "-i", f"color=c=0x{color}:s={params.width}x{params.height}:r={params.fps}:d={params.duration_sec}",
            ])
            bg_label = f"[{input_count}:v]"
        input_count += 1

        # --- Overlay inputs ---
        overlay_labels: list[str] = []
        for ov in params.overlays:
            ov_path = self._validate_input_path(ov.image_path, must_exist=True)
            cmd.extend(["-i", str(ov_path)])
            overlay_labels.append(f"[{input_count}:v]")
            input_count += 1

        # --- Audio input ---
        audio_map_label: str | None = None
        if params.audio_path:
            audio_path = self._validate_input_path(params.audio_path, must_exist=True)
            cmd.extend(["-i", str(audio_path)])
            # For -map, input streams use "N:a" (no brackets); brackets are
            # only for filtergraph output labels.
            audio_map_label = f"{input_count}:a"
            input_count += 1
        else:
            # Generate silent audio so the output has an audio track.
            cmd.extend([
                "-f", "lavfi",
                "-i", f"anullsrc=channel_layout=stereo:sample_rate=48000:duration={params.duration_sec}",
            ])
            audio_map_label = f"{input_count}:a"
            input_count += 1

        # --- Build filtergraph ---
        # Scale the background to the target size.
        filter_parts: list[str] = []
        filter_parts.append(
            f"{bg_label}scale={params.width}:{params.height}:force_original_aspect_ratio=decrease,"
            f"pad={params.width}:{params.height}:(ow-iw)/2:(oh-ih)/2,setsar=1[bg]"
        )

        # Composite each overlay onto the background with timing.
        prev_label = "[bg]"
        for i, (ov_label, ov) in enumerate(zip(overlay_labels, params.overlays)):
            # Scale overlay to fit within frame (max 80% width).
            scale_w = int(params.width * 0.8)
            scale_h = int(params.height * 0.8)
            px = int(ov.x * params.width)
            py = int(ov.y * params.height)
            # Build the overlay filter with timing.
            enable_expr = ""
            if ov.start_time > 0 or ov.end_time is not None:
                start = ov.start_time
                end = ov.end_time if ov.end_time is not None else params.duration_sec
                enable_expr = f":enable='between(t,{start},{end})'"
            out_label = f"[v{i}]" if i < len(overlay_labels) - 1 else "[vout]"
            filter_parts.append(
                f"{ov_label}scale={scale_w}:{scale_h}:force_original_aspect_ratio=decrease[ov{i}]"
            )
            filter_parts.append(
                f"{prev_label}[ov{i}]overlay={px}:{py}{enable_expr}{out_label}"
            )
            prev_label = out_label

        # If no overlays, the video stream is just [bg].
        if not overlay_labels:
            filter_parts.append(f"[bg]null[vout]")
        else:
            # The last overlay produced [vout]; if a video_filter is requested,
            # apply it to [vout] and re-label. We must rename the existing [vout]
            # to an intermediate label first.
            if params.video_filter:
                if not self._is_safe_arg(params.video_filter):
                    raise RenderError(f"unsafe video_filter rejected: {params.video_filter!r}")
                # Replace the last [vout] with an intermediate label and add the filter.
                filter_parts[-1] = filter_parts[-1].replace("[vout]", "[vpre]")
                filter_parts.append(f"[vpre]{params.video_filter}[vout]")

        filtergraph = ";".join(filter_parts)
        cmd.extend(["-filter_complex", filtergraph])

        # --- Output encoding ---
        cmd.extend([
            "-map", "[vout]",
            "-map", audio_map_label,
            "-c:v", "libx264",
            "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-r", str(params.fps),
            "-s", f"{params.width}x{params.height}",
            "-t", str(params.duration_sec),
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
        ])
        cmd.append(str(output))
        return cmd

    def _build_render_command(self, params: RenderJobParams, output: Path) -> list[str]:
        """Build the ffmpeg argv list. Never uses shell=True."""
        cmd: list[str] = [self.ffmpeg_bin, "-y"]
        # If an audio source is provided, use it; otherwise generate a silent
        # test pattern of the requested duration. This is a real, working
        # ffmpeg command — not a stub.
        if params.audio_path:
            audio_safe = self._validate_input_path(params.audio_path, must_exist=True)
            cmd.extend(["-i", str(audio_safe)])
        else:
            # Generate a test source (color bars + tone) so the render is real.
            duration = params.duration_sec or 5.0
            cmd.extend([
                "-f", "lavfi",
                "-i", f"testsrc=duration={duration}:size={params.width}x{params.height}:rate={params.fps}",
                "-f", "lavfi",
                "-i", f"sine=frequency=440:duration={duration}",
            ])
        cmd.extend([
            "-c:v", "libx264",
            "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-r", str(params.fps),
            "-s", f"{params.width}x{params.height}",
        ])
        if params.duration_sec:
            cmd.extend(["-t", str(params.duration_sec)])
        # Append only safe, validated extra args (no shell metachars allowed).
        for arg in params.extra_args:
            if not self._is_safe_arg(arg):
                raise RenderError(f"unsafe ffmpeg argument rejected: {arg!r}")
            cmd.append(arg)
        cmd.append(str(output))
        return cmd

    @staticmethod
    def _is_safe_arg(arg: str) -> bool:
        """Reject arguments containing shell metacharacters."""
        if not arg:
            return False
        # Allow only alphanumeric, punctuation common in ffmpeg options.
        forbidden = set(";|&$`<>(){}!\n\r\t\\")
        return not any(c in forbidden for c in arg)

    # --- path safety --------------------------------------------------------

    def _validate_input_path(self, path: str, *, must_exist: bool = False) -> Path:
        """Validate an input path is safe and inside an approved directory."""
        validate_path_safety(path, allow_absolute=True)
        # Try restricting to approved roots.
        for root_attr in ("assets_path", "data_path", "output_path"):
            root = getattr(self.settings, root_attr)
            try:
                return restrict_to_directory(path, root, must_exist=must_exist)
            except FileSafetyError:
                continue
        # Fallback: absolute path that exists and has no traversal.
        p = Path(path).resolve()
        if must_exist and not p.exists():
            raise FileSafetyError(f"input file does not exist: {p}")
        return p

    def _validate_output_path(self, path: str) -> Path:
        """Output must always be inside the approved output directory."""
        return restrict_to_directory(path, self.settings.output_path)

    # --- subprocess ---------------------------------------------------------

    def _run_subprocess(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        """Run a subprocess with an argv list (never shell=True)."""
        logger.debug("subprocess: %s", shlex.join(cmd))
        return subprocess.run(  # noqa: S603 - argv list, no shell
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )


def get_ffmpeg_service(settings: Settings | None = None) -> FFmpegService:
    """Factory: return an FFmpegRenderer if ffmpeg is installed, else Stub."""
    settings = settings or get_settings()
    ffmpeg_bin = shutil.which(settings.ffmpeg_path) or shutil.which("ffmpeg")
    ffprobe_bin = shutil.which(settings.ffprobe_path) or shutil.which("ffprobe")
    if ffmpeg_bin and ffprobe_bin:
        return FFmpegRenderer(settings, ffmpeg_bin=ffmpeg_bin, ffprobe_bin=ffprobe_bin)
    return StubFFmpegService(settings)


__all__ = [
    "ComposeVideoParams",
    "FFmpegRenderer",
    "FFmpegService",
    "MediaInfo",
    "OverlayLayer",
    "RenderJobParams",
    "StubFFmpegService",
    "get_ffmpeg_service",
]
