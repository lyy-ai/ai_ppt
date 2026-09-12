"""Boundary between the cloud product and the PPT Master core routes.

The cloud API owns authentication, quotas, jobs, storage, and delivery. This
module owns route selection and delegates generation to the core pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable


class CoreRoute(str, Enum):
    GENERATE_PPTX = "generate_pptx"
    CREATE_TEMPLATE = "create_template"
    FILL_NATIVE_PPTX = "fill_native_pptx"
    ENHANCE_NATIVE_PPTX = "enhance_native_pptx"


@dataclass(frozen=True)
class RouteCapability:
    route: CoreRoute
    core_available: bool
    cloud_available: bool
    implementation: str
    reason: str = ""
    session_available: bool = False

    @property
    def available(self) -> bool:
        """Whether this checkout can execute the route through the cloud API."""
        return self.cloud_available


class CoreRouteUnavailable(RuntimeError):
    """Raised when a cloud request targets an unavailable core route."""

    def __init__(self, capability: RouteCapability):
        self.capability = capability
        message = f"PPT Master route '{capability.route.value}' is not available"
        if capability.reason:
            message += f": {capability.reason}"
        super().__init__(message)


def _skill_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _upstream_route_available(route: CoreRoute) -> bool:
    """Detect an installed upstream route without importing implementation code."""
    skill_file = _skill_dir() / "SKILL.md"
    try:
        skill_text = skill_file.read_text(encoding="utf-8")
    except OSError:
        skill_text = ""
    # Route support is a property of the installed upstream core. Do not infer
    # cloud adapter support from the presence of a core helper alone.
    if 'version: "4.2.0"' not in skill_text or not (_skill_dir() / "workflows" / "routing.md").exists():
        return False
    scripts = _skill_dir() / "scripts"
    markers = {
        CoreRoute.CREATE_TEMPLATE: scripts / "pptx_template_import.py",
        CoreRoute.FILL_NATIVE_PPTX: scripts / "template_fill_pptx.py",
        CoreRoute.ENHANCE_NATIVE_PPTX: scripts / "native_enhance_pptx.py",
    }
    marker = markers.get(route)
    return bool(marker and marker.exists())


def route_capabilities() -> dict[str, RouteCapability]:
    """Return the route contract exposed by this checkout."""
    capabilities = {
        CoreRoute.GENERATE_PPTX: RouteCapability(
            CoreRoute.GENERATE_PPTX,
            True,
            True,
            "cloud_generator.orchestrator.run_pipeline",
            session_available=False,
        ),
        CoreRoute.CREATE_TEMPLATE: RouteCapability(
            CoreRoute.CREATE_TEMPLATE,
            _upstream_route_available(CoreRoute.CREATE_TEMPLATE),
            False,
            "upstream PPT Master create-template route",
            "core route is installed; staged intake and package publish are available, but rich hosted authoring is not complete",
            session_available=True,
        ),
        CoreRoute.FILL_NATIVE_PPTX: RouteCapability(
            CoreRoute.FILL_NATIVE_PPTX,
            _upstream_route_available(CoreRoute.FILL_NATIVE_PPTX),
            False,
            "upstream PPT Master template-fill-pptx route",
            "core route is installed; staged native-fill session is available, but it is not a one-shot generation task",
            session_available=True,
        ),
        CoreRoute.ENHANCE_NATIVE_PPTX: RouteCapability(
            CoreRoute.ENHANCE_NATIVE_PPTX,
            _upstream_route_available(CoreRoute.ENHANCE_NATIVE_PPTX),
            False,
            "upstream PPT Master native-enhance-pptx route",
            "core route is installed; staged native-enhance session is available, but it is not a one-shot generation task",
            session_available=True,
        ),
    }
    return {route.value: capability for route, capability in capabilities.items()}


def resolve_route(value: str | CoreRoute | None) -> CoreRoute:
    route = CoreRoute(value or CoreRoute.GENERATE_PPTX)
    capability = route_capabilities()[route.value]
    if not capability.available:
        raise CoreRouteUnavailable(capability)
    return route


def run_core_route(
    *,
    route: str | CoreRoute | None,
    brief_path: str | Path,
    source_path: str | Path,
    output_path: str | Path,
    project_dir: str | Path | None = None,
    keep_project: bool = False,
    max_attempts: int = 2,
    status_callback: Callable[[str, str, dict[str, Any] | None], None] | None = None,
) -> Path:
    """Run a validated core route from the cloud worker boundary."""
    resolved = resolve_route(route)
    if resolved is not CoreRoute.GENERATE_PPTX:
        raise CoreRouteUnavailable(route_capabilities()[resolved.value])

    # Lazy import avoids a circular dependency while the legacy implementation
    # remains in orchestrator.py during the upstream migration.
    from .orchestrator import run_pipeline

    return run_pipeline(
        brief_path=brief_path,
        source_path=source_path,
        output_path=output_path,
        project_dir=project_dir,
        keep_project=keep_project,
        max_attempts=max_attempts,
        status_callback=status_callback,
    )
