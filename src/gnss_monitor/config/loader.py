"""YAML configuration loading with fail-fast validation.

The application must refuse to start on any configuration error, with a
message readable by a human at 3 a.m. over SSH. All YAML and validation
errors are wrapped in ConfigError with the file path included.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Union

import yaml
from pydantic import ValidationError

from gnss_monitor.config.schema import RootConfig


class ConfigError(Exception):
    """Raised when the configuration file is missing, unreadable,
    syntactically invalid, or fails schema validation."""


def _format_validation_error(error: ValidationError) -> str:
    lines: list[str] = []
    for issue in error.errors():
        location = ".".join(str(part) for part in issue["loc"])
        lines.append(f"  - {location}: {issue['msg']}")
    return "\n".join(lines)


def load_config(path: Union[str, Path]) -> RootConfig:
    """Load and validate a YAML configuration file.

    Returns a fully validated RootConfig. Raises ConfigError on any
    problem; never returns a partially valid configuration.
    """
    config_path = Path(path)

    if not config_path.is_file():
        raise ConfigError(f"configuration file not found: {config_path}")

    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"cannot read configuration file {config_path}: {exc}"
        ) from exc

    try:
        data: Any = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"invalid YAML in {config_path}:\n  {exc}"
        ) from exc

    if data is None:
        raise ConfigError(f"configuration file is empty: {config_path}")

    if not isinstance(data, dict):
        raise ConfigError(
            f"configuration root must be a mapping, got "
            f"{type(data).__name__} in {config_path}"
        )

    try:
        return RootConfig(**data)
    except ValidationError as exc:
        raise ConfigError(
            f"invalid configuration in {config_path}:\n"
            f"{_format_validation_error(exc)}"
        ) from exc