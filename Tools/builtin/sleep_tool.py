"""Sleep tool — deliberate wait for the agent"""
import time
from Tools.registry import registry


def sleep(ms: int = 1000) -> str:
    """Block for the given number of milliseconds, then return."""
    seconds = max(0, int(ms)) / 1000.0
    time.sleep(seconds)
    return f"Slept for {int(ms)}ms ({seconds:.2f}s)"


registry.register(
    name="Sleep",
    description="Pause execution for a specified duration in milliseconds.",
    arguments_schema={
        "type": "object",
        "properties": {
            "ms": {
                "type": "integer",
                "description": "Duration to wait in milliseconds (default 1000 = 1 second)",
                "default": 1000,
            },
        },
    },
    handler=sleep,
    group="utility",
)
