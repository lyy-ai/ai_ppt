#!/usr/bin/env python3
"""Brief JSON Schema — the contract between the frontend Brief Assistant and the backend pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any

from .core_adapter import CoreRoute


class Scenario(str, Enum):
    EXECUTIVE_REPORT = "executive_report"
    BUSINESS_PROPOSAL = "business_proposal"
    ACADEMIC_DEFENSE = "academic_defense"
    PAPER_READING = "paper_reading"
    COURSE_LECTURE = "course_lecture"
    PRODUCT_INTRO = "product_intro"
    DATA_BRIEFING = "data_briefing"
    GENERAL = "general"


class Audience(str, Enum):
    BOSS = "boss"
    CLIENT = "client"
    INVESTOR = "investor"
    COLLEAGUE = "colleague"
    STUDENT = "student"
    PUBLIC = "public"
    GENERAL = "general"


class Goal(str, Enum):
    DECISION_SUPPORT = "decision_support"
    PROGRESS_UPDATE = "progress_update"
    PERSUADE = "persuade"
    EDUCATE = "educate"
    INFORM = "inform"
    GENERAL = "general"


class Tone(str, Enum):
    CONCISE_PROFESSIONAL = "concise_professional"
    DATA_DRIVEN = "data_driven"
    STORYTELLING = "storytelling"
    ACADEMIC = "academic"
    CASUAL = "casual"
    GENERAL = "general"


class Style(str, Enum):
    BUSINESS_DARK = "business_dark"
    BUSINESS_LIGHT = "business_light"
    ACADEMIC = "academic"
    MINIMAL = "minimal"
    CREATIVE = "creative"
    DATA_REPORT = "data_report"
    GENERAL = "general"


class Language(str, Enum):
    ZH_CN = "zh-CN"
    EN = "en"
    ZH_EN = "zh-EN"


@dataclass
class Brief:
    """Structured task brief produced by the frontend Brief Assistant.

    This is the single source of truth that the backend pipeline consumes.
    The frontend chat UI is responsible for collecting these fields through
    guided conversation, then submitting a validated Brief to the task API.
    """

    scenario: str = "general"
    audience: str = "general"
    goal: str = "general"
    tone: str = "general"
    style: str = "general"
    page_count: int = 10
    language: str = "zh-CN"
    route: str = CoreRoute.GENERATE_PPTX.value

    include_speaker_notes: bool = False
    include_charts: bool = False
    include_images: bool = False
    include_animations: bool = False
    include_audio: bool = False
    include_svg_snapshot: bool = False
    merge_paragraphs: bool = False
    image_source_mode: str = "auto"
    audio_provider: str = "edge"
    audio_voice: str = ""

    must_include: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)

    source_files: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    source_text: str = ""

    template_id: str = ""
    template_kind: str = ""
    template_path: str = ""

    user_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Brief":
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    @classmethod
    def from_json(cls, text: str) -> "Brief":
        return cls.from_dict(json.loads(text))

    @classmethod
    def from_file(cls, path: str | Path) -> "Brief":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.page_count < 4 or self.page_count > 40:
            errors.append(f"page_count must be 4-40, got {self.page_count}")
        if self.scenario not in Scenario._value2member_map_:
            errors.append(f"unknown scenario: {self.scenario}")
        if self.audience not in Audience._value2member_map_:
            errors.append(f"unknown audience: {self.audience}")
        if self.goal not in Goal._value2member_map_:
            errors.append(f"unknown goal: {self.goal}")
        if self.tone not in Tone._value2member_map_:
            errors.append(f"unknown tone: {self.tone}")
        if self.style not in Style._value2member_map_:
            errors.append(f"unknown style: {self.style}")
        if self.language not in Language._value2member_map_:
            errors.append(f"unknown language: {self.language}")
        if self.route not in CoreRoute._value2member_map_:
            errors.append(f"unknown route: {self.route}")
        if self.template_kind and self.template_kind not in {"brand", "layout", "deck"}:
            errors.append(f"unknown template_kind: {self.template_kind}")
        if self.image_source_mode and self.image_source_mode not in {"auto", "generate", "search", "none"}:
            errors.append(f"unknown image_source_mode: {self.image_source_mode}")
        if self.audio_provider and self.audio_provider not in {"edge", "elevenlabs", "minimax", "qwen", "cosyvoice"}:
            errors.append(f"unknown audio_provider: {self.audio_provider}")
        if not self.source_files and not self.source_urls and not self.source_text.strip():
            errors.append("at least one of source_files, source_urls, or source_text is required")
        return errors


BRIEF_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "PPT Master Task Brief",
    "type": "object",
    "required": [
        "scenario",
        "audience",
        "goal",
        "tone",
        "style",
        "page_count",
        "language",
    ],
    "properties": {
        "scenario": {"type": "string", "enum": [item.value for item in Scenario]},
        "audience": {"type": "string", "enum": [item.value for item in Audience]},
        "goal": {"type": "string", "enum": [item.value for item in Goal]},
        "tone": {"type": "string", "enum": [item.value for item in Tone]},
        "style": {"type": "string", "enum": [item.value for item in Style]},
        "page_count": {"type": "integer", "minimum": 4, "maximum": 40},
        "language": {"type": "string", "enum": [item.value for item in Language]},
        "route": {"type": "string", "enum": [item.value for item in CoreRoute], "default": CoreRoute.GENERATE_PPTX.value},
        "include_speaker_notes": {"type": "boolean", "default": False},
        "include_charts": {"type": "boolean", "default": False},
        "include_images": {"type": "boolean", "default": False},
        "include_animations": {"type": "boolean", "default": False},
        "include_audio": {"type": "boolean", "default": False},
        "include_svg_snapshot": {"type": "boolean", "default": False},
        "merge_paragraphs": {"type": "boolean", "default": False},
        "image_source_mode": {"type": "string", "enum": ["auto", "generate", "search", "none"], "default": "auto"},
        "audio_provider": {"type": "string", "enum": ["edge", "elevenlabs", "minimax", "qwen", "cosyvoice"], "default": "edge"},
        "audio_voice": {"type": "string"},
        "must_include": {"type": "array", "items": {"type": "string"}},
        "avoid": {"type": "array", "items": {"type": "string"}},
        "source_files": {"type": "array", "items": {"type": "string"}},
        "source_urls": {"type": "array", "items": {"type": "string"}},
        "source_text": {"type": "string"},
        "template_id": {"type": "string"},
        "template_kind": {"type": "string", "enum": ["", "brand", "layout", "deck"]},
        "template_path": {"type": "string"},
        "user_notes": {"type": "string"},
    },
}


def write_schema(path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(BRIEF_JSON_SCHEMA, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
