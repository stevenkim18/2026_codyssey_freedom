"""Explicit Codex text envelope with inspected, non-executable tool definitions.

CLI telemetry is validated then discarded before any external request. Tool
descriptions, grammar, schema strings and dictionary keys remain inspection
inputs. Structural identifiers and virtual key segments cannot be rewritten.
Grammar and semantic default/example/enum values are immutable too; only
descriptions/titles at known annotation positions permit masking.
Non-boolean numeric tool values use protected canonical JSON-number segments;
these represent parsed structural values, not their original wire lexemes.
"""
from copy import deepcopy
import json
import math
import re
from uuid import UUID

from .responses import ParsedText, ProtocolError, _text


_ROOT = {"model", "input", "stream", "store", "tools", "tool_choice", "parallel_tool_calls",
         "reasoning", "include", "prompt_cache_key", "client_metadata", "text", "max_output_tokens"}
_METADATA = {"thread_id", "root_turn_id", "session_id", "turn_id", "x-codex-installation-id",
             "x-codex-turn-metadata", "x-codex-window-id"}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_TYPES = ("object", "array", "string", "integer", "number", "boolean", "null")
_SCHEMA_KEYS = {"type", "description", "title", "properties", "required", "additionalProperties", "items",
                "enum", "anyOf", "oneOf", "allOf", "$defs", "$ref", "minimum", "maximum",
                "minLength", "maxLength", "minItems", "maxItems", "pattern", "format", "default", "examples"}


def _reject(code="unsupported_cli_protocol"):
    raise ProtocolError(code)


def _keys(value, allowed, required=()):
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        _reject()
    if set(value) - set(allowed) or set(required) - set(value):
        _reject()


def _bounded_json(value):
    nodes = size = 0

    def visit(item, depth):
        nonlocal nodes, size
        nodes += 1
        if nodes > 4096 or depth > 32:
            _reject("cli_shape_limit")
        if isinstance(item, str):
            size += len(_text(item).encode("utf-8"))
        elif isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    _reject()
                visit(key, depth + 1)
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
        elif type(item) in (int, float):
            try:
                if not math.isfinite(item):
                    _reject()
            except OverflowError:
                _reject()
        elif item is not None and type(item) is not bool:
            _reject()
        if size > 1024 * 1024:
            _reject("cli_shape_limit")

    visit(value, 0)


def _identifier(value):
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        _reject()


def _schema(value, depth=0):
    if depth > 8:
        _reject("cli_schema_limit")
    _keys(value, _SCHEMA_KEYS)
    for key, item in value.items():
        if key == "type":
            types = item if isinstance(item, list) else [item]
            if not types or len(types) > len(_TYPES) or any(not isinstance(kind, str) or kind not in _TYPES for kind in types):
                _reject()
        elif key in ("description", "title", "pattern", "format"):
            _text(item)
        elif key in ("properties", "$defs"):
            if not isinstance(item, dict) or len(item) > 32:
                _reject()
            for name, child in item.items():
                if not isinstance(name, str) or not 1 <= len(name) <= 128:
                    _reject()
                _text(name)
                _schema(child, depth + 1)
        elif key == "required":
            if not isinstance(item, list) or len(item) > 32 or any(not isinstance(name, str) or not 1 <= len(name) <= 128 for name in item):
                _reject()
            properties = value.get("properties", {})
            if not isinstance(properties, dict) or len(set(item)) != len(item) or any(name not in properties for name in item):
                _reject()
        elif key == "additionalProperties":
            if type(item) is not bool:
                _schema(item, depth + 1)
        elif key == "items":
            _schema(item, depth + 1)
        elif key in ("anyOf", "oneOf", "allOf"):
            if not isinstance(item, list) or not 1 <= len(item) <= 16:
                _reject()
            for child in item:
                _schema(child, depth + 1)
        elif key == "$ref":
            if not isinstance(item, str) or not item.startswith("#/$defs/") or len(item) > 256 or len(item.split("/")) != 3:
                _reject()
        elif key == "enum":
            if not isinstance(item, list) or not 1 <= len(item) <= 32 or any(isinstance(entry, (list, dict)) for entry in item):
                _reject()
        elif key in ("minimum", "maximum"):
            if type(item) not in (int, float):
                _reject()
        elif key in ("minLength", "maxLength", "minItems", "maxItems"):
            if type(item) is not int or not 0 <= item <= 1000000:
                _reject()
        elif key == "examples":
            if not isinstance(item, list) or len(item) > 16:
                _reject()
        # default/examples are bounded JSON; their strings are inspected below.


