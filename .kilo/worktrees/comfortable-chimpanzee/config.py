"""Central configuration loader for the cookie automation project."""

import json
import os
from functools import lru_cache
from typing import Any

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT_DIR, "json", "config.json")

_REQUIRED_SECTIONS = ("paths", "browser", "waits", "onlyfiles", "gemini", "encryption", "retry", "git", "destinations")


def resolve_path(path: str) -> str:
    """Resolve a project-relative path against the project root."""
    if not path:
        return path
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(ROOT_DIR, path))


def _validate(config: dict[str, Any]) -> None:
    missing = [section for section in _REQUIRED_SECTIONS if section not in config]
    if missing:
        raise ValueError(f"Configuration is missing required sections: {', '.join(missing)}")

    required_paths = (
        "accounts_file",
        "output_dir",
        "error_dir",
        "error_screenshot",
        "chatgpt_locators_file",
        "atomicmail_locators_file",
        "cookies_file",
        "encrypted_cookies_file",
    )
    missing_paths = [key for key in required_paths if key not in config["paths"]]
    if missing_paths:
        raise ValueError(f"Configuration is missing required paths: {', '.join(missing_paths)}")

    if not config["destinations"]:
        raise ValueError("Configuration must contain at least one destination")


@lru_cache(maxsize=1)
def get_config() -> dict[str, Any]:
    """Load and cache the centralized JSON configuration."""
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"Configuration file not found: {CONFIG_PATH}")

    with open(CONFIG_PATH, "r", encoding="utf-8") as config_file:
        config = json.load(config_file)

    _validate(config)
    return config


def reload_config() -> dict[str, Any]:
    """Clear the configuration cache and load it again."""
    get_config.cache_clear()
    return get_config()