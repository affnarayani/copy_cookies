"""Centralized locator repository and AI-assisted locator actions."""

import os
import re
from dataclasses import dataclass
from typing import Any, Iterator

from config import get_config, resolve_path
from gemini_fallback import perform_action


class LocatorNotFoundError(LookupError):
    """Raised when a named locator is not present in a platform locator file."""


class LocatorActionError(RuntimeError):
    """Raised when neither the standard locator nor the Gemini fallback succeeds."""


@dataclass(frozen=True)
class LocatorCandidate:
    """A single locator definition loaded from a platform locator file."""

    name: str
    strategy: str
    value: str
    options: dict[str, str]
    description: str


class LocatorRepository:
    """Loads and builds Playwright locators from a platform locator text file."""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self._entries: dict[str, list[LocatorCandidate]] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"Locator file not found: {self.file_path}")

        with open(self.file_path, "r", encoding="utf-8") as locator_file:
            for line_number, raw_line in enumerate(locator_file, start=1):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue

                parts = [part.strip() for part in line.split("|")]
                if len(parts) < 5:
                    raise ValueError(
                        f"Invalid locator line {line_number} in {self.file_path}: expected "
                        "'name | strategy | value | options | description'"
                    )

                name, strategy, value, options_raw = parts[:4]
                description = "|".join(parts[4:]).strip()
                if not name or not strategy or not value:
                    raise ValueError(
                        f"Invalid locator line {line_number} in {self.file_path}: "
                        "name, strategy and value are required"
                    )

                options: dict[str, str] = {}
                for option in options_raw.split(";"):
                    option = option.strip()
                    if not option:
                        continue
                    if "=" not in option:
                        raise ValueError(
                            f"Invalid option '{option}' on line {line_number} in {self.file_path}"
                        )
                    option_name, option_value = option.split("=", 1)
                    options[option_name.strip()] = option_value.strip()

                candidate = LocatorCandidate(
                    name=name,
                    strategy=strategy,
                    value=value,
                    options=options,
                    description=description,
                )
                self._entries.setdefault(name, []).append(candidate)

        if not self._entries:
            raise ValueError(f"Locator file contains no locators: {self.file_path}")

    def get(self, name: str) -> list[LocatorCandidate]:
        """Return all candidates defined for a locator name."""
        if name not in self._entries:
            raise LocatorNotFoundError(
                f"Locator '{name}' was not found in {self.file_path}"
            )
        return self._entries[name]

    def build(self, page, name: str):
        """Build the first candidate for a locator name."""
        return self.build_candidate(page, self.get(name)[0], frozenset())

    def build_candidate(
        self,
        page,
        candidate: LocatorCandidate,
        chain_stack: frozenset[str],
    ):
        """Build a Playwright locator for one candidate definition."""
        strategy = candidate.strategy
        value = candidate.value
        options = candidate.options

        if strategy == "test_id":
            return page.get_by_test_id(value)

        if strategy == "role":
            role_options: dict[str, Any] = {}
            if "name" in options and "name_regex" in options:
                raise ValueError(
                    f"Locator '{candidate.name}' defines both name and name_regex"
                )
            if "name" in options:
                role_options["name"] = options["name"]
            if "name_regex" in options:
                role_options["name"] = re.compile(options["name_regex"])
            if options.get("exact", "false").lower() == "true":
                role_options["exact"] = True

            locator = page.get_by_role(value, **role_options)
            if "has_text" in options:
                locator = locator.filter(has_text=options["has_text"])
            if "has_text_regex" in options:
                locator = locator.filter(has_text=re.compile(options["has_text_regex"]))
            return locator

        if strategy == "label":
            exact = options.get("exact", "false").lower() == "true"
            return page.get_by_label(value, exact=exact)

        if strategy == "text":
            exact = options.get("exact", "false").lower() == "true"
            return page.get_by_text(value, exact=exact)

        if strategy == "placeholder":
            exact = options.get("exact", "false").lower() == "true"
            return page.get_by_placeholder(value, exact=exact)

        if strategy == "css":
            return page.locator(value)

        if strategy == "xpath":
            if value.startswith("xpath="):
                selector = value
            elif value.startswith("//") or value.startswith("("):
                selector = f"xpath={value}"
            else:
                selector = value
            return page.locator(selector)

        if strategy == "chain":
            if candidate.name in chain_stack:
                raise ValueError(f"Circular locator chain detected at '{candidate.name}'")
            next_stack = chain_stack | {candidate.name}

            locator = None
            for segment in candidate.value.split(">"):
                segment = segment.strip()
                if not segment:
                    continue

                if segment in self._entries:
                    child = self._entries[segment][0]
                    locator = self.build_candidate(page, child, next_stack)
                    continue

                if ":" in segment:
                    child_strategy, child_value = segment.split(":", 1)
                    child = LocatorCandidate(
                        name=segment,
                        strategy=child_strategy.strip(),
                        value=child_value.strip(),
                        options={},
                        description=candidate.description,
                    )
                    locator = self.build_candidate(page, child, next_stack)
                    continue

                raise ValueError(
                    f"Locator chain segment '{segment}' in '{candidate.name}' is not defined"
                )

            if locator is None:
                raise ValueError(f"Locator chain '{candidate.name}' has no valid segments")
            return locator

        raise ValueError(
            f"Unsupported locator strategy '{strategy}' for locator '{candidate.name}'"
        )