def _tools(value):
    if not isinstance(value, list) or len(value) > 1:
        _reject()
    names = set()
    for namespace in value:
        _keys(namespace, {"type", "name", "description", "tools"}, {"type", "name", "tools"})
        if namespace["type"] != "namespace" or namespace["name"] != "functions":
            _reject()
        if "description" in namespace:
            _text(namespace["description"])
        children = namespace["tools"]
        if not isinstance(children, list) or not 1 <= len(children) <= 2:
            _reject()
        for child in children:
            if not isinstance(child, dict) or not isinstance(child.get("name"), str) or child["name"] in names:
                _reject()
            names.add(child["name"])
            if child["name"] == "exec":
                _keys(child, {"type", "name", "description", "format"}, {"type", "name", "description", "format"})
                if child["type"] != "custom":
                    _reject()
                grammar = child["format"]
                _keys(grammar, {"type", "syntax", "definition"}, {"type", "syntax", "definition"})
                if grammar["type"] != "grammar" or grammar["syntax"] != "lark":
                    _reject()
                _text(grammar["definition"])
            elif child["name"] == "wait":
                _keys(child, {"type", "name", "description", "strict", "parameters"},
                      {"type", "name", "description", "strict", "parameters"})
                if child["type"] != "function" or type(child["strict"]) is not bool:
                    _reject()
                _schema(child["parameters"])
            else:
                _reject()
            _text(child["description"])


def _messages(items):
    if not isinstance(items, list) or not 1 <= len(items) <= 256:
        _reject()
    messages, definitions = [], []
    for item in items:
        if not isinstance(item, dict):
            _reject()
        if item.get("type") == "additional_tools":
            _keys(item, {"type", "role", "id", "tools"}, {"type", "role", "tools"})
            if item["role"] != "developer" or not isinstance(item["tools"], list):
                _reject()
            if "id" in item:
                _identifier(item["id"])
            definitions.extend(deepcopy(item["tools"]))
            continue
        _keys(item, {"type", "role", "id", "status", "content"}, {"role", "content"})
        if item.get("type", "message") != "message" or item["role"] not in ("system", "developer", "user", "assistant"):
            _reject()
        if "id" in item:
            _identifier(item["id"])
        if "status" in item and (item["role"] != "assistant" or item["status"] != "completed"):
            _reject()
        content = item["content"]
        if isinstance(content, str):
            _text(content)
        elif isinstance(content, list) and 1 <= len(content) <= 256:
            for part in content:
                _keys(part, {"type", "text", "annotations"}, {"type", "text"})
                if part["type"] != ("output_text" if item["role"] == "assistant" else "input_text"):
                    _reject()
                if "annotations" in part and (item["role"] != "assistant" or part["annotations"] != []):
                    _reject()
                _text(part["text"])
        else:
            _reject()
        message = {"type": "message", "role": item["role"], "content": deepcopy(content)}
        if "status" in item:
            message["status"] = "completed"
        messages.append(message)
    if not messages:
        _reject()
    return messages, definitions


class CLIParsedText(ParsedText):
    def __init__(self, body, segments, paths, protected):
        super().__init__(body, segments, paths)
        self._protected = frozenset(protected)

    def replace(self, segments):
        if not isinstance(segments, list) or len(segments) != len(self.segments):
            _reject("invalid_replacement")
        for original, replacement in zip(self.segments, segments):
            if (not isinstance(replacement, dict) or set(replacement) != {"segment_id", "text"}
                    or replacement["segment_id"] != original["segment_id"] or not isinstance(replacement["text"], str)):
                _reject("invalid_replacement")
            _text(replacement["text"])
            if original["segment_id"] in self._protected and replacement["text"] != original["text"]:
                _reject("invalid_replacement")
        if sum(len(item["text"]) for item in segments) > 16000:
            _reject("text_limit")
        body = deepcopy(self.body)
        for path, item in zip(self.paths, segments):
            if path is not None:
                target = body
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = item["text"]
        _tools(body.get("tools", []))
        return body


