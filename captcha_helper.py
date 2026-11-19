"""Helper script to test CAPTCHA solving extension on hCaptcha demo page.

Conventions followed from this project:
- Locators are declared in locators/<platform>_locators.txt and accessed through
  common.locator_manager.get_smart_locator; scripts reference locator keys only.
- Waits use custom_random_wait() and absolute_wait() driven by json/config.json.
- .env provides DECRYPT_KEY (for passwords).
- Every account gets a fresh browser session/context.
- captcha_helper.py always runs headless=False.
"""

import json
import os
import random
import shutil
import sys
import tempfile
import time
import zipfile

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from common.config import ROOT_DIR, get_config, resolve_path
from common.locator_manager import get_smart_locator
from common.upload_utils import upload_to_onlyfiles

load_dotenv(os.path.join(ROOT_DIR, ".env"))

CONFIG = get_config()
HEADLESS = False
USER_AGENT = CONFIG["browser"]["user_agent"]
TYPING_DELAY = int(CONFIG["browser"]["typing_delay_ms"])

HCAPTCHA_DEMO_URL = "https://accounts.hcaptcha.com/demo"

ERROR_SCREENSHOT = resolve_path(CONFIG["paths"]["error_screenshot"])
EXTENSION_ZIP = resolve_path("extension/nopecha.zip")
_EXTRACTED_EXTENSION_DIR = None

# Persistent browser profile directory (in project root)
BROWSER_PROFILE_DIR = resolve_path("browser_profile/captcha_helper")


def _find_extension_root(extract_dir: str) -> str:
    """Walk the extracted dir to find the folder containing manifest.json."""
    for root, _dirs, files in os.walk(extract_dir):
        if "manifest.json" in files:
            return root
    return extract_dir


def _prepare_extension_dir():
    """Extract the extension zip to a temp directory for --load-extension."""
    global _EXTRACTED_EXTENSION_DIR
    if not os.path.exists(EXTENSION_ZIP):
        raise FileNotFoundError(f"Extension zip not found: {EXTENSION_ZIP}")
    if _EXTRACTED_EXTENSION_DIR is None:
        _EXTRACTED_EXTENSION_DIR = tempfile.mkdtemp(prefix="captcha_helper_ext_")
        with zipfile.ZipFile(EXTENSION_ZIP, "r") as zf:
            zf.extractall(_EXTRACTED_EXTENSION_DIR)
        ext_root = _find_extension_root(_EXTRACTED_EXTENSION_DIR)
        manifest = os.path.join(ext_root, "manifest.json")
        print(f"[INFO] Extracted extension to: {_EXTRACTED_EXTENSION_DIR}", flush=True)
        print(f"[INFO] Extension root (manifest): {ext_root}", flush=True)
        print(f"[INFO] Manifest exists: {os.path.exists(manifest)}", flush=True)
        for root, _dirs, files in os.walk(ext_root):
            level = root.replace(ext_root, "").count(os.sep)
            indent = " " * 2 * level
            print(f"{indent}{os.path.basename(root)}/", flush=True)
            subindent = " " * 2 * (level + 1)
            for file in files:
                print(f"{subindent}{file}", flush=True)
        if os.path.exists(manifest):
            with open(manifest, "r", encoding="utf-8") as f:
                manifest_data = json.load(f)
                print(f"[INFO] Extension name: {manifest_data.get('name', 'unknown')}", flush=True)
                print(f"[INFO] Extension version: {manifest_data.get('version', 'unknown')}", flush=True)
                print(f"[INFO] Extension manifest keys: {list(manifest_data.keys())}", flush=True)
        _EXTRACTED_EXTENSION_DIR = ext_root
    return _EXTRACTED_EXTENSION_DIR


def _cleanup_extension_dir():
    global _EXTRACTED_EXTENSION_DIR
    if _EXTRACTED_EXTENSION_DIR and os.path.exists(_EXTRACTED_EXTENSION_DIR):
        try:
            shutil.rmtree(_EXTRACTED_EXTENSION_DIR, ignore_errors=True)
        except Exception:
            pass


def custom_random_wait(min_sec=None, max_sec=None):
    min_sec = CONFIG["waits"]["min_random_wait"] if min_sec is None else min_sec
    max_sec = CONFIG["waits"]["max_random_wait"] if max_sec is None else max_sec
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)


def absolute_wait():
    custom_random_wait(
        CONFIG["waits"]["min_absolute_wait"],
        CONFIG["waits"]["max_absolute_wait"],
    )


def capture_error_screenshot(page):
    if not page:
        return None
    try:
        os.makedirs(os.path.dirname(ERROR_SCREENSHOT), exist_ok=True)
        page.screenshot(path=ERROR_SCREENSHOT)
        print(f"[INFO] Saved error screenshot to: {ERROR_SCREENSHOT}", flush=True)
        return upload_to_onlyfiles(ERROR_SCREENSHOT, CONFIG)
    except Exception as error:
        print(f"[WARNING] Failed to capture or upload error screenshot: {error}", flush=True)
        return None


