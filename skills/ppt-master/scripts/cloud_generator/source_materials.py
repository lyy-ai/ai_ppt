#!/usr/bin/env python3
"""Server-side material handling for cloud_generator.

Browser uploads arrive as JSON-safe base64 blobs. This module persists them
inside the job input workspace, converts supported source formats to Markdown,
and merges everything into the source.md consumed by the generation pipeline.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .security import redact_secrets


SKILL_DIR = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
SOURCE_TO_MD_DIR = SCRIPTS_DIR / "source_to_md"

MAX_MATERIAL_BYTES = 30 * 1024 * 1024
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".csv", ".json", ".xml", ".yaml", ".yml"}
PDF_SUFFIXES = {".pdf"}
DOC_SUFFIXES = {".docx", ".html", ".htm", ".epub", ".ipynb"}
PPT_SUFFIXES = {".pptx", ".ppt", ".ppsx"}
EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls", ".csv"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | PDF_SUFFIXES | DOC_SUFFIXES | PPT_SUFFIXES | EXCEL_SUFFIXES
SUPPORTED_SUFFIXES |= AUDIO_SUFFIXES


def materialize_source(
    *,
    input_dir: Path,
    source_text: str = "",
    materials: list[dict[str, Any]] | None = None,
    source_urls: list[str] | None = None,
) -> Path:
    """Persist uploaded materials and return a merged Markdown source file."""
    input_dir.mkdir(parents=True, exist_ok=True)
    material_dir = input_dir / "materials"
    extracted_dir = input_dir / "extracted"
    material_dir.mkdir(parents=True, exist_ok=True)
    extracted_dir.mkdir(parents=True, exist_ok=True)

    chunks: list[str] = []
    if source_text.strip():
        chunks.append("# User Request And Browser-Readable Source\n\n" + source_text.strip())

    for item in materials or []:
        saved = _save_material(item, material_dir)
        converted = _convert_material(saved, extracted_dir)
        chunks.append(f"# Uploaded Material: {saved.name}\n\n{converted.strip()}")

    for index, url in enumerate(_normalize_urls(source_urls or []), start=1):
        converted = _convert_url(url, extracted_dir, index)
        chunks.append(f"# Web Source: {url}\n\n{converted.strip()}")

    if not chunks:
        raise ValueError("Provide source_text or material_files")

    source_file = input_dir / "source.md"
    source_file.write_text("\n\n---\n\n".join(chunks).strip() + "\n", encoding="utf-8")
    return source_file


def _normalize_urls(values: list[str]) -> list[str]:
    urls: list[str] = []
    for value in values:
        url = str(value or "").strip()
        if not url:
            continue
        if not re.match(r"^https?://", url, re.IGNORECASE):
            continue
        _validate_fetch_url(url)
        if url not in urls:
            urls.append(url)
    return urls[:10]


def _validate_fetch_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("URL host is required")
    if os.environ.get("PPT_MASTER_ALLOW_PRIVATE_URLS") == "1":
        return
    if host in {"localhost"} or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError(f"Private or local URL is not allowed: {url}")
    try:
        ip = ipaddress.ip_address(host)
        if _is_blocked_ip(ip):
            raise ValueError(f"Private or local URL is not allowed: {url}")
        return
    except ValueError as exc:
        if "not allowed" in str(exc):
            raise
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve URL host: {host}") from exc
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise ValueError(f"Private or local URL resolves to blocked address {address}: {url}")


def _is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _convert_url(url: str, extracted_dir: Path, index: int) -> str:
    output_path = extracted_dir / f"web_{index:02d}.md"
    script = SOURCE_TO_MD_DIR / "web_to_md.py"
    cmd = [sys.executable, str(script), url, "-o", str(output_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    log_path = output_path.with_suffix(".log")
    log_path.write_text(
        "\n".join([
            f"$ {' '.join(cmd)}",
            "",
            "STDOUT:",
            redact_secrets(result.stdout),
            "",
            "STDERR:",
            redact_secrets(result.stderr),
        ]),
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(redact_secrets(f"web_to_md.py failed for {url}:\n{result.stderr}\n{result.stdout}"))
    if not output_path.exists():
        raise RuntimeError(f"web_to_md.py did not create {output_path}")
    return output_path.read_text(encoding="utf-8", errors="replace")


def _save_material(item: dict[str, Any], material_dir: Path) -> Path:
    name = _safe_filename(str(item.get("name") or "upload.bin"))
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported uploaded file type `{suffix or 'unknown'}` for {name}")
    encoded = str(item.get("content_base64") or "")
    if not encoded:
        raise ValueError(f"Uploaded material {name} is missing content_base64")
    if "," in encoded and encoded.lstrip().startswith("data:"):
        encoded = encoded.split(",", 1)[1]
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Uploaded material {name} is not valid base64") from exc
    if len(data) > MAX_MATERIAL_BYTES:
        raise ValueError(f"Uploaded material {name} exceeds {MAX_MATERIAL_BYTES} bytes")
    _validate_material_signature(name, suffix, data)

    target = _unique_path(material_dir / name)
    target.write_bytes(data)
    try:
        _scan_material(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target


def _validate_material_signature(name: str, suffix: str, data: bytes) -> None:
    """Reject obvious executable payloads before running conversion tools."""
    header = data[:16]
    if header.startswith(b"MZ") or header.startswith(b"\x7fELF"):
        raise ValueError(f"Uploaded material {name} looks like an executable file")
    if header.startswith((b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe")):
        raise ValueError(f"Uploaded material {name} looks like a Mach-O executable file")
    if suffix in PDF_SUFFIXES and not data.lstrip().startswith(b"%PDF-"):
        raise ValueError(f"Uploaded material {name} does not look like a valid PDF")
    if suffix in {".docx", ".xlsx", ".xlsm", ".pptx", ".ppsx", ".epub"}:
        if not header.startswith(b"PK\x03\x04") and not header.startswith(b"PK\x05\x06") and not header.startswith(b"PK\x07\x08"):
            raise ValueError(f"Uploaded material {name} does not look like a valid zipped document")
    if suffix in {".doc", ".xls", ".ppt"} and not header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise ValueError(f"Uploaded material {name} does not look like a valid Office document")


def _scan_material(path: Path) -> None:
    """Run an optional malware scanner command against an uploaded file."""
    scanner = os.environ.get("PPT_MASTER_CLAMSCAN_BIN", "").strip()
    if not scanner:
        return
    with tempfile.TemporaryDirectory(prefix="ppt-master-scan-") as temp_dir:
        log_path = Path(temp_dir) / "clamscan.log"
        cmd = [scanner, "--no-summary", str(path)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        log_path.write_text(
            "\n".join([
                f"$ {' '.join(cmd)}",
                "",
                "STDOUT:",
                redact_secrets(result.stdout),
                "",
                "STDERR:",
                redact_secrets(result.stderr),
            ]),
            encoding="utf-8",
        )
        if result.returncode != 0:
            raise ValueError(f"Uploaded material {path.name} did not pass malware scan")


def _convert_material(path: Path, extracted_dir: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in AUDIO_SUFFIXES:
        return f"Audio asset saved as `{path.name}` for a route-specific narration workflow."
    if suffix in TEXT_SUFFIXES:
        return path.read_text(encoding="utf-8", errors="replace")

    out_file = extracted_dir / f"{path.stem}.md"
    if suffix in PDF_SUFFIXES:
        return _run_converter("pdf_to_md.py", path, out_file)
    if suffix in DOC_SUFFIXES:
        return _run_converter("doc_to_md.py", path, out_file)
    if suffix in PPT_SUFFIXES:
        return _run_converter("ppt_to_md.py", path, out_file)
    if suffix in EXCEL_SUFFIXES:
        return _run_converter("excel_to_md.py", path, out_file)

    return (
        f"Unsupported uploaded file type `{suffix or 'unknown'}`. "
        f"The file was saved as `{path.name}` but was not converted."
    )


def _run_converter(script_name: str, input_path: Path, output_path: Path) -> str:
    script = SOURCE_TO_MD_DIR / script_name
    cmd = [sys.executable, str(script), str(input_path), "-o", str(output_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    log_path = output_path.with_suffix(".log")
    log_path.write_text(
        "\n".join([
            f"$ {' '.join(cmd)}",
            "",
            "STDOUT:",
            redact_secrets(result.stdout),
            "",
            "STDERR:",
            redact_secrets(result.stderr),
        ]),
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(redact_secrets(f"{script_name} failed for {input_path.name}:\n{result.stderr}\n{result.stdout}"))
    if not output_path.exists():
        raise RuntimeError(f"{script_name} did not create {output_path}")
    return output_path.read_text(encoding="utf-8", errors="replace")


def _safe_filename(name: str) -> str:
    name = Path(name).name.strip() or "upload.bin"
    name = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", name)
    return name[:160] or "upload.bin"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not allocate unique filename for {path.name}")
