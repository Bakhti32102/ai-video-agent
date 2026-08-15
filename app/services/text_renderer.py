"""Text rendering service using Pillow.

Renders text overlays to PNG images for compositing into video frames.
Open-source only (no Adobe After Effects dependency). Supports:
- title, subtitle, lower_third, location_label, date, statistic, callout
- safe-area positioning
- font, size, color, background, alignment
- word wrapping for long text

All output paths are validated against the approved output/temp directories.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.core.exceptions import FileSafetyError
from app.core.logging import get_logger
from app.utils.paths import restrict_to_directory

logger = get_logger("text_renderer")

# Default font search paths (Linux). Windows users can set FONT_PATH.
_DEFAULT_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
]


class TextRenderer:
    """Renders text to PNG images using Pillow."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def render(
        self,
        text: str,
        output_path: str,
        *,
        font_size: int = 48,
        color: str = "#FFFFFF",
        background_color: str = "#000000",
        width: int = 1920,
        height: int = 1080,
        x: float = 0.1,
        y: float = 0.1,
        font_path: str | None = None,
        align: str = "left",
    ) -> str:
        """Render text to a PNG file. Returns the validated output path."""
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError as exc:
            raise RuntimeError(
                "Pillow is required for text rendering; install with 'pip install Pillow'"
            ) from exc

        # Validate output path is in an approved directory.
        safe_path = self._validate_output_path(output_path)

        img = Image.new("RGBA", (width, height), self._hex_to_rgba(background_color, 0))
        draw = ImageDraw.Draw(img)

        # Background box (semi-transparent for readability).
        bg_rgb = self._hex(background_color)
        # Draw a background rectangle behind the text area.
        font = self._load_font(font_path, font_size)
        # Calculate text bounding box.
        lines = self._wrap_text(text, font, width, x)
        line_height = font_size + 6
        text_block_height = len(lines) * line_height
        px = int(x * width)
        py = int(y * height)
        # Draw semi-transparent background.
        bg_img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        bg_draw = ImageDraw.Draw(bg_img)
        margin = 20
        bg_draw.rectangle(
            [
                max(0, px - margin),
                max(0, py - margin),
                min(width, px + self._max_text_width(lines, font) + margin),
                min(height, py + text_block_height + margin),
            ],
            fill=(bg_rgb[0], bg_rgb[1], bg_rgb[2], 180),
        )
        img = Image.alpha_composite(img, bg_img)
        draw = ImageDraw.Draw(img)

        text_rgb = self._hex(color)
        for i, line in enumerate(lines):
            ly = py + i * line_height
            if align == "center":
                tw = draw.textlength(line, font=font)
                draw.text((px + (self._max_text_width(lines, font) - tw) / 2, ly), line, fill=text_rgb, font=font)
            elif align == "right":
                tw = draw.textlength(line, font=font)
                draw.text((px + self._max_text_width(lines, font) - tw, ly), line, fill=text_rgb, font=font)
            else:
                draw.text((px, ly), line, fill=text_rgb, font=font)

        Path(safe_path).parent.mkdir(parents=True, exist_ok=True)
        # Convert RGBA to RGB for PNG (flatten onto black if needed).
        if background_color:
            final = Image.new("RGB", (width, height), self._hex(background_color))
            final.paste(img, mask=img.split()[3] if img.mode == "RGBA" else None)
            final.save(safe_path, "PNG")
        else:
            img.convert("RGB").save(safe_path, "PNG")
        logger.info("rendered text overlay to %s", safe_path)
        return str(safe_path)

    def _validate_output_path(self, path: str) -> Path:
        """Output must be inside the approved output or temp directory."""
        for root_attr in ("output_path", "temp_path"):
            root = getattr(self.settings, root_attr)
            try:
                return restrict_to_directory(path, root)
            except FileSafetyError:
                continue
        raise FileSafetyError(f"output path not in approved directory: {path}")

    @staticmethod
    def _load_font(font_path: str | None, size: int):
        """Load a font, falling back to system defaults."""
        try:
            from PIL import ImageFont
        except ImportError:
            return None
        if font_path:
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                logger.warning("could not load font %s; using default", font_path)
        for path in _DEFAULT_FONTS:
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
        return ImageFont.load_default()

    @staticmethod
    def _hex(color: str) -> tuple[int, int, int]:
        c = color.lstrip("#")
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)

    @staticmethod
    def _hex_to_rgba(color: str, alpha: int = 255) -> tuple[int, int, int, int]:
        c = color.lstrip("#")
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), alpha

    @staticmethod
    def _wrap_text(text: str, font, max_width: int, x_norm: float) -> list[str]:
        """Wrap text to fit within the available width."""
        if font is None:
            return [text]
        try:
            from PIL import ImageDraw
            dummy = ImageDraw.Draw(__import__("PIL").Image.new("RGB", (10, 10)))
        except Exception:
            return [text]
        available = int(max_width * (1.0 - x_norm - 0.05))
        words = text.split()
        if not words:
            return [text]
        lines: list[str] = []
        current = words[0]
        for word in words[1:]:
            test = current + " " + word
            if dummy.textlength(test, font=font) <= available:
                current = test
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines

    @staticmethod
    def _max_text_width(lines: list[str], font) -> int:
        if font is None:
            return 500
        try:
            from PIL import ImageDraw, Image as PILImage
            dummy = ImageDraw.Draw(PILImage.new("RGB", (10, 10)))
            return int(max(dummy.textlength(line, font=font) for line in lines))
        except Exception:
            return 500


__all__ = ["TextRenderer"]
