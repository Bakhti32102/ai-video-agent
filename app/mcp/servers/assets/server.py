"""Icon/Asset MCP server.

Manages icons, images, map assets, audio assets, fonts, and overlays. Uses
the existing secure path utilities to validate all file paths.

Tools:
- ``register_asset`` — register an asset with full metadata + provenance
- ``get_asset`` — retrieve an asset by ID
- ``list_assets`` — list registered assets (optionally filtered by type)
- ``validate_asset`` — validate an asset file path and format
- ``find_asset`` — find assets matching a query

Never invents missing assets. The in-memory registry is per-server-instance;
a future enhancement can persist to the database.

Legacy tools (backward compat):
- ``select_asset`` — returns failure (assets must be registered, not invented)
- ``list_assets`` — now uses the new schema
"""

from __future__ import annotations

from typing import Any

from app.core.enums import AgentName, AssetType
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
    ValidateAssetInput,
    ValidateAssetOutput,
)
from app.mcp.servers.base import BaseMcpServer, ToolDefinition
from app.utils.ids import new_id


class AssetMcpServer(BaseMcpServer):
    """Finds/generates/selects icons and visual assets with metadata."""

    name = AgentName.ASSET
    version = "3.0.0"
    description = "Manages icons, images, map assets, audio, fonts with provenance."

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
            description="Find assets matching a query (by name or metadata).",
            input_schema=FindAssetInput,
            output_schema=FindAssetOutput,
            handler=self._find_asset,
            tags={"read"},
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
            if query_lower in asset["name"].lower():
                matches.append(asset)
                continue
            # Search metadata values.
            md = asset.get("metadata") or {}
            if any(query_lower in str(v).lower() for v in md.values()):
                matches.append(asset)
        if inp.asset_type is not None:
            matches = [m for m in matches if m["asset_type"] == inp.asset_type.value]
        return Result.ok(FindAssetOutput(assets=matches, count=len(matches)))

    # --- legacy -------------------------------------------------------------

    async def _select_asset_legacy(self, arguments: dict[str, Any]) -> Result[list[dict]]:
        """Legacy: assets must be registered, not invented."""
        requirement = arguments.get("requirement", "")
        if not requirement or not str(requirement).strip():
            return self._fail("requirement must not be empty")
        return self._fail(
            f"asset selection requires registration; use register_asset for: {requirement}"
        )