def run_captcha_test(pw, ext_dir):
    # Use persistent profile directory in project root
    # Run locally first to save NopeCHA extension state (free credits), then use same profile in CI
    user_data_dir = BROWSER_PROFILE_DIR
    os.makedirs(user_data_dir, exist_ok=True)
    print(f"[INFO] Using persistent browser profile: {user_data_dir}", flush=True)
    
    context = None
    page = None
    try:
        print("[DEBUG] Launching persistent browser context with extension...", flush=True)
        launch_args = [
            "--start-maximized",
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            f"--disable-extensions-except={ext_dir}",
            f"--load-extension={ext_dir}",
            "--disable-extensions-http-throttling",
            "--disable-background-networking",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-features=TranslateUI",
            "--disable-ipc-flooding-protection",
            "--force-color-profile=srgb",
            "--metrics-recording-only",
            "--no-first-run",
            "--no-default-browser-check",
            "--password-store=basic",
            "--use-mock-keychain",
        ]

        context = pw.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=HEADLESS,
            no_viewport=True,
            user_agent=USER_AGENT,
            args=launch_args,
        )

        print(f"[DEBUG] Extension loaded successfully. Context ID: {id(context)}", flush=True)
        print(f"[DEBUG] Extension directory: {ext_dir}", flush=True)

        page = context.pages[0] if context.pages else context.new_page()
        page.wait_for_timeout(8000)

        print(f"[DEBUG] Open pages on launch: {[p.url for p in context.pages]}", flush=True)
        print(f"[DEBUG] Background pages: {[p.url for p in context.background_pages]}", flush=True)
        print(f"[DEBUG] Service workers count: {len(context.service_workers)}", flush=True)
        for sw in context.service_workers:
            print(f"[DEBUG]   Service worker: {sw.url}", flush=True)

        print("[DEBUG] Checking for NopeCHA content script on demo page...", flush=True)
        page.goto(HCAPTCHA_DEMO_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        try:
            nopecha_active = page.evaluate("""() => {
                return {
                    hasNopechaGlobal: typeof window.nopecha !== 'undefined',
                    hasNopechaElement: document.querySelector('[data-nopecha]') !== null,
                    scripts: Array.from(document.querySelectorAll('script')).map(s => s.src).filter(src => src.includes('nopecha')),
                    iframes: Array.from(document.querySelectorAll('iframe')).map(f => f.src).filter(f => f.includes('nopecha')),
                    allIframes: Array.from(document.querySelectorAll('iframe')).map(f => f.src)
                };
            }""")
            print(f"[DEBUG] NopeCHA detection: {nopecha_active}", flush=True)
        except Exception as e:
            print(f"[DEBUG] NopeCHA detection failed: {e}", flush=True)

        print(f"[STEP] Navigating to hCaptcha demo page: {HCAPTCHA_DEMO_URL}", flush=True)
        page.goto(HCAPTCHA_DEMO_URL, wait_until="domcontentloaded")
        absolute_wait()

        print("[STEP] Waiting for CAPTCHA to be solved by extension (max 30s)...", flush=True)
        start_time = time.time()
        max_wait = 30
        poll_count = 0
        success_found = False

        while (time.time() - start_time) < max_wait:
            poll_count += 1
            try:
                success_locator = page.get_by_text("Challenge Success!")
                if success_locator.is_visible(timeout=2000):
                    print("[OK] 'Challenge Success!' text found - CAPTCHA solved!", flush=True)
                    success_found = True
                    break
            except Exception as e:
                pass

            if poll_count % 3 == 0:
                elapsed = int(time.time() - start_time)
                print(f"[DEBUG] Poll #{poll_count} ({elapsed}s elapsed) - waiting for CAPTCHA solution...", flush=True)
                # Check if CAPTCHA iframe is present
                try:
                    iframes = page.evaluate("""() => Array.from(document.querySelectorAll('iframe')).map(f => f.src)""")
                    hcaptcha_frames = [f for f in iframes if 'hcaptcha' in f]
                    print(f"[DEBUG]   hCaptcha iframes: {len(hcaptcha_frames)}", flush=True)
                except Exception:
                    pass

            custom_random_wait(3, 5)

        if not success_found:
            raise RuntimeError(f"Timed out after {max_wait}s waiting for CAPTCHA to be solved")

        print("[SUCCESS] CAPTCHA challenge completed successfully!", flush=True)
        return True

    except Exception as error:
        print(f"[ERROR] CAPTCHA test failed: {error}", flush=True)
        capture_error_screenshot(page)
        return False
    finally:
        if context:
            try:
                context.close()
                print("[INFO] Context closed. Browser profile persisted for next run.", flush=True)
            except Exception:
                pass
        # NOTE: Do NOT delete user_data_dir - we persist it for NopeCHA extension state (free credits)


def run():
    print("[START] CAPTCHA Helper script started", flush=True)

    ext_dir = _prepare_extension_dir()

    stealth = Stealth()
    playwright_manager = stealth.use_sync(sync_playwright())
    pw = playwright_manager.__enter__()

    success = False
    try:
        print(f"\n{'=' * 60}", flush=True)
        print("[INFO] Running CAPTCHA test on hCaptcha demo page", flush=True)
        print(f"{'=' * 60}", flush=True)
        success = run_captcha_test(pw, ext_dir)
        if success:
            print("[OK] CAPTCHA test passed!", flush=True)
            sys.exit(0)
        else:
            print("[ERROR] CAPTCHA test failed", flush=True)
            sys.exit(1)
    except Exception as error:
        print(f"[ERROR] Unexpected error: {error}", flush=True)
        sys.exit(1)
    finally:
        _cleanup_extension_dir()
        try:
            playwright_manager.__exit__(None, None, None)
        except Exception:
            pass


if __name__ == "__main__":
    run()