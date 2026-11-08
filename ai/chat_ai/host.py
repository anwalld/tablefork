"""The collaboration host: a cheap model that picks the next speaker(s).

Translated from LobeHub's group-supervisor prompt; the algorithm (look at the
conversation and each member's role, then decide who speaks) is the same, but
the runtime is ours.
"""

import json
import re

# The prompt for the host model. `participants` is a "Name：role" list and
# `history` the recent turns, both injected before the call.
HOST_PROMPT = """你是这个多人房间的主持人。房间里的参与者（名字：分工）：
{participants}

最近的对话：
{history}

请决定接下来由谁来发言。规则：
- 如果用户最后一条消息明确提到了某个参与者，必须优先让那个参与者发言。
- 根据对话内容和每个人的分工，选择最合适的一个或多个参与者。
- 只能从在场参与者里选择，不要编造不存在的名字。
- 本轮无需继续时输出 end 为 true。

只输出一个 JSON 对象，不要输出别的内容，格式：
{{"speakers": ["名字"], "mode": "speak", "end": false}}
mode 为 "speak" 表示依次发言，"broadcast" 表示并行发言。"""

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def parse_host_json(text: str) -> dict | None:
    """Extracts the host's JSON decision from the model output.

    Returns a dict with `speakers`, `mode` and `end`, or None when the model
    produced something unusable, so the caller can fall back to the old rules.
    """
    stripped = text.strip()
    for candidate in _FENCE.findall(stripped):
        parsed = _load(candidate)
        if parsed is not None:
            return parsed
    return _load(stripped)


def _load(text: str) -> dict | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end < start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    speakers = parsed.get("speakers")
    if not isinstance(speakers, list) or not all(isinstance(s, str) for s in speakers):
        return None
    mode = parsed.get("mode", "speak")
    if mode not in ("speak", "broadcast"):
        return None
    return {"speakers": speakers, "mode": mode, "end": bool(parsed.get("end", False))}