class SmartLocator:
    """Runs standard locator candidates first, then the Gemini vision fallback."""

    def __init__(self, page, repository: LocatorRepository, name: str, config: dict[str, Any]):
        self.page = page
        self.repository = repository
        self.name = name
        self.config = config
        self.candidates = repository.get(name)
        self.description = self.candidates[0].description
        self.default_timeout = int(config["browser"].get("locator_timeout_ms", 5000))

    def _timeout(self, timeout: int | None) -> int:
        return self.default_timeout if timeout is None else int(timeout)

    def _iter_locators(self) -> Iterator[Any]:
        for candidate in self.candidates:
            try:
                yield self.repository.build_candidate(self.page, candidate, frozenset())
            except Exception:
                continue

    def raw(self) -> Any:
        """Return the first standard Playwright locator for this name."""
        return self.repository.build(self.page, self.name)

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
                return True
            except Exception as error:
                last_error = error

        try:
            return bool(
                perform_action(
                    self.page,
                    self.description,
                    "click",
                    None,
                    self.config,
                )
            )
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
                return True
            except Exception as error:
                last_error = error

        try:
            return bool(
                perform_action(
                    self.page,
                    self.description,
                    "fill",
                    value,
                    self.config,
                )
            )
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
                return True
            except Exception as error:
                last_error = error

        if state == "visible":
            try:
                return bool(
                    perform_action(
                        self.page,
                        self.description,
                        "wait",
                        None,
                        self.config,
                    )
                )
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
                    return text
            except Exception as error:
                last_error = error

        try:
            result = perform_action(
                self.page,
                self.description,
                "text",
                None,
                self.config,
            )
            return result or ""
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


def _get_repository(platform: str) -> LocatorRepository:
    if platform not in _PLATFORM_FILE_KEYS:
        raise ValueError(f"Unknown locator platform: {platform}")

    if platform not in _REPOSITORY_CACHE:
        config = get_config()
        file_key = _PLATFORM_FILE_KEYS[platform]
        repository = LocatorRepository(resolve_path(config["paths"][file_key]))
        _REPOSITORY_CACHE[platform] = repository

    return _REPOSITORY_CACHE[platform]


def get_smart_locator(page, platform: str, name: str, config: dict[str, Any] | None = None) -> SmartLocator:
    """Return an AI-assisted locator for a platform and locator name."""
    repository = _get_repository(platform)
    return SmartLocator(
        page=page,
        repository=repository,
        name=name,
        config=config or get_config(),
    )


def reload_locator_repositories() -> None:
    """Clear cached locator repositories (useful after editing locator files)."""
    _REPOSITORY_CACHE.clear()