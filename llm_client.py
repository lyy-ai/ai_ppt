#!/usr/bin/env python3
"""Small multi-provider chat client for cloud_generator."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o"
ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_DEFAULT_VERSION = "2023-06-01"


def chat_completion(
    *,
    system: str,
    user: str,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    profile: str = "default",
    model: str = "",
) -> str:
    """Call a configured chat completion endpoint.

    Environment variables:
    - LLM_PROVIDER: openai/openai-compatible or anthropic.
    - OPENAI_API_KEY, DEEPSEEK_API_KEY, or ARK_API_KEY: required API key.
    - ANTHROPIC_API_KEY: required when LLM_PROVIDER=anthropic.
    - OPENAI_BASE_URL or LLM_BASE_URL: optional endpoint root, e.g. https://api.deepseek.com
    - ANTHROPIC_BASE_URL: optional Anthropic endpoint root.
    - OPENAI_MODEL or LLM_MODEL: optional model name, e.g. deepseek-chat
    - ANTHROPIC_MODEL: optional Anthropic model name.
    - LLM_MODEL_BRIEF / LLM_MODEL_STRATEGY / LLM_MODEL_SLIDE / LLM_MODEL_SLIDE_FALLBACK:
      optional profile-specific model routes.
    - LLM_TIMEOUT_SECONDS / LLM_TIMEOUT_<PROFILE>_SECONDS: request timeout.
    - LLM_MAX_RETRIES / LLM_MAX_RETRIES_<PROFILE>: retry count.
    - CLOUD_GENERATOR_MOCK=1: return deterministic local responses for tests.
    """
    selected_model = resolve_model_name(profile=profile, model=model)
    if os.environ.get("CLOUD_GENERATOR_MOCK") == "1":
        content = _mock_response(system=system, user=user)
        _record_usage(
            profile=profile,
            model=selected_model,
            usage=_mock_usage(system=system, user=user, content=content),
            response_id="mock",
        )
        return content

    _load_env_files()
    profile_key = _profile_key(profile)
    provider = _resolve_provider(profile_key)
    api_key = _resolve_api_key(profile_key, provider)
    if not api_key:
        raise RuntimeError(
            "No LLM API key found. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, DEEPSEEK_API_KEY, or ARK_API_KEY."
        )

    request = _build_request(
        provider=provider,
        profile_key=profile_key,
        api_key=api_key,
        model=selected_model,
        system=system,
        user=user,
        temperature=temperature,
        max_tokens=max_tokens,
    )

    timeout_seconds = _profile_int(profile_key, "TIMEOUT_SECONDS", 120, minimum=5, maximum=600)
    max_retries = _profile_int(profile_key, "MAX_RETRIES", 3, minimum=1, maximum=6)
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code < 500 or attempt == max_retries:
                raise RuntimeError(f"LLM request failed with HTTP {exc.code}: {detail}") from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == max_retries:
                raise RuntimeError(
                    f"LLM request failed after {max_retries} attempt(s), "
                    f"timeout={timeout_seconds}s, profile={profile}: {exc}"
                ) from exc
            last_error = exc
        time.sleep(1.5 * attempt)
    else:
        raise RuntimeError(f"LLM request failed after retries: {last_error}")

    _record_usage(
        profile=profile,
        model=selected_model,
        usage=body.get("usage") if isinstance(body, dict) else None,
        response_id=str(body.get("id") or "") if isinstance(body, dict) else "",
    )
    return _extract_response_text(provider, body)


def _first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def resolve_model_name(*, profile: str = "default", model: str = "") -> str:
    _load_env_files()
    profile_key = _profile_key(profile)
    provider = _resolve_provider(profile_key)
    if provider == "anthropic":
        return model or _profile_env(profile_key, "MODEL", "ANTHROPIC_MODEL", "LLM_MODEL") or DEFAULT_MODEL
    return model or _profile_env(profile_key, "MODEL", "OPENAI_MODEL", "LLM_MODEL") or DEFAULT_MODEL


def _resolve_provider(profile_key: str) -> str:
    provider = _profile_env(profile_key, "PROVIDER", "LLM_PROVIDER", "CLOUD_GENERATOR_LLM_PROVIDER").strip().lower()
    if provider in {"anthropic", "claude"}:
        return "anthropic"
    return "openai"


def _resolve_api_key(profile_key: str, provider: str) -> str:
    if provider == "anthropic":
        return _profile_env(profile_key, "API_KEY", "ANTHROPIC_API_KEY", "LLM_API_KEY")
    return _profile_env(profile_key, "API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ARK_API_KEY", "LLM_API_KEY")


def _build_request(
    *,
    provider: str,
    profile_key: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    temperature: float,
    max_tokens: int,
) -> urllib.request.Request:
    if provider == "anthropic":
        base_url = _profile_env(profile_key, "BASE_URL", "ANTHROPIC_BASE_URL", "LLM_BASE_URL") or ANTHROPIC_DEFAULT_BASE_URL
        url = _anthropic_messages_url(base_url)
        payload = {
            "model": model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        return urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": os.environ.get("ANTHROPIC_VERSION", ANTHROPIC_DEFAULT_VERSION),
            },
        )

    base_url = _profile_env(profile_key, "BASE_URL", "OPENAI_BASE_URL", "LLM_BASE_URL") or DEFAULT_BASE_URL
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    return urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )


def _anthropic_messages_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    if value.endswith("/v1/messages"):
        return value
    if value.endswith("/v1"):
        return f"{value}/messages"
    return f"{value}/v1/messages"


def _extract_response_text(provider: str, body: dict[str, object]) -> str:
    if provider == "anthropic":
        parts: list[str] = []
        content = body.get("content", []) if isinstance(body, dict) else []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        if parts:
            return "\n".join(parts)
        raise RuntimeError("Anthropic response did not include text content.")
    return body["choices"][0]["message"]["content"]


def _profile_key(profile: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "_", str(profile or "default")).strip("_").upper()
    return value or "DEFAULT"


def _profile_env(profile_key: str, suffix: str, *fallback_names: str) -> str:
    names: list[str] = []
    if profile_key and profile_key != "DEFAULT":
        names.extend([
            f"LLM_{suffix}_{profile_key}",
            f"LLM_{profile_key}_{suffix}",
            f"OPENAI_{suffix}_{profile_key}",
            f"OPENAI_{profile_key}_{suffix}",
            f"DEEPSEEK_{suffix}_{profile_key}",
            f"DEEPSEEK_{profile_key}_{suffix}",
            f"CLOUD_GENERATOR_{profile_key}_{suffix}",
        ])
    names.extend(fallback_names)
    return _first_env(*names)


def _profile_int(profile_key: str, suffix: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = _profile_env(profile_key, suffix, f"LLM_{suffix}")
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _load_env_files() -> None:
    candidates = [
        Path.cwd() / ".env",
        Path.home() / ".ppt-master" / ".env",
    ]
    for path in candidates:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _mock_usage(*, system: str, user: str, content: str) -> dict[str, int]:
    prompt_tokens = max(1, (len(system) + len(user)) // 4)
    completion_tokens = max(1, len(content) // 4)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _record_usage(
    *,
    profile: str,
    model: str,
    usage: object,
    response_id: str = "",
) -> None:
    if not isinstance(usage, dict):
        return
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "profile": profile or "default",
        "model": model or DEFAULT_MODEL,
        "response_id": response_id,
        "usage": usage,
    }
    targets: list[Path] = []
    project_dir = os.environ.get("PPT_MASTER_CURRENT_PROJECT_DIR", "").strip()
    if project_dir:
        targets.append(Path(project_dir) / "llm_usage.jsonl")
    explicit_log = os.environ.get("PPT_MASTER_LLM_USAGE_LOG", "").strip()
    if explicit_log:
        targets.append(Path(explicit_log))

    seen: set[str] = set()
    line = json.dumps(payload, ensure_ascii=False)
    for target in targets:
        key = str(target.resolve()) if target.is_absolute() else str(target)
        if key in seen:
            continue
        seen.add(key)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:
            pass


def _mock_response(*, system: str, user: str) -> str:
    if "presentation design strategist" in system:
        page_count_match = re.search(r"Page count:\s*(\d+)", user)
        page_count = int(page_count_match.group(1)) if page_count_match else 6
        pages = "\n".join(
            f"{idx}. Slide {idx}: Key message {idx}"
            for idx in range(1, page_count + 1)
        )
        outline_blocks = "\n\n".join(
            "#### Slide {idx:02d} - Key message {idx}\n\n"
            "- **Audience move**: move from context to understanding of key message {idx}\n"
            "- **Layout**: single-column briefing composition\n"
            "- **Title**: Key message {idx}\n"
            "- **Core message**: The deck explains key message {idx}.\n"
            "- **Content**: Concise supporting points for key message {idx}."
            .format(idx=idx)
            for idx in range(1, page_count + 1)
        )
        rhythm = "\n".join(
            f"- P{idx:02d}: {'anchor' if idx in (1, page_count) else 'dense'}"
            for idx in range(1, page_count + 1)
        )
        return json.dumps(
            {
                "design_spec": (
                    "# Mock Deck Design Spec\n\n"
                    "## Project Info\n"
                    "- Project Name: Mock Cloud Generator Deck\n"
                    "- Canvas Format: PPT 16:9 (1280x720)\n\n"
                    "## Visual Theme\n"
                    "- Background: #0B1020\n"
                    "- Primary: #5B8DEF\n"
                    "- Accent: #F6C85F\n"
                    "- Text: #F8FAFC\n"
                    "- Secondary Text: #B8C2D8\n\n"
                    "## Typography\n"
                    "- font_family: Arial, sans-serif\n"
                    "- title: 54\n"
                    "- body: 22\n\n"
                    "## Page Outline\n"
                    f"{pages}\n\n"
                    "## IX. Content Outline\n"
                    f"### Part 1: Main story\n\n{outline_blocks}\n"
                ),
                "spec_lock": (
                    "<!-- ppt-master-schema: spec-lock/v1 -->\n"
                    "# Execution Lock\n\n"
                    "## canvas\n"
                    "- viewBox: 0 0 1280 720\n"
                    "- format: PPT 16:9\n\n"
                    "## communication\n"
                    "- audience: general\n"
                    "- objective: Communicate the requested topic clearly to the audience.\n"
                    "- core_message: The deck presents a concise, editable summary of the source request.\n\n"
                    "## mode\n"
                    "- mode: briefing\n\n"
                    "## visual_style\n"
                    "- visual_style: minimal\n\n"
                    "## colors\n"
                    "- bg: #0B1020\n"
                    "- primary: #5B8DEF\n"
                    "- accent: #F6C85F\n"
                    "- text: #F8FAFC\n"
                    "- text_secondary: #B8C2D8\n"
                    "- border: #26324A\n\n"
                    "## typography\n"
                    "- font_family: Arial, sans-serif\n"
                    "- body: 22\n"
                    "- title: 54\n"
                    "- subtitle: 28\n"
                    "- annotation: 18\n"
                    "- title_family: Arial, sans-serif\n"
                    "- body_family: Arial, sans-serif\n\n"
                    "## icons\n"
                    "- library: none\n"
                    "- inventory: none\n\n"
                    "## page_rhythm\n"
                    f"{rhythm}\n\n"
                    "## pptx_structure\n"
                    "- mode: flat\n\n"
                    "## image_generation\n"
                    "- disabled\n\n"
                    "## forbidden\n"
                    "- rgba()\n"
                    "- <style>, class, <foreignObject>, <animate*>, <script>, <iframe>\n"
                ),
            },
            ensure_ascii=False,
        )

    page_match = re.search(r"Page\s+(\d+)\s+of\s+(\d+)", user)
    page_number = int(page_match.group(1)) if page_match else 1
    total_pages = int(page_match.group(2)) if page_match else 1
    title_match = re.search(r"Title:\s*(.+)", user)
    title = title_match.group(1).strip() if title_match else f"Slide {page_number}"
    image_match = re.search(r"Available raster image for this slide:\s*(\S+)", user)
    image_href = image_match.group(1).strip() if image_match else ""
    image_block = ""
    if image_href:
        box_match = re.search(r"Preferred SVG box:\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)", user)
        par_match = re.search(r"preserveAspectRatio:\s*([A-Za-z]+[A-Za-z]*(?:\s+(?:slice|meet))?)", user)
        if box_match:
            img_x, img_y, img_w, img_h = box_match.groups()
        else:
            img_x, img_y, img_w, img_h = "790", "198", "320", "220"
        preserve_aspect_ratio = par_match.group(1).strip() if par_match else "xMidYMid slice"
        image_block = (
            f'  <rect x="{img_x}" y="{img_y}" width="{img_w}" height="{img_h}" rx="24" fill="#0B1020" stroke="#26324A" stroke-width="2"/>\n'
            f'  <image href="{_xml_escape(image_href)}" x="{img_x}" y="{img_y}" width="{img_w}" height="{img_h}" preserveAspectRatio="{_xml_escape(preserve_aspect_ratio)}"/>\n'
        )
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" data-pptx-page-role="content">
  <rect x="0" y="0" width="1280" height="720" fill="#0B1020"/>
  <rect x="72" y="72" width="1136" height="576" rx="28" fill="#121A2F" stroke="#26324A" stroke-width="2"/>
  <text x="104" y="150" font-family="Arial, sans-serif" font-size="28" fill="#5B8DEF">PPT Master Cloud</text>
  <text x="104" y="250" font-family="Arial, sans-serif" font-size="54" font-weight="700" fill="#F8FAFC">{_xml_escape(title)}</text>
  <text x="104" y="330" font-family="Arial, sans-serif" font-size="26" fill="#B8C2D8">Automatically generated editable draft slide.</text>
{image_block}  <circle cx="1128" cy="584" r="38" fill="#5B8DEF"/>
  <text x="104" y="590" font-family="Arial, sans-serif" font-size="18" fill="#F6C85F">Slide {page_number} / {total_pages}</text>
</svg>"""
    return json.dumps(
        {
            "svg": svg,
            "notes": f"# Speaker Notes\n\nThis is a mock speaker note for slide {page_number}.",
        },
        ensure_ascii=False,
    )


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
