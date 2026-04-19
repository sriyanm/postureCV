import json
import os
import time
from typing import Any
from urllib import error, request

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
OLLAMA_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "8"))
OLLAMA_MAX_RETRIES = max(1, int(os.getenv("OLLAMA_MAX_RETRIES", "2")))
OLLAMA_ENABLED = os.getenv("OLLAMA_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
SUMMARY_EMPTY_MESSAGE = (
    "No risk clips yet. Record a session to generate targeted improvement suggestions."
)
SUMMARY_MISSING_DESCRIPTION_MESSAGE = (
    "Risk clips exist, but detailed posture descriptions are missing in older data. "
    "Record new clips to generate pattern-based guidance."
)


def _normalize_description(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def _extract_clip_descriptions(clip: dict[str, Any]) -> list[str]:
    descriptions = clip.get("risk_descriptions")
    if isinstance(descriptions, list):
        cleaned = [str(item or "").strip() for item in descriptions]
        cleaned = [item for item in cleaned if item]
        if cleaned:
            return cleaned

    primary = str(clip.get("primary_risk_description") or "").strip()
    return [primary] if primary else []


def summarize_improvement_patterns_fallback(clips: list[dict[str, Any]]) -> str:
    if not clips:
        return SUMMARY_EMPTY_MESSAGE

    counts: dict[str, dict[str, Any]] = {}
    for clip in clips:
        seen: set[str] = set()
        for description in _extract_clip_descriptions(clip):
            key = _normalize_description(description)
            if not key or key in seen:
                continue
            seen.add(key)
            if key not in counts:
                counts[key] = {"label": description, "count": 0}
            counts[key]["count"] += 1

    ranked = sorted(counts.values(), key=lambda item: (-item["count"], item["label"]))
    if not ranked:
        return SUMMARY_MISSING_DESCRIPTION_MESSAGE

    top = ranked[:3]
    frequent = "; ".join(f'{item["label"]} ({item["count"]})' for item in top)
    return (
        f"From {len(clips)} risk clips, most frequent triggers are: {frequent}. "
        "Start with the highest-frequency pattern first."
    )


def _build_prompt(clips: list[dict[str, Any]]) -> str:
    counts: dict[str, dict[str, Any]] = {}
    for clip in clips:
        level = str(clip.get("risk_level") or "").strip().lower()
        severity_weight = 2 if level == "danger" else 1
        seen: set[str] = set()
        for description in _extract_clip_descriptions(clip):
            key = _normalize_description(description)
            if not key or key in seen:
                continue
            seen.add(key)
            if key not in counts:
                counts[key] = {
                    "label": description,
                    "count": 0,
                    "weighted_count": 0,
                    "danger_count": 0,
                    "warning_count": 0,
                }
            counts[key]["count"] += 1
            counts[key]["weighted_count"] += severity_weight
            if level == "danger":
                counts[key]["danger_count"] += 1
            elif level == "warning":
                counts[key]["warning_count"] += 1

    ranked = sorted(
        counts.values(),
        key=lambda item: (-item["weighted_count"], -item["count"], item["label"]),
    )
    top_patterns = ranked[:6]
    allowed_patterns = [item["label"] for item in top_patterns]
    payload = {
        "clip_count": len(clips),
        "top_patterns": top_patterns,
        "allowed_patterns": allowed_patterns,
        "instruction": (
            "Return strict JSON with key `priorities` only. "
            "`priorities` must be an array of 2-4 objects with shape: "
            '{"pattern":"<exact string from allowed_patterns>","action":"<one concrete posture cue>"} '
            "Use only allowed_patterns as the risk evidence. "
            "Do not add new risk factors, objects, or scene context. "
            "Do not mention chair, desk, keyboard, mouse, monitor, screen, floor, or footrest. "
            "Keep actions practical, non-medical, and <= 20 words each."
        ),
    }
    return json.dumps(payload, ensure_ascii=True)


def _call_ollama_chat(prompt: str) -> str:
    url = f"{OLLAMA_BASE_URL}/api/chat"
    body = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an ergonomic safety assistant. "
                    "Do not provide medical diagnosis. "
                    "Output strict JSON only with grounded priorities from provided patterns."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "format": "json",
        "options": {"temperature": 0.1},
    }
    raw = json.dumps(body).encode("utf-8")
    req = request.Request(
        url=url,
        data=raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=OLLAMA_TIMEOUT_SECONDS) as response:
        decoded = response.read().decode("utf-8")
    parsed = json.loads(decoded)
    message = parsed.get("message")
    if not isinstance(message, dict):
        raise ValueError("Ollama chat response missing message field.")
    content = str(message.get("content") or "").strip()
    if not content:
        raise ValueError("Ollama returned empty summary.")
    return content


def _contains_forbidden_terms(text: str, allowed_patterns: list[str]) -> bool:
    lowered = text.lower()
    allowed_context = " ".join(allowed_patterns).lower()
    blocked_terms = [
        "chair",
        "desk",
        "keyboard",
        "mouse",
        "monitor",
        "screen",
        "floor",
        "footrest",
    ]
    for term in blocked_terms:
        if term in lowered and term not in allowed_context:
            return True
    return False


def _clean_sentence(text: str) -> str:
    cleaned = " ".join(str(text or "").strip().split())
    if not cleaned:
        return cleaned
    cleaned = cleaned[0].upper() + cleaned[1:]
    if cleaned[-1] not in {".", "!", "?"}:
        cleaned += "."
    return cleaned


def _pattern_phrase(pattern: str) -> str:
    phrase = " ".join(str(pattern or "").strip().split())
    if phrase.endswith("."):
        phrase = phrase[:-1]
    if phrase:
        phrase = phrase[0].lower() + phrase[1:]
    return phrase


def _format_grounded_summary(model_json: str, clips: list[dict[str, Any]]) -> str:
    parsed = json.loads(model_json)
    priorities = parsed.get("priorities")
    if not isinstance(priorities, list):
        raise ValueError("Model response missing priorities list.")
    if len(priorities) < 2 or len(priorities) > 4:
        raise ValueError("Model response must contain 2-4 priorities.")

    counts: dict[str, dict[str, Any]] = {}
    for clip in clips:
        level = str(clip.get("risk_level") or "").strip().lower()
        severity_weight = 2 if level == "danger" else 1
        seen: set[str] = set()
        for description in _extract_clip_descriptions(clip):
            key = _normalize_description(description)
            if not key or key in seen:
                continue
            seen.add(key)
            if key not in counts:
                counts[key] = {"label": description, "weighted_count": 0}
            counts[key]["weighted_count"] += severity_weight
    ranked = sorted(counts.values(), key=lambda item: (-item["weighted_count"], item["label"]))
    allowed_patterns = [item["label"] for item in ranked[:6]]
    allowed_map = {_normalize_description(label): label for label in allowed_patterns}

    rendered_lines: list[tuple[str, str]] = []
    seen_patterns: set[str] = set()
    for item in priorities:
        if not isinstance(item, dict):
            raise ValueError("Each priority must be an object.")
        pattern = str(item.get("pattern") or "").strip()
        action = str(item.get("action") or "").strip()
        if not pattern or not action:
            raise ValueError("Each priority requires pattern and action.")
        normalized = _normalize_description(pattern)
        canonical = allowed_map.get(normalized)
        if not canonical:
            raise ValueError("Priority references out-of-scope pattern.")
        if normalized in seen_patterns:
            continue
        if _contains_forbidden_terms(action, allowed_patterns):
            raise ValueError("Action includes forbidden scene/object guidance.")
        seen_patterns.add(normalized)
        rendered_lines.append((canonical, action))

    if len(rendered_lines) < 2:
        raise ValueError("Need at least two valid grounded priorities.")

    coach_sentences: list[str] = []
    for index, (pattern, action) in enumerate(rendered_lines):
        pattern_text = _pattern_phrase(pattern)
        action_text = _clean_sentence(action)
        if index == 0:
            coach_sentences.append(f"Start with the {pattern_text} issue: {action_text}")
        elif index == 1:
            coach_sentences.append(f"Then address to the {pattern_text} issue: {action_text}")
        else:
            coach_sentences.append(f"Next, for the {pattern_text} issue: {action_text}")
    return "Here are some areas of improvement from your recent risk clips: " + " ".join(coach_sentences)


def synthesize_improvement_summary(clips: list[dict[str, Any]]) -> tuple[str, str, str | None]:
    fallback_summary = summarize_improvement_patterns_fallback(clips)
    if not clips or not OLLAMA_ENABLED:
        return fallback_summary, "fallback", None

    has_any_description = any(_extract_clip_descriptions(clip) for clip in clips)
    if not has_any_description:
        return fallback_summary, "fallback", None

    prompt = _build_prompt(clips)
    for attempt in range(OLLAMA_MAX_RETRIES):
        try:
            model_json = _call_ollama_chat(prompt)
            summary = _format_grounded_summary(model_json, clips)
            return summary, "llm", OLLAMA_MODEL
        except (error.URLError, error.HTTPError, TimeoutError, json.JSONDecodeError, ValueError):
            if attempt >= OLLAMA_MAX_RETRIES - 1:
                break
            time.sleep(0.25 * (attempt + 1))
    return fallback_summary, "fallback", None
