"""Small explicit Responses text surface; unknown content always fails closed.

This adapter intentionally rejects encrypted history, tool definitions/results,
references to stored conversations, and non-text modalities (PRD R02/R05).
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import re


class ProtocolError(Exception):
    def __init__(self, code="unsupported_protocol"):
        self.code = code
        super().__init__(code)


@dataclass
class ParsedText:
    body: dict
    segments: list[dict]
    paths: list[tuple]

    def replace(self, segments: list[dict]) -> dict:
        if (not isinstance(segments, list) or len(segments) != len(self.segments)
                or any(s.get("segment_id") != original["segment_id"] or not isinstance(s.get("text"), str)
                       for s, original in zip(segments, self.segments))):
            raise ProtocolError("invalid_replacement")
        result = deepcopy(self.body)
        for path, segment in zip(self.paths, segments):
            target = result
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = segment["text"]
        return result


def _keys(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ProtocolError()


def _text(value):
    if not isinstance(value, str):
        raise ProtocolError()
    # Lone surrogate codepoints cannot be encoded as valid UTF-8.
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ProtocolError("invalid_unicode") from None
    return value


def _collect(body, paths):
    segments = []
    for index, path in enumerate(paths):
        value = body
        for key in path:
            value = value[key]
        segments.append({"segment_id": f"s{index}", "text": _text(value)})
    if sum(len(s["text"]) for s in segments) > 16000:
        raise ProtocolError("text_limit")
    return ParsedText(body, segments, paths)


def parse_request(value, allowed_models):
    if isinstance(value, dict) and "client_metadata" in value:
        # This is a separately validated pinned CLI envelope, not a permissive
        # fallback for unsupported ordinary Responses fields.
        from .cli_request import parse_cli_request
        return parse_cli_request(value, allowed_models)
    _keys(value, {"model", "instructions", "input", "stream", "store", "tools", "tool_choice",
                  "parallel_tool_calls", "reasoning", "text", "max_output_tokens"}, {"model", "input"})
    if not isinstance(value["model"], str) or value["model"] not in allowed_models:
        raise ProtocolError("model_not_allowed")
    for field in ("stream", "store", "parallel_tool_calls"):
        if field in value and type(value[field]) is not bool:
            raise ProtocolError()
    if value.get("store", False) or value.get("parallel_tool_calls", False):
        raise ProtocolError()
    if value.get("tools", []) != [] or value.get("tool_choice", "none") != "none":
        raise ProtocolError("tools_unsupported")
    if "max_output_tokens" in value and (type(value["max_output_tokens"]) is not int or not 1 <= value["max_output_tokens"] <= 32768):
        raise ProtocolError()
    if "reasoning" in value:
        _keys(value["reasoning"], {"effort"}, {"effort"})
        if value["reasoning"]["effort"] not in ("none", "minimal", "low", "medium", "high", "xhigh"):
            raise ProtocolError()
    if "text" in value:
        _keys(value["text"], {"verbosity"}, {"verbosity"})
        if value["text"]["verbosity"] not in ("low", "medium", "high"):
            raise ProtocolError()
    body = deepcopy(value)
    body["store"] = False
    paths = []
    if "instructions" in body:
        paths.append(("instructions",))
    items = body["input"]
    if isinstance(items, str):
        paths.append(("input",))
    elif isinstance(items, list) and items and len(items) <= 256:
        for i, item in enumerate(items):
            _keys(item, {"type", "role", "content"}, {"role", "content"})
            if item.get("type", "message") != "message" or item["role"] not in ("system", "developer", "user", "assistant"):
                raise ProtocolError()
            content = item["content"]
            if isinstance(content, str):
                paths.append(("input", i, "content"))
            elif isinstance(content, list) and content and len(content) <= 256:
                for j, part in enumerate(content):
                    _keys(part, {"type", "text"}, {"type", "text"})
                    expected = "output_text" if item["role"] == "assistant" else "input_text"
                    if part["type"] != expected:
                        raise ProtocolError()
                    paths.append(("input", i, "content", j, "text"))
            else:
                raise ProtocolError()
    else:
        raise ProtocolError()
    return _collect(body, paths)


def parse_response(value):
    if not isinstance(value, dict) or value.get("status") != "completed":
        raise ProtocolError("upstream_incomplete")
    output = value.get("output")
    if not isinstance(output, list) or not output or len(output) > 256:
        raise ProtocolError()
    # Rebuild from accepted text only. Never reflect upstream metadata, errors,
    # arbitrary IDs, model labels, annotations, encrypted reasoning, or URLs.
    body = {"id": "resp_gateway", "object": "response", "created_at": 0,
            "status": "completed", "output": []}
    paths = []
    for i, item in enumerate(output):
        if (not isinstance(item, dict) or item.get("type") != "message"
                or item.get("role") != "assistant" or item.get("status", "completed") != "completed"):
            raise ProtocolError()
        content = item.get("content")
        if not isinstance(content, list) or not content or len(content) > 256:
            raise ProtocolError()
        safe_item = {"id": f"msg_gateway_{i}", "type": "message", "role": "assistant",
                     "status": "completed", "content": []}
        for j, part in enumerate(content):
            if not isinstance(part, dict) or part.get("type") != "output_text":
                raise ProtocolError()
            safe_item["content"].append({"type": "output_text", "text": _text(part.get("text")), "annotations": []})
            paths.append(("output", i, "content", j, "text"))
        body["output"].append(safe_item)
    return _collect(body, paths)


def safe_events(body):
    """Only call after inbound policy and journal durability have succeeded."""
    sequence = 0

    def event(kind, **values):
        nonlocal sequence
        value = {"type": kind, "sequence_number": sequence, **values}
        sequence += 1
        return f"event: {kind}\ndata: {json.dumps(value, ensure_ascii=False, allow_nan=False)}\n\n".encode()

    yield event("response.created", response={**body, "status": "in_progress", "output": []})
    for i, item in enumerate(body["output"]):
        yield event("response.output_item.added", output_index=i, item={**item, "status": "in_progress", "content": []})
        for j, part in enumerate(item["content"]):
            loc = {"item_id": item["id"], "output_index": i, "content_index": j}
            yield event("response.content_part.added", **loc, part={**part, "text": ""})
            yield event("response.output_text.delta", **loc, delta=part["text"])
            yield event("response.output_text.done", **loc, text=part["text"])
            yield event("response.content_part.done", **loc, part=part)
        yield event("response.output_item.done", output_index=i, item=item)
    yield event("response.completed", response=body)
