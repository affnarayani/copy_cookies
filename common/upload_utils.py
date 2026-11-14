"""OnlyFiles screenshot upload helpers."""

import os
from typing import Any

import requests

from .config import get_config


def upload_to_onlyfiles(screenshot_path: str, config: dict[str, Any] | None = None) -> str | None:
    """Upload a screenshot to OnlyFiles and return its direct URL."""
    config = config or get_config()
    onlyfiles_config = config["onlyfiles"]

    if not onlyfiles_config.get("enabled", True):
        print("[INFO] OnlyFiles upload is disabled in the configuration.", flush=True)
        return None

    if not os.path.exists(screenshot_path):
        print(f"[WARNING] Screenshot not found for upload: {screenshot_path}", flush=True)
        return None

    upload_url = onlyfiles_config["upload_url"]
    expire_seconds = int(onlyfiles_config["expire_seconds"])

    try:
        with open(screenshot_path, "rb") as screenshot_file:
            response = requests.post(
                upload_url,
                files={"file": screenshot_file},
                data={"expire": expire_seconds},
                timeout=60,
            )
    except requests.RequestException as error:
        print(f"[WARNING] OnlyFiles upload failed: {error}", flush=True)
        return None

    if response.status_code != 200:
        print(f"[WARNING] OnlyFiles upload failed with HTTP {response.status_code}", flush=True)
        return None

    try:
        response_data = response.json()
    except ValueError:
        print("[WARNING] OnlyFiles returned a non-JSON response.", flush=True)
        return None

    if not response_data.get("status"):
        error = response_data.get("error") or {}
        message = error.get("message", "Unknown error")
        code = error.get("code")
        print(f"[WARNING] OnlyFiles upload failed: {message} (code: {code})", flush=True)
        return None

    direct_url = response_data["data"]["file"]["url"]["full"]
    print(f"[INFO] Direct link (expires in {expire_seconds} seconds): {direct_url}", flush=True)
    return direct_url


def upload_error_screenshot(screenshot_path: str, config: dict[str, Any] | None = None) -> str | None:
    """Upload the standard error screenshot if it exists."""
    if not os.path.exists(screenshot_path):
        print("[INFO] No error screenshot found to upload.", flush=True)
        return None

    return upload_to_onlyfiles(screenshot_path, config)