"""Centralized locator repository and AI-assisted locator actions."""

import json
import os
import re
from datetime import datetime
from typing import Any, Iterator

from .config import get_config, resolve_path
from .gemini_fallback import perform_action, GeminiFallbackError


class LocatorNotFoundError(LookupError):
    """Raised when a named locator is not present in a platform locator file."""


class LocatorActionError(RuntimeError):
    """Raised when neither the standard locator nor the Gemini fallback succeeds."""


CHANGED_LOCATORS_FILE = "locators/changed_locators.json"


def _get_changed_locators_path(config: dict[str, Any]) -> str:
    """Get the resolved path for the changed_locators.json file."""
    return resolve_path(CHANGED_LOCATORS_FILE)


def _load_changed_locators(config: dict[str, Any]) -> dict:
    """Load the changed locators mappings from JSON file."""
    changed_file = _get_changed_locators_path(config)
    if not os.path.exists(changed_file):
        return {}
    try:
        with open(changed_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_changed_locators(config: dict[str, Any], mappings: dict) -> None:
    """Save the changed locators mappings to JSON file."""
    changed_file = _get_changed_locators_path(config)
    try:
        os.makedirs(os.path.dirname(changed_file), exist_ok=True)
        with open(changed_file, "w", encoding="utf-8") as f:
            json.dump(mappings, f, indent=2, ensure_ascii=False)
    except OSError as error:
        print(f"[WARNING] Failed to save changed locators file: {error}", flush=True)


def _get_locator_file_path(config: dict[str, Any], platform: str) -> str:
    """Get the resolved path for a platform's locator file."""
    file_key = f"{platform}_locators_file"
    return resolve_path(config["paths"][file_key])


def _read_locator_file(file_path: str) -> dict[str, list[str]]:
    """Read locator file and return dict of name -> list of expression lines."""
    if not os.path.exists(file_path):
        return {}
    entries = {}
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            name, rest = line.split("=", 1)
            name = name.strip()
            expression = rest.split("::")[0].strip()
            entries.setdefault(name, []).append(expression)
    return entries


def _write_locator_file(file_path: str, entries: dict[str, list[str]]) -> None:
    """Write locator file from dict of name -> list of expression lines.
    Preserves existing comments and formatting by rewriting the file.
    """
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("# ==========================================================================\n")
        if "chatgpt" in file_path:
            f.write("# ChatGPT locators\n")
        elif "atomicmail" in file_path:
            f.write("# Atomic Mail locators\n")
        f.write("# --------------------------------------------------------------------------\n")
        f.write("# Each line is a real Playwright locator expression, identical to what the\n")
        f.write("# Python scripts used before this file existed:\n")
        f.write("#\n")
        f.write("#     key = <playwright expression> :: <vision description>\n")
        f.write("#\n")
        f.write("# - The expression is evaluated with `page` (the current Playwright Page) and\n")
        f.write("#   `re` in scope. Edit it exactly as you would edit the Python call.\n")
        f.write("# - Everything after ` ::` is optional. It is the natural-language description\n")
        f.write("#   handed to Gemini Vision when the expression matches nothing, so keep it\n")
        f.write("#   specific about position, label, and icon.\n")
        f.write("# - `{placeholder}` values are filled by the calling script.\n")
        f.write("# - Use `||`-free syntax: a key holds exactly ONE expression. For alternative\n")
        f.write("#   selectors, chain them with Playwright's own `.or_(...)`)\n")
        f.write("#\n")
        f.write("# Keep this file free of Python-side locators: scripts must only reference keys.\n")
        f.write("# ==========================================================================\n\n")

        for name, expressions in entries.items():
            for expr in expressions:
                f.write(f"{name} = {expr} ::\n")


def _auto_correct_locators(config: dict[str, Any]) -> None:
    """Auto-correct locator txt files from changed_locators.json on startup.

    Behavior based on config["locator"]["auto_correct_locator"]:
    - True (ON): Update txt files with suggested locators (REPLACE existing), remove corrected entries from JSON
    - False (OFF): Only remove entries from JSON if suggested locator already in txt file
    """
    auto_correct = config.get("locator", {}).get("auto_correct_locator", True)
    mappings = _load_changed_locators(config)
    if not mappings:
        return

    platforms = {"chatgpt", "atomicmail"}
    entries_to_remove = []

    for key, mapping in mappings.items():
        platform = mapping.get("platform")
        locator_name = mapping.get("locator_name")
        suggested = mapping.get("suggested_locator")

        if platform not in platforms or not locator_name or not suggested:
            continue

        locator_file = _get_locator_file_path(config, platform)
        current_entries = _read_locator_file(locator_file)

        # Check if suggested locator is already in the file
        existing_expressions = current_entries.get(locator_name, [])
        suggested_stripped = suggested.strip()
        already_exists = any(suggested_stripped == expr.strip() for expr in existing_expressions)

        if already_exists:
            # Suggested locator already in file - remove from tracking
            entries_to_remove.append(key)
            if auto_correct:
                print(f"[INFO] Auto-correct: Locator '{locator_name}' already updated in {platform} file. Removing from tracking.", flush=True)
            else:
                print(f"[INFO] Locator '{locator_name}' already manually updated in {platform} file. Removing from tracking.", flush=True)
        elif auto_correct:
            # Auto-correct: APPEND suggested locator as new fallback entry (preserve existing fallbacks)
            if locator_name not in current_entries:
                current_entries[locator_name] = []
            # Add suggested locator as a new fallback (insert at position 1, after primary)
            # This way the original primary stays first, suggested becomes second fallback
            if len(current_entries[locator_name]) >= 1:
                current_entries[locator_name].insert(1, suggested_stripped)
            else:
                current_entries[locator_name].append(suggested_stripped)
            _write_locator_file(locator_file, current_entries)
            entries_to_remove.append(key)
            print(f"[INFO] Auto-correct: Added '{locator_name}' suggested locator as fallback in {platform} file. Removing from tracking.", flush=True)
        else:
            # Manual mode: suggested not in file yet, keep tracking
            print(f"[INFO] Manual mode: Locator '{locator_name}' in {platform} file not yet updated. Suggested: {suggested_stripped[:80]}...", flush=True)

    # Remove corrected entries from JSON
    if entries_to_remove:
        for key in entries_to_remove:
            del mappings[key]
        _save_changed_locators(config, mappings)
        # Clear repository cache so new locators are loaded
        reload_locator_repositories()


def _save_changed_locator(
    platform: str,
    locator_name: str,
    broken_expression: str,
    suggested_expression: str,
    config: dict[str, Any],
) -> None:
    """Save or update a broken/suggested locator mapping.

    Key logic:
    - Key is platform.locator_name (prevents duplicates)
    - If broken_expression == suggested_expression: remove entry (locator now matches)
    - If entry exists: keep original broken_locator, update suggested_locator and timestamp
    - If new entry: create with current broken_expression as broken_locator
    """
    # Normalize expressions for comparison (strip whitespace)
    broken_norm = broken_expression.strip()
    suggested_norm = suggested_expression.strip()

    # If they're the same, the locator in code matches Gemini's suggestion → no tracking needed
    if broken_norm == suggested_norm:
        _remove_fixed_locator(platform, locator_name, config)
        return

    try:
        mappings = _load_changed_locators(config)
        key = f"{platform}.{locator_name}"

        if key in mappings:
            # Update existing: keep original broken_locator, update suggested_locator
            mappings[key]["suggested_locator"] = suggested_expression
            mappings[key]["timestamp"] = datetime.utcnow().isoformat() + "Z"
            mappings[key]["reason"] = f"Gemini Vision fallback suggested updated locator (re-failure)"
            print(f"[INFO] Updated locator change mapping: {key} -> suggested: {suggested_expression[:80]}...", flush=True)
        else:
            # New entry
            mappings[key] = {
                "platform": platform,
                "locator_name": locator_name,
                "broken_locator": broken_expression,
                "suggested_locator": suggested_expression,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "reason": f"Gemini Vision fallback succeeded where standard locator failed",
            }
            print(f"[INFO] Saved new locator change mapping: {key} -> suggested: {suggested_expression[:80]}...", flush=True)

        _save_changed_locators(config, mappings)
    except Exception as error:
        print(f"[WARNING] Failed to save changed locator mapping: {error}", flush=True)


def _remove_fixed_locator(platform: str, locator_name: str, config: dict[str, Any]) -> bool:
    """Remove a locator entry from changed_locators.json when it starts working again.

    Returns True if an entry was removed, False if no entry existed.
    """
    try:
        mappings = _load_changed_locators(config)
        key = f"{platform}.{locator_name}"

        if key in mappings:
            del mappings[key]
            _save_changed_locators(config, mappings)
            print(f"[INFO] Removed fixed locator from tracking: {key}", flush=True)
            return True
        return False
    except Exception as error:
        print(f"[WARNING] Failed to remove fixed locator: {error}", flush=True)
        return False


def _check_primary_locator_works(page, repository, locator_name: str) -> bool:
    """Check if the primary (first) locator expression for a name currently works.

    This is called when a standard locator succeeds to detect if a previously
    broken locator has been fixed (by developer update to locator file).
    """
    try:
        expressions = repository.get_expressions(locator_name)
        if not expressions:
            return False
        # Try to evaluate the primary expression
        primary_expr = expressions[0]
        local_vars = {"page": page, "re": re}
        eval(primary_expr, {"__builtins__": {}}, local_vars)
        return True
    except Exception:
        return False


class LocatorRepository:
    """Loads and evaluates Playwright locator expressions from a platform locator text file.

    Format: variable_name = <playwright expression> :: <vision description>
    Multiple lines with the same variable_name are tried in order.
    The vision description (after ::) is optional and used for Gemini Vision fallback.
    """

    def __init__(self, file_path: str):
        self.file_path = file_path
        self._entries: dict[str, list[tuple[str, str]]] = {}  # name -> list of (expression, vision_description)
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"Locator file not found: {self.file_path}")

        with open(self.file_path, "r", encoding="utf-8") as locator_file:
            for line_number, raw_line in enumerate(locator_file, start=1):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue

                if "=" not in line:
                    raise ValueError(
                        f"Invalid locator line {line_number} in {self.file_path}: expected "
                        "'variable_name = expression :: vision_description'"
                    )

                name, rest = line.split("=", 1)
                name = name.strip()

                # Split expression from optional vision description
                if "::" in rest:
                    expression, vision_description = rest.split("::", 1)
                    expression = expression.strip()
                    vision_description = vision_description.strip()
                else:
                    expression = rest.strip()
                    vision_description = f"Locator '{name}' from {self.file_path}"

                if not name or not expression:
                    raise ValueError(
                        f"Invalid locator line {line_number} in {self.file_path}: "
                        "variable_name and expression are required"
                    )

                self._entries.setdefault(name, []).append((expression, vision_description))

        if not self._entries:
            raise ValueError(f"Locator file contains no locators: {self.file_path}")

    def get(self, name: str) -> list[tuple[str, str]]:
        """Return all (expression, vision_description) pairs defined for a locator name."""
        if name not in self._entries:
            raise LocatorNotFoundError(
                f"Locator '{name}' was not found in {self.file_path}"
            )
        return self._entries[name]

    def get_expressions(self, name: str) -> list[str]:
        """Return just the expressions for a locator name."""
        return [expr for expr, _ in self.get(name)]

    def get_vision_description(self, name: str, index: int = 0) -> str:
        """Return the vision description for a specific expression index."""
        entries = self.get(name)
        if index < len(entries):
            return entries[index][1]
        return f"Locator '{name}' from {self.file_path}"

    def evaluate(self, page, expression: str) -> Any:
        """Evaluate a locator expression with the given page."""
        # Make 'page' and 're' available for the expression
        local_vars = {"page": page, "re": re}
        try:
            return eval(expression, {"__builtins__": {}}, local_vars)
        except Exception as error:
            raise LocatorActionError(
                f"Failed to evaluate locator expression '{expression}': {error}"
            ) from error


