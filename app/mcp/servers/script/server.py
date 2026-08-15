"""Script Understanding MCP server.

Accepts a documentary script and analyzes it, extracting scenes, narration,
locations, people, dates, historical events, and visual/map/icon/text/
transition/sound requirements.

Tools (Phase 3):
- ``analyze_script`` — full analysis (scenes + entities + requirements)
- ``split_into_scenes`` — split script into timed scene specs
- ``extract_entities`` — detect locations/dates/people/events/objects
- ``extract_locations`` — detect location mentions (unresolved until Geo MCP)

Legacy tools (kept for backward compatibility):
- ``split_scenes`` — alias for ``split_into_scenes``
- ``detect_entities`` — alias for ``extract_entities``

The script parser is deterministic and heuristic (no LLM dependency). It never
invents missing facts: locations are marked ``unresolved`` until the Geo MCP
server resolves them.
"""

from __future__ import annotations

import re
from typing import Any

from app.core.enums import AgentName
from app.core.result import Result
from app.mcp.schemas import (
    AnalyzeScriptInput,
    AnalyzeScriptOutput,
    ExtractEntitiesInput,
    ExtractEntitiesOutput,
    ExtractLocationsInput,
    ExtractLocationsOutput,
    SplitIntoScenesInput,
    SplitIntoScenesOutput,
)
from app.mcp.servers.base import BaseMcpServer, ToolDefinition
from app.utils.ids import new_id

# Heuristic patterns for entity detection (deterministic, no LLM).
# These are intentionally conservative to avoid false positives.
_DATE_RE = re.compile(r"\b(\d{1,4}(?:st|nd|rd|th)?\s+(?:century|centuries)|\d{1,2}(?:st|nd|rd|th)?\s+century|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}|\d{4}\s*(?:BCE?|CE|AD|BC)?|year\s+\d{4})\b", re.IGNORECASE)
_MONEY_RE = re.compile(r"\b(?:\\\$|€|£|ten million|\\$\\d+[\\.,]?\\d*\s*(?:million|billion|thousand)?)", re.IGNORECASE)
# Known historical figures / terms (extensible; this is a heuristic seed list).
_KNOWN_PEOPLE = {"gadsden", "santa anna", "james gadsden"}
_PLACE_KEYWORDS = {"mexico", "united states", "arizona", "new mexico", "mesa", "sonora", "chihuahua"}


