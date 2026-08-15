"""Icon/Asset MCP server.

Manages icons, images, map assets, audio assets, fonts, and overlays. Uses
the existing secure path utilities to validate all file paths. Supports
scanning a local asset directory to auto-register assets (no copyrighted
scraping — only user-supplied local files).

Tools:
- ``register_asset`` — register an asset with full metadata + provenance
- ``get_asset`` — retrieve an asset by ID
- ``list_assets`` — list registered assets (optionally filtered by type)
- ``validate_asset`` — validate an asset file path and format
- ``find_asset`` — find assets matching a query and/or tags
- ``scan_directory`` — scan a local directory and auto-register supported files

Never invents missing assets. The in-memory registry is per-server-instance;
a future enhancement can persist to the database.

Legacy tools (backward compat):
- ``select_asset`` — returns failure (assets must be registered, not invented)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import get_settings
from app.core.enums import AgentName, AssetFormat, AssetType
from app.core.result import Result
from app.guardrails.media import file_extension, is_supported_media
from app.mcp.schemas import (
    FindAssetInput,
    FindAssetOutput,
    GetAssetInput,
    GetAssetOutput,
    ListAssetsInput,
    ListAssetsOutput,
    RegisterAssetInput,
    RegisterAssetOutput,
    ScanAssetsDirInput,
    ScanAssetsDirOutput,
    ValidateAssetInput,
    ValidateAssetOutput,
)
from app.mcp.servers.base import BaseMcpServer, ToolDefinition
from app.utils.ids import new_id
from app.utils.paths import contains_traversal, restrict_to_directory


class AssetMcpServer(BaseMcpServer):
    """Finds/generates/selects icons and visual assets with metadata."""

    name = AgentName.ASSET
    version = "4.0.0"
    description = "Manages icons, images, map assets, audio, fonts with provenance and tag search."

    def __init__(self) -> None:
        super().__init__()
        self._assets: dict[str, dict[str, Any]] = {}
        self._register_tool(ToolDefinition(
            name="register_asset",
            description="Register an asset with full metadata and provenance.",
            input_schema=RegisterAssetInput,
            output_schema=RegisterAssetOutput,
            handler=self._register_asset,
            tags={"write"},
        ))
        self._register_tool(ToolDefinition(
            name="get_asset",
            description="Retrieve a registered asset by ID.",
            input_schema=GetAssetInput,
            output_schema=GetAssetOutput,
            handler=self._get_asset,
            tags={"read"},
        ))
        self._register_tool(ToolDefinition(
            name="list_assets",
            description="List registered assets, optionally filtered by type.",
            input_schema=ListAssetsInput,
            output_schema=ListAssetsOutput,
            handler=self._list_assets,
            tags={"read"},
        ))
        self._register_tool(ToolDefinition(
            name="validate_asset",
            description="Validate an asset file path and format against supported media.",
            input_schema=ValidateAssetInput,
            output_schema=ValidateAssetOutput,
            handler=self._validate_asset,
            tags={"read"},
        ))
        self._register_tool(ToolDefinition(
            name="find_asset",
            description="Find assets matching a query (by name, tags, or metadata).",
            input_schema=FindAssetInput,
            output_schema=FindAssetOutput,
            handler=self._find_asset,
            tags={"read"},
        ))
        self._register_tool(ToolDefinition(
            name="scan_directory",
            description="Scan a local asset directory and auto-register supported media files.",
            input_schema=ScanAssetsDirInput,
            output_schema=ScanAssetsDirOutput,
            handler=self._scan_directory,
            tags={"write"},
        ))

    async def handle(self, tool: str, arguments: dict[str, Any]) -> Result[Any]:
        if tool == "select_asset":
            return await self._select_asset_legacy(arguments)
        return await self.execute_tool(tool, arguments)

    # --- tool handlers ------------------------------------------------------

    async def _register_asset(self, inp: RegisterAssetInput) -> Result[RegisterAssetOutput]:
        asset_id = new_id("asset_")
        record: dict[str, Any] = {
            "asset_id": asset_id,
            "name": inp.name,
            "asset_type": inp.asset_type.value,
            "format": inp.format.value,
            "file_path": inp.file_path,
            "source": inp.source,
            "license": inp.license,
            "tags": inp.tags or [],
            "dimensions": inp.dimensions or {},
            "metadata": inp.metadata or {},
        }
        self._assets[asset_id] = record
        return Result.ok(RegisterAssetOutput(
            asset_id=asset_id,
            name=inp.name,
            asset_type=inp.asset_type.value,
            format=inp.format.value,
            file_path=inp.file_path,
            source=inp.source,
            license=inp.license,
            registered=True,
        ))

    async def _get_asset(self, inp: GetAssetInput) -> Result[GetAssetOutput]:
        asset = self._assets.get(inp.asset_id)
        if asset is None:
            return Result.ok(GetAssetOutput(asset=None, found=False))
        return Result.ok(GetAssetOutput(asset=asset, found=True))

    async def _list_assets(self, inp: ListAssetsInput) -> Result[ListAssetsOutput]:
        assets = list(self._assets.values())
        if inp.asset_type is not None:
            assets = [a for a in assets if a["asset_type"] == inp.asset_type.value]
        return Result.ok(ListAssetsOutput(assets=assets, count=len(assets)))

    async def _validate_asset(self, inp: ValidateAssetInput) -> Result[ValidateAssetOutput]:
        errors: list[str] = []
        ext = file_extension(inp.file_path)
        if not ext:
            errors.append(f"file has no extension: {inp.file_path}")
        elif not is_supported_media(inp.file_path):
            errors.append(f"unsupported media format '{ext}': {inp.file_path}")
        return Result.ok(ValidateAssetOutput(
            valid=not errors,
            file_path=inp.file_path,
            format=ext or None,
            errors=errors,
        ))

    async def _find_asset(self, inp: FindAssetInput) -> Result[FindAssetOutput]:
        query_lower = inp.query.lower()
        matches: list[dict[str, Any]] = []
        for asset in self._assets.values():
            # Match by name.
            if query_lower in asset["name"].lower():
                matches.append(asset)
                continue
            # Match by tags.
            tags = asset.get("tags") or []
            if any(query_lower in t.lower() for t in tags):
                matches.append(asset)
                continue
            # Search metadata values.
            md = asset.get("metadata") or {}
            if any(query_lower in str(v).lower() for v in md.values()):
                matches.append(asset)
        # Additional tag filter (AND logic: must match all requested tags).
        if inp.tags:
            wanted = {t.lower() for t in inp.tags}
            matches = [
                m for m in matches
                if wanted.issubset({t.lower() for t in (m.get("tags") or [])})
            ]
        if inp.asset_type is not None:
            matches = [m for m in matches if m["asset_type"] == inp.asset_type.value]
        return Result.ok(FindAssetOutput(assets=matches, count=len(matches)))

    async def _scan_directory(self, inp: ScanAssetsDirInput) -> Result[ScanAssetsDirOutput]:
        """Scan a local directory and auto-register supported media files.

        Only user-supplied local files are registered — no copyrighted
        scraping. Files must be inside the approved assets directory.
        """
        settings = get_settings()
        errors: list[str] = []
        warnings: list[str] = []
        directory = inp.directory or str(settings.assets_path)
        # Validate the directory is inside the approved assets root.
        if contains_traversal(directory):
            return Result.fail(f"path traversal detected: {directory}")
        try:
            dir_path = restrict_to_directory(directory, settings.assets_path, must_exist=True)
        except Exception as exc:
            return Result.fail(f"unsafe or missing directory: {exc}")

        if not dir_path.is_dir():
            return Result.fail(f"not a directory: {dir_path}")

        scanned = 0
        registered = 0
        globber = dir_path.rglob("*") if inp.recursive else dir_path.glob("*")
        for p in globber:
            if not p.is_file():
                continue
            scanned += 1
            ext = file_extension(str(p))
            if not ext or not is_supported_media(str(p)):
                continue
            atype, fmt = self._infer_asset_type(ext)
            if atype is None:
                warnings.append(f"skipped unsupported format: {p}")
                continue
            asset_id = new_id("asset_")
            record: dict[str, Any] = {
                "asset_id": asset_id,
                "name": p.stem,
                "asset_type": atype.value,
                "format": fmt.value,
                "file_path": str(p),
                "source": "local_scan",
                "license": None,
                "tags": self._infer_tags(p.name, atype),
                "dimensions": {},
                "metadata": {"scanned_at": str(p.stat().st_mtime)},
            }
            self._assets[asset_id] = record
            registered += 1
        if registered == 0:
            warnings.append(f"no supported media files found in {dir_path}")
        return Result.ok(ScanAssetsDirOutput(
            scanned=scanned, registered=registered, errors=errors, warnings=warnings,
        ))

    @staticmethod
    def _infer_asset_type(ext: str) -> tuple[AssetType | None, AssetFormat | None]:
        """Infer (AssetType, AssetFormat) from a file extension."""
        ext_lower = ext.lower().lstrip(".")
        fmt_map = {
            "png": AssetFormat.PNG, "jpg": AssetFormat.JPG, "jpeg": AssetFormat.JPG,
            "webp": AssetFormat.WEBP, "svg": AssetFormat.SVG,
            "mp3": AssetFormat.MP3, "wav": AssetFormat.WAV, "aac": AssetFormat.AAC,
            "mp4": AssetFormat.MP4, "webm": AssetFormat.WEBM,
            "ttf": AssetFormat.TTF, "otf": AssetFormat.OTF,
            "geojson": AssetFormat.GEOJSON,
        }
        fmt = fmt_map.get(ext_lower)
        if fmt is None:
            return None, None
        # Infer type from format.
        if fmt in {AssetFormat.PNG, AssetFormat.JPG, AssetFormat.WEBP}:
            return AssetType.IMAGE, fmt
        if fmt == AssetFormat.SVG:
            return AssetType.SVG, fmt
        if fmt in {AssetFormat.MP3, AssetFormat.WAV, AssetFormat.AAC}:
            return AssetType.AUDIO, fmt
        if fmt in {AssetFormat.MP4, AssetFormat.WEBM}:
            return AssetType.VIDEO_CLIP, fmt
        if fmt in {AssetFormat.TTF, AssetFormat.OTF}:
            return AssetType.FONT, fmt
        if fmt == AssetFormat.GEOJSON:
            return AssetType.VECTOR, fmt
        return AssetType.OTHER, fmt

    @staticmethod
    def _infer_tags(filename: str, atype: AssetType) -> list[str]:
        """Infer tags from a filename (heuristic)."""
        tags: list[str] = [atype.value]
        name_lower = filename.lower()
        if "icon" in name_lower:
            tags.append("icon")
        if "map" in name_lower:
            tags.append("map")
        if "logo" in name_lower:
            tags.append("logo")
        if "bg" in name_lower or "background" in name_lower:
            tags.append("background")
        return tags

    # --- legacy -------------------------------------------------------------

    async def _select_asset_legacy(self, arguments: dict[str, Any]) -> Result[list[dict]]:
        """Legacy: assets must be registered, not invented."""
        requirement = arguments.get("requirement", "")
        if not requirement or not str(requirement).strip():
            return self._fail("requirement must not be empty")
        return self._fail(
            f"asset selection requires registration; use register_asset for: {requirement}"
        )