class SmartLocator:
    """Runs standard locator expressions first, then the Gemini vision fallback."""

    def __init__(self, page, repository: LocatorRepository, name: str, config: dict[str, Any]):
        self.page = page
        self.repository = repository
        self.name = name
        self.config = config
        self.expressions = repository.get_expressions(name)
        self.vision_description = repository.get_vision_description(name, 0)
        self.default_timeout = int(config["browser"].get("locator_timeout_ms", 5000))

    def _timeout(self, timeout: int | None) -> int:
        return self.default_timeout if timeout is None else int(timeout)

    def _iter_locators(self) -> Iterator[Any]:
        for expression in self.expressions:
            try:
                yield self.repository.evaluate(self.page, expression)
            except Exception:
                continue

    def raw(self) -> Any:
        """Return the first standard Playwright locator for this name."""
        return self.repository.evaluate(self.page, self.expressions[0])

    def _get_platform(self) -> str:
        """Determine platform from repository file path."""
        return "chatgpt" if "chatgpt" in self.repository.file_path else "atomicmail"

    def _on_standard_success(self) -> None:
        """Called when a standard locator (primary expression) succeeds.
        Checks if this locator was previously tracked as broken and removes it if so.
        """
        platform = self._get_platform()
        # Check if the primary expression now works (meaning developer fixed it)
        if _check_primary_locator_works(self.page, self.repository, self.name):
            _remove_fixed_locator(platform, self.name, self.config)

    def _handle_fallback_result(
        self,
        action: str,
        result: tuple[Any, str | None],
        last_error: Exception | None,
    ) -> Any:
        """Handle the result from Gemini fallback, saving suggested locator if provided."""
        action_result, suggested_locator = result

        if suggested_locator:
            platform = self._get_platform()
            _save_changed_locator(
                platform=platform,
                locator_name=self.name,
                broken_expression=self.expressions[0] if self.expressions else "unknown",
                suggested_expression=suggested_locator,
                config=self.config,
            )

        return action_result

    def click(
        self,
        timeout: int | None = None,
        button: str = "left",
        click_count: int = 1,
    ) -> bool:
        last_error: Exception | None = None
        for locator in self._iter_locators():
            try:
                locator.first.click(
                    timeout=self._timeout(timeout),
                    button=button,
                    click_count=click_count,
                )
                # Standard locator succeeded - check if we can clean up tracking
                self._on_standard_success()
                return True
            except Exception as error:
                last_error = error

        try:
            result = perform_action(
                self.page,
                self.vision_description,
                "click",
                None,
                self.config,
            )
            return self._handle_fallback_result("click", result, last_error)
        except Exception as fallback_error:
            raise LocatorActionError(
                f"Could not click locator '{self.name}': {last_error}; "
                f"Gemini fallback failed: {fallback_error}"
            ) from fallback_error

    def fill(self, value: str, timeout: int | None = None) -> bool:
        last_error: Exception | None = None
        for locator in self._iter_locators():
            try:
                locator.first.fill(value, timeout=self._timeout(timeout))
                self._on_standard_success()
                return True
            except Exception as error:
                last_error = error

        try:
            result = perform_action(
                self.page,
                self.vision_description,
                "fill",
                value,
                self.config,
            )
            return self._handle_fallback_result("fill", result, last_error)
        except Exception as fallback_error:
            raise LocatorActionError(
                f"Could not fill locator '{self.name}': {last_error}; "
                f"Gemini fallback failed: {fallback_error}"
            ) from fallback_error

    def is_visible(self, timeout: int | None = None) -> bool:
        """Return whether any standard candidate is visible.

        Optional presence checks intentionally do not call Gemini: a missing optional
        element is a valid result and must not trigger a paid vision request.
        """
        for locator in self._iter_locators():
            try:
                if locator.first.is_visible(timeout=self._timeout(timeout)):
                    self._on_standard_success()
                    return True
            except Exception:
                continue
        return False

    def wait_for(self, state: str = "visible", timeout: int | None = None) -> bool:
        per_candidate_timeout = min(self._timeout(timeout), self.default_timeout)
        last_error: Exception | None = None

        for locator in self._iter_locators():
            try:
                locator.first.wait_for(
                    state=state,
                    timeout=per_candidate_timeout,
                )
                self._on_standard_success()
                return True
            except Exception as error:
                last_error = error

        if state == "visible":
            try:
                result = perform_action(
                    self.page,
                    self.vision_description,
                    "wait",
                    None,
                    self.config,
                )
                return self._handle_fallback_result("wait", result, last_error)
            except Exception as fallback_error:
                raise LocatorActionError(
                    f"Could not wait for locator '{self.name}': {last_error}; "
                    f"Gemini fallback failed: {fallback_error}"
                ) from fallback_error

        raise LocatorActionError(
            f"Could not wait for locator '{self.name}' in state '{state}': {last_error}"
        )

    def text_content(self) -> str:
        last_error: Exception | None = None
        for locator in self._iter_locators():
            try:
                text = locator.first.text_content()
                if text is not None:
                    self._on_standard_success()
                    return text
            except Exception as error:
                last_error = error

        try:
            result = perform_action(
                self.page,
                self.vision_description,
                "text",
                None,
                self.config,
            )
            return self._handle_fallback_result("text", result, last_error)
        except Exception as fallback_error:
            raise LocatorActionError(
                f"Could not read text from locator '{self.name}': {last_error}; "
                f"Gemini fallback failed: {fallback_error}"
            ) from fallback_error

    def count(self) -> int:
        for locator in self._iter_locators():
            try:
                return int(locator.first.count())
            except Exception:
                continue
        return 0


