from dataclasses import dataclass
from typing import Any, Callable
import asyncio
import json
import threading


class ToolValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ToolEntry:
    name: str
    description: str
    arguments_schema: dict
    handler: Callable[..., Any]
    group: str | None = None
    is_async: bool = False

    def to_definition(self):
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": dict(self.arguments_schema or {}),
        }


class ToolRegistry:
    def __init__(self):
        self._tools = {}

    def register(
        self,
        name,
        description,
        arguments_schema,
        handler,
        group=None,
        is_async=False,
    ):
        tool_name = str(name or "").strip()
        if not tool_name:
            raise ValueError("Tool name is required")
        if not callable(handler):
            raise TypeError("Tool handler must be callable")

        self._tools[tool_name] = ToolEntry(
            name=tool_name,
            description=str(description or "").strip(),
            arguments_schema=dict(arguments_schema or {}),
            handler=handler,
            group=group,
            is_async=bool(is_async or asyncio.iscoroutinefunction(handler)),
        )
        return self._tools[tool_name]

    def get_entry(self, name):
        return self._tools.get(str(name or "").strip())

    def list_entries(self, group=None):
        entries = list(self._tools.values())
        if group is not None:
            entries = [entry for entry in entries if entry.group == group]
        return sorted(entries, key=lambda entry: entry.name)

    def list_definitions(self, group=None):
        return [entry.to_definition() for entry in self.list_entries(group=group)]

    @staticmethod
    def _matches_schema_type(value, expected_type):
        type_map = {
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "null": lambda item: item is None,
        }
        checker = type_map.get(expected_type)
        if checker is None:
            return True
        return checker(value)

    def _validate_arguments(self, entry, arguments):
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise ToolValidationError("Tool arguments must be a JSON object")

        schema = dict(entry.arguments_schema or {})
        properties = schema.get("properties", {}) or {}
        required = schema.get("required", []) or []

        for field_name in required:
            if field_name not in arguments:
                raise ToolValidationError(f"Missing required argument: {field_name}")

        for field_name, value in arguments.items():
            definition = properties.get(field_name)
            if not isinstance(definition, dict):
                continue
            expected_type = definition.get("type")
            if expected_type and not self._matches_schema_type(value, expected_type):
                raise ToolValidationError(
                    f"Argument '{field_name}' must be of type {expected_type}"
                )

        return dict(arguments)

    @staticmethod
    def _error_result(message):
        return json.dumps({"error": str(message)}, ensure_ascii=False)

    @staticmethod
    def _run_async(awaitable):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)

        container = {}

        def runner():
            try:
                container["result"] = asyncio.run(awaitable)
            except Exception as exc:
                container["error"] = exc

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()

        if "error" in container:
            raise container["error"]
        return container.get("result")

    async def dispatch(self, name, arguments=None):
        entry = self.get_entry(name)
        if entry is None:
            return self._error_result(f"Unknown tool: {name}")

        try:
            validated_arguments = self._validate_arguments(entry, arguments)
            if entry.is_async:
                return await entry.handler(**validated_arguments)
            return entry.handler(**validated_arguments)
        except ToolValidationError as exc:
            return self._error_result(exc)
        except Exception as exc:
            return self._error_result(f"Tool execution failed: {type(exc).__name__}: {exc}")


registry = ToolRegistry()