class ScriptMcpServer(BaseMcpServer):
    """Understands a documentary script and produces scene specifications."""

    name = AgentName.SCRIPT
    version = "3.0.0"
    description = "Analyzes documentary scripts: scenes, entities, locations, requirements."

    def __init__(self) -> None:
        super().__init__()
        self._register_tool(ToolDefinition(
            name="analyze_script",
            description="Analyze a documentary script: extract scenes, entities, and all visual/audio/map requirements.",
            input_schema=AnalyzeScriptInput,
            output_schema=AnalyzeScriptOutput,
            handler=self._analyze_script,
            tags={"read"},
        ))
        self._register_tool(ToolDefinition(
            name="split_into_scenes",
            description="Split script text into timed scene specifications.",
            input_schema=SplitIntoScenesInput,
            output_schema=SplitIntoScenesOutput,
            handler=self._split_into_scenes,
            tags={"read"},
        ))
        self._register_tool(ToolDefinition(
            name="extract_entities",
            description="Detect locations, dates, people, events, and objects from script text.",
            input_schema=ExtractEntitiesInput,
            output_schema=ExtractEntitiesOutput,
            handler=self._extract_entities,
            tags={"read"},
        ))
        self._register_tool(ToolDefinition(
            name="extract_locations",
            description="Extract location mentions from the script (marked unresolved until Geo MCP resolves them).",
            input_schema=ExtractLocationsInput,
            output_schema=ExtractLocationsOutput,
            handler=self._extract_locations,
            tags={"read"},
        ))

    async def handle(self, tool: str, arguments: dict[str, Any]) -> Result[Any]:
        # Legacy tool aliases for backward compatibility.
        if tool == "split_scenes":
            return await self.execute_tool("split_into_scenes", arguments)
        if tool == "detect_entities":
            return await self.execute_tool("extract_entities", arguments)
        # Unknown tool — try the new execute_tool path (which will fail gracefully).
        return await self.execute_tool(tool, arguments)

    # --- tool handlers ------------------------------------------------------

    async def _analyze_script(self, inp: AnalyzeScriptInput) -> Result[AnalyzeScriptOutput]:
        scenes_result = await self._split_into_scenes(SplitIntoScenesInput(
            script_text=inp.script_text,
            total_duration_sec=inp.total_duration_sec,
            project_id=inp.project_id,
        ))
        if scenes_result.is_failure:
            return scenes_result
        scenes = scenes_result.data.scenes if scenes_result.data else []

        entities_result = await self._extract_entities(ExtractEntitiesInput(script_text=inp.script_text))
        entities = entities_result.data.model_dump() if entities_result.success and entities_result.data else {}

        locs_result = await self._extract_locations(ExtractLocationsInput(script_text=inp.script_text))
        locations = locs_result.data.locations if locs_result.success and locs_result.data else []

        requirements = self._extract_requirements(inp.script_text, scenes)

        warnings = list(scenes_result.data.warnings) + list(entities_result.data.warnings) if scenes_result.data else []
        warnings.append("script analysis is heuristic-based; LLM NER is a future enhancement")
        return Result.ok(AnalyzeScriptOutput(
            scenes=scenes,
            entities=entities,
            locations=locations,
            requirements=requirements,
            warnings=warnings,
        ))

    async def _split_into_scenes(self, inp: SplitIntoScenesInput) -> Result[SplitIntoScenesOutput]:
        text = inp.script_text
        total_duration = inp.total_duration_sec
        project_id = inp.project_id

        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [text.strip()]

        n = len(paragraphs)
        per = total_duration / n
        scenes: list[dict[str, Any]] = []
        for i, para in enumerate(paragraphs):
            start = i * per
            end = round(start + per, 3)
            # Extract per-scene requirements.
            entities = self._entities_for_paragraph(para)
            locations = self._locations_for_paragraph(para)
            scene = {
                "id": new_id("scene_"),
                "project_id": project_id,
                "index": i,
                "title": self._derive_title(para, i),
                "narration": para,
                "start_time": round(start, 3),
                "end_time": end,
                "duration": round(per, 3),
                "entities": entities,
                "locations": locations,
                "visual_type": self._visual_type(para, locations),
                "map_required": bool(locations),
                "text_required": True,
                "icon_required": self._icon_required(para),
                "transition_type": self._transition_type(i, n),
                "sound_design_requirements": self._sound_reqs(para),
            }
            scenes.append(scene)
        return Result.ok(SplitIntoScenesOutput(
            scenes=scenes,
            warnings=["script splitting is heuristic; paragraph-based with proportional timing"],
        ))

    async def _extract_entities(self, inp: ExtractEntitiesInput) -> Result[ExtractEntitiesOutput]:
        text_lower = inp.script_text.lower()
        dates = sorted(set(m.group(0) for m in _DATE_RE.finditer(inp.script_text)))
        people = sorted({p for p in _KNOWN_PEOPLE if p in text_lower})
        # Locations: match known place keywords.
        locations = sorted({kw for kw in _PLACE_KEYWORDS if kw in text_lower})
        events = self._detect_events(text_lower)
        objects = self._detect_objects(text_lower)
        return Result.ok(ExtractEntitiesOutput(
            locations=locations,
            dates=dates,
            people=people,
            events=events,
            objects=objects,
            warnings=["entity detection is heuristic; LLM NER is a future enhancement"],
        ))

    async def _extract_locations(self, inp: ExtractLocationsInput) -> Result[ExtractLocationsOutput]:
        text_lower = inp.script_text.lower()
        found = sorted({kw for kw in _PLACE_KEYWORDS if kw in text_lower})
        locations = [
            {"name": name, "status": "unresolved", "confidence": 0.0}
            for name in found
        ]
        return Result.ok(ExtractLocationsOutput(
            locations=locations,
            warnings=["locations are marked unresolved until Geo MCP resolves them"],
        ))

    # --- heuristic helpers --------------------------------------------------

    def _derive_title(self, paragraph: str, index: int) -> str:
        """Derive a short title from the first sentence of a paragraph."""
        first_sentence = paragraph.split(".")[0].strip()
        if not first_sentence:
            return f"Scene {index + 1}"
        words = first_sentence.split()
        if len(words) <= 6:
            return first_sentence[:255]
        return " ".join(words[:6]) + "..."

    def _detect_events(self, text_lower: str) -> list[str]:
        events: list[str] = []
        event_terms = {
            "negotiated": "negotiation",
            "agreement": "agreement",
            "treaty": "treaty",
            "purchase": "purchase",
            "transferred": "transfer",
            "war": "war",
            "battle": "battle",
            "signed": "signing",
        }
        for term, label in event_terms.items():
            if term in text_lower:
                events.append(label)
        return sorted(set(events))

    def _detect_objects(self, text_lower: str) -> list[str]:
        objects: list[str] = []
        # Money / quantities.
        if _MONEY_RE.search(text_lower) or "million" in text_lower or "dollars" in text_lower:
            objects.append("money")
        if "square miles" in text_lower or "acres" in text_lower:
            objects.append("land_area")
        return sorted(set(objects))

    # --- per-scene requirement helpers -------------------------------------

    def _entities_for_paragraph(self, paragraph: str) -> dict[str, Any]:
        """Extract entities scoped to a single paragraph (provenance-preserved)."""
        lower = paragraph.lower()
        dates = sorted(set(m.group(0) for m in _DATE_RE.finditer(paragraph)))
        people = sorted({p for p in _KNOWN_PEOPLE if p in lower})
        events = self._detect_events(lower)
        objects = self._detect_objects(lower)
        return {
            "dates": dates,
            "people": people,
            "events": events,
            "objects": objects,
        }

    def _locations_for_paragraph(self, paragraph: str) -> list[dict[str, Any]]:
        """Extract location mentions from one paragraph (marked unresolved)."""
        lower = paragraph.lower()
        found = sorted({kw for kw in _PLACE_KEYWORDS if kw in lower})
        return [{"name": name, "status": "unresolved", "confidence": 0.0} for name in found]

    @staticmethod
    def _visual_type(paragraph: str, locations: list[dict]) -> str:
        """Determine the visual type for a scene."""
        if locations:
            return "map"
        lower = paragraph.lower()
        if any(kw in lower for kw in ("portrait", "photograph", "photo")):
            return "image"
        if any(kw in lower for kw in ("chart", "graph", "statistics", "percent")):
            return "graphic"
        return "text"

    @staticmethod
    def _icon_required(paragraph: str) -> bool:
        lower = paragraph.lower()
        return any(kw in lower for kw in ("million", "dollars", "currency", "money", "percent", "%"))

    @staticmethod
    def _transition_type(index: int, total: int) -> str:
        """Default transition between scenes."""
        if index == 0:
            return "fade"
        if index == total - 1:
            return "fade"
        return "dissolve"

    @staticmethod
    def _sound_reqs(paragraph: str) -> list[dict[str, Any]]:
        """Derive sound design requirements for a scene from its narration."""
        lower = paragraph.lower()
        reqs: list[dict[str, Any]] = []
        if any(kw in lower for kw in ("negotiated", "agreement", "treaty", "signed")):
            reqs.append({"cue": "historical_atmosphere", "reason": "historical/diplomatic event"})
        if any(kw in lower for kw in ("war", "battle", "invasion", "attack")):
            reqs.append({"cue": "impact", "reason": "conflict reference"})
        if any(kw in lower for kw in ("travel", "journey", "crossed", "expansion")):
            reqs.append({"cue": "whoosh", "reason": "movement reference"})
        return reqs

    def _extract_requirements(self, text: str, scenes: list[dict]) -> dict[str, Any]:
        """Derive visual/map/icon/text/transition/sound requirements from script."""
        text_lower = text.lower()
        map_reqs = []
        for scene in scenes:
            narration = (scene.get("narration") or "").lower()
            if any(kw in narration for kw in _PLACE_KEYWORDS):
                map_reqs.append({"scene_index": scene["index"], "requirement": "map_zoom"})
        icon_reqs = []
        if "money" in text_lower or "million" in text_lower or "dollars" in text_lower:
            icon_reqs.append({"icon": "currency", "reason": "monetary amount mentioned"})
        text_reqs = []
        for scene in scenes:
            text_reqs.append({"scene_index": scene["index"], "kind": "title", "text": scene.get("title", "")})
        transition_reqs = []
        for i in range(1, len(scenes)):
            transition_reqs.append({"from_index": i - 1, "to_index": i, "kind": "fade"})
        sound_reqs = []
        if any(kw in text_lower for kw in ("negotiated", "agreement", "treaty")):
            sound_reqs.append({"cue": "historical_atmosphere", "reason": "historical event"})
        return {
            "map": map_reqs,
            "icons": icon_reqs,
            "text": text_reqs,
            "transitions": transition_reqs,
            "sound": sound_reqs,
        }