_PLATFORM_FILE_KEYS = {
    "chatgpt": "chatgpt_locators_file",
    "atomicmail": "atomicmail_locators_file",
}
_REPOSITORY_CACHE: dict[str, LocatorRepository] = {}


# Run auto-correct once per process
_AUTO_CORRECT_RUN = False


def _get_repository(platform: str) -> LocatorRepository:
    global _AUTO_CORRECT_RUN
    if platform not in _PLATFORM_FILE_KEYS:
        raise ValueError(f"Unknown locator platform: {platform}")

    if platform not in _REPOSITORY_CACHE:
        config = get_config()

        # Run auto-correct on first repository creation
        if not _AUTO_CORRECT_RUN:
            _auto_correct_locators(config)
            _AUTO_CORRECT_RUN = True

        file_key = _PLATFORM_FILE_KEYS[platform]
        repository = LocatorRepository(resolve_path(config["paths"][file_key]))
        _REPOSITORY_CACHE[platform] = repository

    return _REPOSITORY_CACHE[platform]


def get_smart_locator(page, platform: str, name: str | None = None, config: dict[str, Any] | None = None):
    """Return an AI-assisted locator for a platform and locator name.

    If `name` is None or a dict (config), returns a LocatorNamespace for the platform
    that can create SmartLocator instances via `.locator(name)`.
    If `name` is a string, returns a SmartLocator for that specific locator.
    """
    repository = _get_repository(platform)
    effective_config = config or get_config()

    # If name is None or a dict (passed as config by mistake), return namespace
    if name is None or isinstance(name, dict):
        if isinstance(name, dict):
            effective_config = name
        return LocatorNamespace(page, repository, effective_config)

    return SmartLocator(
        page=page,
        repository=repository,
        name=name,
        config=effective_config,
    )


class LocatorNamespace:
    """Namespace for creating SmartLocator instances for a platform.

    Usage:
        chatgpt = get_smart_locator(page, "chatgpt", CONFIG)
        chatgpt.locator("chatgpt_email_input").fill(email)
        chatgpt.locator("chatgpt_login_button").click()
    """

    def __init__(self, page, repository: LocatorRepository, config: dict[str, Any]):
        self.page = page
        self.repository = repository
        self.config = config

    def locator(self, name: str) -> SmartLocator:
        """Create a SmartLocator for the given locator name."""
        return SmartLocator(
            page=self.page,
            repository=self.repository,
            name=name,
            config=self.config,
        )

    def __getattr__(self, name: str) -> SmartLocator:
        """Allow attribute-style access: chatgpt.chatgpt_email_input.fill(email)"""
        return self.locator(name)


def reload_locator_repositories() -> None:
    """Clear cached locator repositories (useful after editing locator files)."""
    _REPOSITORY_CACHE.clear()