"""Gemini vision fallback for locating page elements when standard locators fail."""

import base64
import json
import os
import re
import tempfile
import time
from typing import Any

import requests


class GeminiFallbackError(RuntimeError):
    """Raised when the Gemini vision fallback cannot resolve an element."""


_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "found": {"type": "BOOLEAN"},
        "x": {"type": "NUMBER"},
        "y": {"type": "NUMBER"},
        "text": {"type": "STRING"},
        "reasoning": {"type": "STRING"},
        "suggested_locator": {"type": "STRING"},
    },
    "required": ["found"],
}


def _build_prompt(page_url: str, description: str) -> str:
    return (
        "You are a web UI element locator. The attached image is a screenshot of a browser viewport. "
        f"The current page URL is: {page_url}\n"
        f"Find the element described as: \"{description}\".\n"
        "Return the center of the element as normalized coordinates x and y, where 0.0 is the left/top "
        "edge and 1.0 is the right/bottom edge of the image. Also return any visible text inside the "
        "element. If the element is not visible in the screenshot, set found to false.\n"
        "Additionally, provide a Playwright locator expression (using `page` object) that would reliably "
        "locate this element in the future. Use standard Playwright patterns like "
        "`page.get_by_role(...)`, `page.get_by_test_id(...)`, `page.get_by_label(...)`, "
        "`page.locator(...)` with CSS/XPath selectors. The expression should be a valid Python expression "
        "that evaluates to a Playwright Locator.\n"
        "Respond with JSON only and follow the provided schema."
    )


def _parse_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise GeminiFallbackError("Gemini returned a response without a JSON object")
        return json.loads(match.group(0))


def _extract_response_text(payload: dict[str, Any]) -> str:
    for candidate in payload.get("candidates") or []:
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            if isinstance(part, dict) and part.get("text"):
                return part["text"]

    feedback = payload.get("promptFeedback") or {}
    block_reason = feedback.get("blockReason")
    if block_reason:
        raise GeminiFallbackError(f"Gemini blocked the request: {block_reason}")
    raise GeminiFallbackError("Gemini returned an empty response")


def _call_gemini(image_bytes: bytes, prompt: str, config: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise GeminiFallbackError("GEMINI_API_KEY is not set in the environment or .env file")

    gemini_config = config["gemini"]
    model = gemini_config["model"]
    endpoint = f"{gemini_config['api_base'].rstrip('/')}/models/{model}:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": "image/png",
                            "data": base64.b64encode(image_bytes).decode("ascii"),
                        }
                    },
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.0,
            "response_mime_type": "application/json",
            "response_schema": _RESPONSE_SCHEMA,
        },
    }

    max_attempts = max(1, int(gemini_config.get("max_attempts", 3)))
    request_timeout = int(gemini_config.get("request_timeout_seconds", 60))
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=request_timeout,
            )
            if response.status_code != 200:
                detail = response.text[:500]
                raise GeminiFallbackError(
                    f"Gemini API returned HTTP {response.status_code}: {detail}"
                )

            response_payload = response.json()
            return _parse_json(_extract_response_text(response_payload))
        except Exception as error:
            last_error = error
            if attempt < max_attempts:
                time.sleep(1.5 * attempt)

    raise GeminiFallbackError(f"Gemini request failed after {max_attempts} attempts: {last_error}")


def locate_with_gemini(page, description: str, config: dict[str, Any]) -> dict[str, Any]:
    """Capture a screenshot and ask Gemini to locate the described element."""
    if not config["gemini"].get("enabled", True):
        raise GeminiFallbackError("Gemini fallback is disabled in the configuration")

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
            temp_path = temp_file.name

        page.screenshot(path=temp_path, full_page=False)
        with open(temp_path, "rb") as image_file:
            image_bytes = image_file.read()

        prompt = _build_prompt(page.url, description)
        return _call_gemini(image_bytes, prompt, config)
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def perform_action(
    page,
    description: str,
    action: str,
    value: str | None,
    config: dict[str, Any],
) -> tuple[Any, str | None]:
    """Locate an element with Gemini and complete the requested action.

    Returns:
        Tuple of (action_result, suggested_locator_expression_or_None)
    """
    result = locate_with_gemini(page, description, config)

    if not result.get("found"):
        raise GeminiFallbackError(f"Gemini could not find an element described as: {description}")

    try:
        normalized_x = float(result.get("x"))
        normalized_y = float(result.get("y"))
    except (TypeError, ValueError) as error:
        raise GeminiFallbackError("Gemini returned invalid coordinates") from error

    normalized_x = min(max(normalized_x, 0.0), 1.0)
    normalized_y = min(max(normalized_y, 0.0), 1.0)

    viewport = page.evaluate("() => ({ width: window.innerWidth, height: window.innerHeight })")
    target_x = normalized_x * float(viewport["width"])
    target_y = normalized_y * float(viewport["height"])

    suggested_locator = result.get("suggested_locator")

    if action == "click":
        page.mouse.click(target_x, target_y)
        return True, suggested_locator

    if action in ("fill", "type"):
        page.mouse.click(target_x, target_y)
        page.keyboard.press("Control+A")
        delay = int(config["browser"].get("typing_delay_ms", 50))
        page.keyboard.type(str(value if value is not None else ""), delay=delay)
        return True, suggested_locator

    if action == "text":
        return result.get("text"), suggested_locator

    if action == "wait":
        return True, suggested_locator

    raise GeminiFallbackError(f"Unsupported Gemini fallback action: {action}")