"""Size-limiting utilities to keep LLM token costs bounded.

Logs are capped at max_lines before being sent to the model.
Diffs are capped at max_diff_lines.
"""

import json


def truncate_for_llm(data: dict | list | None, max_lines: int = 200) -> str:
    if data is None:
        return "(no data)"

    serialized = json.dumps(data, indent=2, default=str)
    lines = serialized.splitlines()

    if len(lines) <= max_lines:
        return serialized

    kept = lines[:max_lines]
    dropped = len(lines) - max_lines
    kept.append(f"... [{dropped} lines truncated to stay within token budget]")
    return "\n".join(kept)
