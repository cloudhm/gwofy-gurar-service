"""Parse and inject g.GWOFY_CONFIG in uploaded app-config JS templates."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

CONFIG_INJECT_MARKER = "/*__GWOFY_CONFIG_JSON__*/"

_GWOFY_CONFIG_ASSIGN_RE = re.compile(r"g\.GWOFY_CONFIG\s*=")
_JS_UNQUOTED_KEY_RE = re.compile(r"([{,]\s*)([a-zA-Z_$][\w$]*)\s*:")
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")
_JS_EXPR_TO_NULL_RE = re.compile(r"\bbuildDefaultGwofyStyles\s*\(\s*\)")


def validate_gwofy_config_assignment(source: str) -> None:
    if "g.GWOFY_CONFIG" not in source:
        raise ValueError("app_config_missing_gwofy_config")
    if not _GWOFY_CONFIG_ASSIGN_RE.search(source):
        raise ValueError("app_config_missing_gwofy_config_assignment")


def _strip_js_comments(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    in_str: str | None = None
    escape = False
    while i < n:
        c = text[i]
        c2 = text[i : i + 2] if i + 1 < n else c
        if in_str:
            out.append(c)
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == in_str:
                in_str = None
            i += 1
            continue
        if c2 == "//":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c2 == "/*":
            i += 2
            while i + 1 < n and text[i : i + 2] != "*/":
                i += 1
            i = min(i + 2, n)
            continue
        if c in '"\'`':
            in_str = c
            out.append(c)
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _scan_js_expression_end(source: str, start: int) -> int:
    i = start
    n = len(source)
    paren = brace = bracket = 0
    in_str: str | None = None
    escape = False
    in_line = False
    in_block = False
    while i < n:
        c = source[i]
        c2 = source[i : i + 2] if i + 1 < n else c
        if in_line:
            if c == "\n":
                in_line = False
            i += 1
            continue
        if in_block:
            if c2 == "*/":
                in_block = False
                i += 2
            else:
                i += 1
            continue
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == in_str:
                in_str = None
            i += 1
            continue
        if c2 == "//":
            in_line = True
            i += 2
            continue
        if c2 == "/*":
            in_block = True
            i += 2
            continue
        if c in '"\'`':
            in_str = c
            i += 1
            continue
        if c == "(":
            paren += 1
        elif c == ")":
            paren -= 1
        elif c == "{":
            brace += 1
        elif c == "}":
            brace -= 1
        elif c == "[":
            bracket += 1
        elif c == "]":
            bracket -= 1
        elif c == ";" and paren == brace == bracket == 0:
            return i
        i += 1
    return n


def find_gwofy_config_rhs_span(source: str) -> tuple[int, int] | None:
    m = _GWOFY_CONFIG_ASSIGN_RE.search(source)
    if not m:
        return None
    start = m.end()
    while start < len(source) and source[start].isspace():
        start += 1
    if start >= len(source):
        return None
    end = _scan_js_expression_end(source, start)
    return start, end


def _balanced_brace_end(source: str, open_index: int) -> int | None:
    if open_index >= len(source) or source[open_index] != "{":
        return None
    depth = 0
    in_str: str | None = None
    escape = False
    in_line = False
    in_block = False
    i = open_index
    n = len(source)
    while i < n:
        c = source[i]
        c2 = source[i : i + 2] if i + 1 < n else c
        if in_line:
            if c == "\n":
                in_line = False
            i += 1
            continue
        if in_block:
            if c2 == "*/":
                in_block = False
                i += 2
            else:
                i += 1
            continue
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == in_str:
                in_str = None
            i += 1
            continue
        if c2 == "//":
            in_line = True
            i += 2
            continue
        if c2 == "/*":
            in_block = True
            i += 2
            continue
        if c in '"\'`':
            in_str = c
            i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _js_object_literal_to_dict(object_literal: str) -> dict[str, Any]:
    text = object_literal.strip()
    if not text.startswith("{"):
        return {}
    end = _balanced_brace_end(text, 0)
    if end is None:
        return {}
    text = text[:end]
    text = _strip_js_comments(text)
    text = _JS_EXPR_TO_NULL_RE.sub("null", text)
    text = _JS_UNQUOTED_KEY_RE.sub(r'\1"\2":', text)
    text = _TRAILING_COMMA_RE.sub(r"\1", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: copy.deepcopy(v) for k, v in data.items() if v is not None}


def _extract_object_assign_config_arg(rhs: str) -> str | None:
    rhs = rhs.strip()
    if not rhs.startswith("Object.assign"):
        return None
    open_paren = rhs.find("(")
    if open_paren < 0:
        return None
    depth = 0
    in_str: str | None = None
    escape = False
    args: list[str] = []
    current: list[str] = []
    i = open_paren + 1
    n = len(rhs)
    while i < n:
        c = rhs[i]
        if in_str:
            current.append(c)
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == in_str:
                in_str = None
            i += 1
            continue
        if c in '"\'`':
            in_str = c
            current.append(c)
            i += 1
            continue
        if c == "(":
            depth += 1
            current.append(c)
        elif c == ")":
            if depth == 0:
                if current:
                    args.append("".join(current).strip())
                break
            depth -= 1
            current.append(c)
        elif c == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(c)
        i += 1
    if len(args) >= 2:
        return args[1]
    return None


def parse_script_config_overlay(source: str) -> dict[str, Any]:
    """
    Best-effort overlay from uploaded template (marker, Object.assign 2nd arg, or literal).
    JS function calls (e.g. buildDefaultGwofyStyles()) are omitted from overlay.
    """
    if CONFIG_INJECT_MARKER in source:
        idx = source.find(CONFIG_INJECT_MARKER)
        after = source[idx + len(CONFIG_INJECT_MARKER) :].lstrip()
        if after.startswith("{"):
            end = _balanced_brace_end(after, 0)
            if end:
                parsed = _js_object_literal_to_dict(after[:end])
                if parsed:
                    return parsed

    span = find_gwofy_config_rhs_span(source)
    if not span:
        return {}
    rhs = source[span[0] : span[1]].strip()

    assign_arg = _extract_object_assign_config_arg(rhs)
    if assign_arg:
        if assign_arg.startswith("{"):
            parsed = _js_object_literal_to_dict(assign_arg)
            if parsed:
                return parsed
        if CONFIG_INJECT_MARKER in assign_arg:
            return {}

    if rhs.startswith("{"):
        parsed = _js_object_literal_to_dict(rhs)
        if parsed:
            return parsed
    return {}


def _injection_rhs(template_source: str, config_json: str) -> str:
    if "buildDefaultGwofyStyles" in template_source:
        return f"Object.assign({{ styles: buildDefaultGwofyStyles() }}, {config_json})"
    return config_json


def inject_gwofy_config_into_template(template_source: str, merged_config: dict[str, Any]) -> str:
    config_json = json.dumps(merged_config, ensure_ascii=False)
    if CONFIG_INJECT_MARKER in template_source:
        return template_source.replace(CONFIG_INJECT_MARKER, config_json)

    span = find_gwofy_config_rhs_span(template_source)
    if not span:
        raise ValueError("app_config_missing_gwofy_config_assignment")
    rhs = _injection_rhs(template_source, config_json)
    return template_source[: span[0]] + rhs + template_source[span[1] :]
