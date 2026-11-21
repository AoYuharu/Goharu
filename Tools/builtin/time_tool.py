"""Current time tool.

返回当前日期时间（本地 / UTC / 任意 IANA 时区），只读，无副作用。
"""

import json
from datetime import datetime, timezone

from Tools.registry import registry


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False)


def current_time(timezone_name: str = "local", fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Return the current date/time for a timezone, formatted with strftime."""
    try:
        tz_name = (timezone_name or "local").strip()
        lowered = tz_name.lower()

        if lowered in ("", "local", "system"):
            now = datetime.now().astimezone()
            tz_label = str(now.tzinfo) or "local"
        elif lowered == "utc":
            now = datetime.now(timezone.utc)
            tz_label = "UTC"
        else:
            try:
                from zoneinfo import ZoneInfo
            except ImportError:  # pragma: no cover - Python < 3.9
                return _json({
                    "status": "error",
                    "error": "zoneinfo not available; use 'local' or 'UTC'",
                })
            now = datetime.now(ZoneInfo(tz_name))
            tz_label = tz_name

        fmt = fmt or "%Y-%m-%d %H:%M:%S"
        return _json({
            "status": "success",
            "datetime": now.strftime(fmt),
            "iso": now.isoformat(),
            "timezone": tz_label,
            "weekday": now.strftime("%A"),
            "timestamp": int(now.timestamp()),
        })
    except Exception as exc:
        return _json({"status": "error", "error": f"{type(exc).__name__}: {exc}"})


registry.register(
    name="current_time",
    description=(
        "Return the current date and time. Read-only, no side effects. "
        "Supports the local system timezone (default), 'UTC', or an IANA "
        "timezone name such as 'Asia/Shanghai'. Use when the exact current "
        "time/date is needed instead of guessing."
    ),
    arguments_schema={
        "type": "object",
        "properties": {
            "timezone_name": {
                "type": "string",
                "description": "Timezone: 'local' (default), 'UTC', or an IANA name like 'Asia/Shanghai'.",
                "default": "local",
            },
            "fmt": {
                "type": "string",
                "description": "strftime format string. Default: '%Y-%m-%d %H:%M:%S'.",
                "default": "%Y-%m-%d %H:%M:%S",
            },
        },
    },
    handler=current_time,
    group="utility",
)
