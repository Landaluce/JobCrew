"""Load application configuration from config.yaml with config.local.yaml overrides."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge override into base recursively; override wins on conflicts."""
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(base_dir: str | Path | None = None) -> dict[str, Any]:
    """Load configuration from config.yaml and config.local.yaml.

    Both files are resolved relative to ``base_dir`` (defaults to the current
    working directory). Keys from ``config.local.yaml`` deeply merge over
    ``config.yaml``. Returns an empty dict when neither file exists.
    """
    try:
        import yaml
    except ImportError as exc:
        raise ImportError(
            "YAML configuration requires pyyaml. "
            "Install it with: pip install pyyaml"
        ) from exc

    root = Path(base_dir) if base_dir is not None else Path.cwd()
    config: dict[str, Any] = {}

    config_path = root / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

    local_config_path = root / "config.local.yaml"
    if local_config_path.exists():
        with open(local_config_path) as f:
            local_config = yaml.safe_load(f) or {}
            config = _deep_merge(config, local_config)

    return config