def _collect(body):
    segments, paths, protected = [], [], set()
    total = 0

    def add(text, path, immutable=False):
        nonlocal total
        text = _text(text)
        total += len(text)
        if total > 16000:
            _reject("text_limit")
        if len(segments) >= 256:
            _reject("segment_limit")
        segment_id = f"s{len(segments)}"
        segments.append({"segment_id": segment_id, "text": text})
        paths.append(path)
        if immutable:
            protected.add(segment_id)

    for index, item in enumerate(body["input"]):
        content = item["content"]
        if isinstance(content, str):
            add(content, ("input", index, "content"))
        else:
            for part_index, part in enumerate(content):
                add(part["text"], ("input", index, "content", part_index, "text"))

    def tree(value, path, context="opaque", mutable=False):
        if isinstance(value, dict):
            for key, child in value.items():
                add(key, None, True)
                annotation = context in ("tool", "schema") and key in ("description", "title")
                child_context = "opaque"
                if context == "tool" and key == "tools":
                    child_context = "tool_list"
                elif context == "tool" and key == "parameters":
                    child_context = "schema"
                elif context == "schema_map":
                    child_context = "schema"
                elif context == "schema" and key in ("properties", "$defs"):
                    child_context = "schema_map"
                elif context == "schema" and key in ("items", "additionalProperties"):
                    child_context = "schema"
                elif context == "schema" and key in ("anyOf", "oneOf", "allOf"):
                    child_context = "schema_list"
                tree(child, (*path, key), child_context, annotation)
        elif isinstance(value, list):
            child_context = "tool" if context == "tool_list" else "schema" if context == "schema_list" else "opaque"
            for index, child in enumerate(value):
                tree(child, (*path, index), child_context)
        elif isinstance(value, str):
            add(value, path, not mutable)
        elif type(value) in (int, float):
            add(json.dumps(value, allow_nan=False), None, True)

    tree(body.get("tools", []), ("tools",), "tool_list")
    return CLIParsedText(body, segments, paths, protected)


def parse_cli_request(value, allowed_models):
    """Return a canonical text-only Responses request with inspected definitions."""
    _keys(value, _ROOT, {"model", "input"})
    _bounded_json(value)
    if not isinstance(value["model"], str) or value["model"] not in allowed_models:
        _reject("model_not_allowed")
    for key in ("stream", "store", "parallel_tool_calls"):
        if key in value and type(value[key]) is not bool:
            _reject()
    if value.get("store", False) or value.get("parallel_tool_calls", False) or value.get("tool_choice", "none") not in ("auto", "none"):
        _reject()
    if "prompt_cache_key" in value:
        key = value["prompt_cache_key"]
        try:
            if not isinstance(key, str) or len(key) != 36 or str(UUID(key)) != key.lower():
                _reject()
        except (ValueError, TypeError, AttributeError):
            _reject()
    if "include" in value and value["include"] not in ([], ["reasoning.encrypted_content"]):
        _reject()
    if "client_metadata" in value:
        metadata = value["client_metadata"]
        _keys(metadata, _METADATA)
        if any(not isinstance(item, str) or len(item) > 4096 for item in metadata.values()):
            _reject()
    messages, definitions = _messages(value["input"])
    root_tools = value.get("tools", [])
    if not isinstance(root_tools, list):
        _reject()
    definitions = deepcopy(root_tools) + definitions
    _tools(definitions)
    body = {"model": value["model"], "input": messages, "store": False,
            "parallel_tool_calls": False, "tool_choice": "none", "stream": value.get("stream", False)}
    if definitions:
        body["tools"] = definitions
    if "reasoning" in value:
        reasoning = value["reasoning"]
        _keys(reasoning, {"effort", "context"}, {"effort"})
        if reasoning["effort"] not in ("none", "minimal", "low", "medium", "high", "xhigh") or reasoning.get("context", "all_turns") != "all_turns":
            _reject()
        body["reasoning"] = {"effort": reasoning["effort"]}
    if "text" in value:
        _keys(value["text"], {"verbosity"}, {"verbosity"})
        if value["text"]["verbosity"] not in ("low", "medium", "high"):
            _reject()
        body["text"] = deepcopy(value["text"])
    if "max_output_tokens" in value:
        limit = value["max_output_tokens"]
        if type(limit) is not int or not 1 <= limit <= 32768:
            _reject()
        body["max_output_tokens"] = limit
    return _collect(body)
