"""Abstract base class for all MCP servers in the AI Video Agent pipeline.

Each specialized server (Script, Audio, Geo, ...) implements this interface.
The implementation is transport-agnostic: servers are in-process objects so
they can be unit-tested independently. A real MCP transport (stdio/SSE) can be
layered on top without changing the contract.

Phase 3 additions:
- :class:`ToolDefinition` binds a tool name, description, input/output Pydantic
  schemas, and the handler coroutine.
- :meth:`BaseMcpServer.health_check` reports server liveness.
- :meth:`BaseMcpServer.execute_tool` validates input/output against schemas
  before/after dispatch, so no unvalidated dictionaries reach the caller.
- :meth:`list_tools` returns structured tool metadata.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from pydantic import BaseModel, ValidationError

from app.core.enums import AgentName, AgentRunStatus
from app.core.logging import get_logger, log_event
from app.core.result import Result
from app.schemas.contracts import AgentResult


# Type alias for a tool handler: async function taking a validated input model
# and returning an arbitrary result (will be coerced to the output schema).
ToolHandler = Callable[[Any], Awaitable[Any]]


@dataclass
class ToolDefinition:
    """Declarative description of a single MCP tool.

    Attributes:
        name: Unique tool name within the server.
        description: Human-readable summary.
        input_schema: Pydantic model class for validated input.
        output_schema: Pydantic model class for validated output (``None``
            allows any JSON-serialisable value).
        handler: Async callable that receives a validated input instance.
        tags: Optional grouping tags (e.g. ``{"read"}`` or ``{"write"}``).
    """

    name: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel] | None = None
    handler: ToolHandler | None = None
    tags: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema.model_json_schema(),
            "output_schema": self.output_schema.model_json_schema() if self.output_schema else None,
            "tags": sorted(self.tags),
        }


class BaseMcpServer(ABC):
    """Common interface every MCP server implements.

    Subclasses must set :attr:`name`, :attr:`version`, :attr:`description`,
    register tools via :meth:`_register_tool`, and implement
    :meth:`handle` (the legacy string-dispatch entry point).
    """

    name: AgentName = AgentName.SUPERVISOR  # overridden by subclasses
    version: str = "1.0.0"
    description: str = "MCP server"

    def __init__(self) -> None:
        self.logger = get_logger(f"mcp.{self.name.value}")
        self._tools: dict[str, ToolDefinition] = {}

    # --- identity -----------------------------------------------------------

    @property
    def server_name(self) -> str:
        return self.name.value

    @property
    def server_id(self) -> str:
        return self.name.value

    # --- tool registration --------------------------------------------------

    def _register_tool(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name '{tool.name}' on server '{self.server_name}'")
        self._tools[tool.name] = tool
        self.logger.debug("registered tool %s.%s", self.server_name, tool.name)

    def get_tool(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        """Return the tool names this server exposes."""
        return list(self._tools.keys())

    def list_tool_definitions(self) -> list[ToolDefinition]:
        """Return structured tool metadata (Phase 3)."""
        return list(self._tools.values())

    def tool_schemas(self) -> dict[str, dict[str, Any]]:
        """Return JSON schemas for all tools (for discovery)."""
        return {name: tool.to_dict() for name, tool in self._tools.items()}

    # --- health -------------------------------------------------------------

    async def health_check(self) -> dict[str, Any]:
        """Report server liveness and registered tool count."""
        return {
            "server": self.server_name,
            "version": self.version,
            "description": self.description,
            "status": "healthy",
            "tools": len(self._tools),
        }

    # --- execution ----------------------------------------------------------

    async def execute_tool(self, tool: str, arguments: dict[str, Any]) -> Result[Any]:
        """Validate input, dispatch to handler, validate output.

        This is the schema-guarded entry point. ``handle`` (below) remains for
        backward compatibility with Phase 1/2 callers but is implemented in
        terms of the registered tools.
        """
        td = self._tools.get(tool)
        if td is None:
            # Fall back to the legacy handle() for tools not yet migrated to
            # ToolDefinition (backward compatibility).
            return await self.handle(tool, arguments)

        # Validate input.
        try:
            validated_input = td.input_schema.model_validate(arguments)
        except ValidationError as exc:
            errs = [f"input validation failed: {e}" for e in exc.errors()]
            log_event(self.logger, "tool.input_rejected", tool=tool, errors="; ".join(errs))
            return self._fail(*errs)

        if td.handler is None:
            return self._fail(f"tool '{tool}' has no handler registered")

        try:
            raw_output = await td.handler(validated_input)
        except Exception as exc:  # noqa: BLE001 - surface as structured failure
            log_event(self.logger, "tool.raised", tool=tool, error=str(exc))
            return self._fail(f"tool '{tool}' raised: {exc}")

        # If the handler returned a Result, unwrap it.
        if isinstance(raw_output, Result):
            if raw_output.is_failure:
                return raw_output
            raw_output = raw_output.data

        # Validate output against the declared schema (if any).
        if td.output_schema is not None and raw_output is not None:
            try:
                if isinstance(raw_output, BaseModel):
                    validated_output = raw_output
                else:
                    validated_output = td.output_schema.model_validate(raw_output)
            except ValidationError as exc:
                errs = [f"output validation failed: {e}" for e in exc.errors()]
                log_event(self.logger, "tool.output_rejected", tool=tool, errors="; ".join(errs))
                return self._fail(*errs)
            return self._ok(validated_output.model_dump(mode="json"))
        return self._ok(raw_output)

    @abstractmethod
    async def handle(self, tool: str, arguments: dict[str, Any]) -> Result[Any]:
        """Dispatch a named tool call to this server (legacy entry point).

        Subclasses implement this for backward compatibility with Phase 1/2.
        New Phase 3 tools are registered via :meth:`_register_tool` and
        invoked through :meth:`execute_tool`.
        """

    # --- helpers ------------------------------------------------------------

    def _ok(self, data: Any, *, warnings: list[str] | None = None) -> Result[Any]:
        return Result.ok(data, warnings=warnings)

    def _fail(self, *errors: str) -> Result[Any]:
        return Result.fail(*errors)

    def _to_agent_result(self, result: Result[Any], attempt: int = 1) -> AgentResult:
        """Convert an internal Result into a serializable AgentResult."""
        data = result.data
        output: dict | list | None
        if isinstance(data, (dict, list)):
            output = data
        elif data is None:
            output = None
        elif isinstance(data, BaseModel):
            output = data.model_dump(mode="json")
        else:
            output = {"value": data}
        return AgentResult(
            agent=self.name,
            status=AgentRunStatus.SUCCESS if result.success else AgentRunStatus.FAILED,
            success=result.success,
            output=output,
            errors=result.errors,
            warnings=result.warnings,
            attempt=attempt,
        )


__all__ = ["BaseMcpServer", "ToolDefinition", "ToolHandler"]
