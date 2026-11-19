"""Create Atomic Mail accounts and complete ChatGPT signup for each account.

Conventions followed from this project:
- Locators are declared in locators/<platform>_locators.txt and accessed through
  common.locator_manager.get_smart_locator; scripts reference locator keys only.
- Waits use custom_random_wait() and absolute_wait() driven by json/config.json.
- .env provides DECRYPT_KEY (for passwords).
- Firstname/surname read from accounts/names.json (random choice each, email lowercase).
- Successful emails are appended to accounts/accounts.txt (one per line, max 6).
- Every account gets a fresh browser session/context.
- create_account.py always runs headless=False.
"""

import json
import os
import random
import re
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

ATOMIC_SIGNUP_URL = "https://atomicmail.io/app/auth/sign-up"
CHATGPT_URL = "https://chatgpt.com/"

ACCOUNTS_FILE = resolve_path(CONFIG["paths"]["accounts_file"])
ERROR_SCREENSHOT = resolve_path(CONFIG["paths"]["error_screenshot"])
NAMES_FILE = resolve_path("accounts/names.json")
EXTENSION_ZIP = resolve_path("extension/nopecha.zip")
_EXTRACTED_EXTENSION_DIR = None


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
        _EXTRACTED_EXTENSION_DIR = tempfile.mkdtemp(prefix="atomicmail_ext_")
        with zipfile.ZipFile(EXTENSION_ZIP, "r") as zf:
            zf.extractall(_EXTRACTED_EXTENSION_DIR)
        ext_root = _find_extension_root(_EXTRACTED_EXTENSION_DIR)
        manifest = os.path.join(ext_root, "manifest.json")
        print(f"[INFO] Extracted extension to: {_EXTRACTED_EXTENSION_DIR}", flush=True)
        print(f"[INFO] Extension root (manifest): {ext_root}", flush=True)
        print(f"[INFO] Manifest exists: {os.path.exists(manifest)}", flush=True)
        # List all files in extension root
        for root, _dirs, files in os.walk(ext_root):
            level = root.replace(ext_root, "").count(os.sep)
            indent = " " * 2 * level
            print(f"{indent}{os.path.basename(root)}/", flush=True)
            subindent = " " * 2 * (level + 1)
            for file in files:
                print(f"{subindent}{file}", flush=True)
        # Read manifest for extension ID
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


MAX_ACCOUNTS = 6
DECRYPT_KEY = os.getenv("DECRYPT_KEY")

os.makedirs(os.path.dirname(ERROR_SCREENSHOT), exist_ok=True)


class UsernameTakenError(Exception):
    """Raised when the Atomic Mail username/email is already taken."""


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


def load_names():
    with open(NAMES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    first_names = data.get("first_names", [])
    last_names = data.get("last_names", [])
    if not first_names or not last_names:
        raise RuntimeError(f"names.json missing first_names or last_names: {NAMES_FILE}")
    return first_names, last_names


def generate_names():
    first_names, last_names = load_names()
    firstname = random.choice(first_names)
    surname = random.choice(last_names)
    return firstname, surname


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


def load_existing_accounts():
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def append_account(email):
    # Ensure previous content ends with newline before appending
    if os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        if content and not content.endswith("\n"):
            with open(ACCOUNTS_FILE, "a", encoding="utf-8") as f:
                f.write("\n")
    with open(ACCOUNTS_FILE, "a", encoding="utf-8") as f:
        f.write(email + "\n")
    print(f"[OK] Saved account to {ACCOUNTS_FILE}: {email}", flush=True)


def signup_atomicmail(page, atomic, firstname, surname, suffix, decrypt_key):
    email_username = f"{firstname.lower()}{surname.lower()}{suffix}"
    email = f"{email_username}@atomicmail.io"
    print(f"[STEP] Navigating to Atomic Mail signup: {ATOMIC_SIGNUP_URL}", flush=True)
    page.goto(ATOMIC_SIGNUP_URL, wait_until="domcontentloaded")
    absolute_wait()

    print("[STEP] Entering firstname...", flush=True)
    atomic.locator("atomicmail_register_firstname_input").fill(firstname)
    custom_random_wait(1, 2)

    print("[STEP] Entering lastname...", flush=True)
    atomic.locator("atomicmail_register_lastname_input").fill(surname)
    custom_random_wait(1, 2)

    print("[STEP] Clicking Submit (name step)...", flush=True)
    atomic.locator("atomicmail_register_submit_button").click()
    custom_random_wait(2, 3)

    print("[STEP] Entering email username...", flush=True)
    email_input = atomic.locator("atomicmail_register_email_input")
    email_input.wait_for(state="visible", timeout=10000)
    email_input.fill(email_username)
    custom_random_wait(1, 2)

    print("[STEP] Clicking registration-submit...", flush=True)
    atomic.locator("atomicmail_registration_submit").click()
    custom_random_wait(2, 3)

    print("[STEP] Checking for 'username taken' error...", flush=True)
    if atomic.locator("atomicmail_field_error_message").is_visible(timeout=3000):
        raise UsernameTakenError(f"Email username '{email_username}' already taken")

    print("[STEP] Entering password...", flush=True)
    atomic.locator("atomicmail_register_password_input").fill(decrypt_key)
    custom_random_wait(1, 2)

    print("[STEP] Entering confirm password...", flush=True)
    atomic.locator("atomicmail_register_confirm_password_input").fill(decrypt_key)
    custom_random_wait(1, 2)

    print("[STEP] Clicking Submit (password step)...", flush=True)
    atomic.locator("atomicmail_register_submit_button").click()
    custom_random_wait(2, 3)

    print("[STEP] Clicking download seed...", flush=True)
    atomic.locator("atomicmail_registration_download_seed").click()
    custom_random_wait(2, 3)

    print("[STEP] Waiting for captcha solve (recovery email input), max 180s...", flush=True)
    recovery_input = atomic.locator("atomicmail_register_recovery_email_input")
    start = time.time()
    while (time.time() - start) < 180:
        try:
            if recovery_input.is_visible(timeout=5000):
                print("[OK] Recovery email input visible - captcha solved.", flush=True)
                break
        except Exception:
            pass
        custom_random_wait(3, 5)
    else:
        raise RuntimeError("Timed out waiting for captcha to be solved")

    print("[STEP] Clicking recovery email skip...", flush=True)
    atomic.locator("atomicmail_register_recovery_email_skip").click()
    custom_random_wait(2, 3)

    print("[STEP] Clicking welcome sign-in...", flush=True)
    atomic.locator("atomicmail_welcome_sign_in").click()
    custom_random_wait(3, 5)

    return email


def fetch_verification_code(atomic_page, atomic, config):
    print("[STEP] Waiting for Atomic Mail inbox...", flush=True)
    try:
        atomic.locator("atomicmail_mailbox_navigation").wait_for(state="visible", timeout=20000)
    except Exception:
        print("[INFO] Mailbox navigation not found; proceeding anyway.", flush=True)
    custom_random_wait(2, 3)

    max_wait_seconds = 120
    start = time.time()
    code = None
    while (time.time() - start) < max_wait_seconds and code is None:
        try:
            if atomic.locator("atomicmail_chatgpt_email_link").is_visible(timeout=5000):
                print("[INFO] ChatGPT verification email found. Opening it...", flush=True)
                atomic.locator("atomicmail_chatgpt_email_link").click()
                custom_random_wait(3, 5)
                code_cells = atomic.locator("atomicmail_verification_code_cell").raw()
                count = code_cells.count()
                for index in range(count):
                    try:
                        cell_text = code_cells.nth(index).text_content() or ""
                        lowered = cell_text.lower()
                        markers = ("temporary verification code", "login code", "temporary code")
                        if not any(marker in lowered for marker in markers):
                            continue
                        match = re.search(r"(\d{6})", cell_text)
                        if match:
                            code = match.group(1)
                            print("[OK] Extracted verification code.", flush=True)
                            break
                    except Exception:
                        continue
                if code:
                    break
        except Exception:
            pass
        print("[INFO] ChatGPT email not yet in inbox. Refreshing and waiting...", flush=True)
        atomic_page.keyboard.press("F5")
        custom_random_wait(5, 8)
        try:
            atomic.locator("atomicmail_mailbox_navigation").wait_for(state="visible", timeout=15000)
        except Exception:
            pass

    if code is None:
        raise RuntimeError("Could not find ChatGPT verification code email in Atomic Mail inbox")
    return code


def create_one_account(pw, ext_dir):
    firstname, surname = generate_names()
    firstname_display = firstname.capitalize()
    surname_display = surname.capitalize()
    suffix = random.randint(100000, 999999)
    email_username = f"{firstname.lower()}{surname.lower()}{suffix}"
    email = f"{email_username}@atomicmail.io"

    user_data_dir = tempfile.mkdtemp(prefix="atomicmail_profile_")
    context = None
    page = None
    chatgpt_page = None
    try:
        print("[DEBUG] Launching persistent browser context with extension...", flush=True)
        launch_args = [
            "--start-maximized",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=site-per-process,IsolateOrigins",
            "--disable-web-security",
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

        page = context.pages[0] if context.pages else context.new_page()
        page.wait_for_timeout(8000)

        # Debug: Check if extension content script is active
        print("[DEBUG] Checking for NopeCHA content script...", flush=True)
        page.goto(ATOMIC_SIGNUP_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        try:
            nopecha_active = page.evaluate("""() => {
                return {
                    hasNopechaGlobal: typeof window.nopecha !== 'undefined',
                    hasNopechaElement: document.querySelector('[data-nopecha]') !== null,
                    scripts: Array.from(document.querySelectorAll('script')).map(s => s.src).filter(s => s.includes('nopecha')),
                    iframes: Array.from(document.querySelectorAll('iframe')).map(f => f.src).filter(f => s.includes('nopecha'))
                };
            }""")
            print(f"[DEBUG] NopeCHA detection: {nopecha_active}", flush=True)
        except Exception as e:
            print(f"[DEBUG] NopeCHA detection failed: {e}", flush=True)

        atomic = get_smart_locator(page, "atomicmail", CONFIG)

        max_retries = int(CONFIG["retry"]["max_email_retries"])
        for attempt in range(1, max_retries + 1):
            try:
                email = signup_atomicmail(page, atomic, firstname, surname, suffix, DECRYPT_KEY)
                break
            except UsernameTakenError as error:
                print(f"[WARNING] {error}. Retrying with new email...", flush=True)
                if attempt < max_retries:
                    firstname, surname = generate_names()
                    firstname_display = firstname.capitalize()
                    surname_display = surname.capitalize()
                    suffix = random.randint(100000, 999999)
                    email_username = f"{firstname.lower()}{surname.lower()}{suffix}"
                    email = f"{email_username}@atomicmail.io"
                    custom_random_wait(2, 4)
                else:
                    raise

        chatgpt_page = context.new_page()
        chatgpt = get_smart_locator(chatgpt_page, "chatgpt", CONFIG)

        print(f"[STEP] Navigating to {CHATGPT_URL}", flush=True)
        chatgpt_page.goto(CHATGPT_URL, wait_until="domcontentloaded")
        absolute_wait()

        print("[STEP] Clicking 'Sign up for free'...", flush=True)
        chatgpt.locator("chatgpt_sign_up_button").click()
        absolute_wait()

        print("[STEP] Entering email...", flush=True)
        chatgpt.locator("chatgpt_email_input").fill(email)
        absolute_wait()

        print("[STEP] Clicking Continue...", flush=True)
        chatgpt.locator("chatgpt_continue_button").click()
        absolute_wait()

        print("[STEP] Fetching verification code from Atomic Mail...", flush=True)
        code = fetch_verification_code(page, atomic, CONFIG)

        print("[STEP] Entering verification code...", flush=True)
        code_field = chatgpt.locator("chatgpt_verification_code_input")
        code_field.wait_for(state="visible", timeout=10000)
        code_field.click()
        chatgpt_page.keyboard.type(code, delay=TYPING_DELAY)
        custom_random_wait(1, 2)

        print("[STEP] Clicking Continue after code...", flush=True)
        chatgpt.locator("chatgpt_continue_button_after_code").click()
        absolute_wait()

        print("[STEP] Entering full name...", flush=True)
        chatgpt.locator("chatgpt_full_name_input").fill(f"{firstname_display} {surname_display}")
        custom_random_wait(1, 2)

        print("[STEP] Clicking Continue...", flush=True)
        chatgpt.locator("chatgpt_continue_button").click()
        absolute_wait()

        print("[STEP] Entering age...", flush=True)
        age = random.randint(21, 60)
        age_locator = chatgpt.locator("chatgpt_age_input")
        age_locator.wait_for(state="visible", timeout=10000)
        custom_random_wait(1, 2)
        age_locator.fill(str(age))
        custom_random_wait(1, 2)

        print("[STEP] Clicking Continue...", flush=True)
        chatgpt.locator("chatgpt_continue_button").click()
        absolute_wait()

        print("[STEP] Waiting then clicking Continue again...", flush=True)
        custom_random_wait(3, 5)
        chatgpt.locator("chatgpt_continue_button").click()
        absolute_wait()

        print("[STEP] Waiting for login completion...", flush=True)
        try:
            chatgpt_page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        custom_random_wait(5, 8)

        print("[STEP] Sending 'hello' to ChatGPT...", flush=True)
        chatgpt.locator("chatgpt_ask_chatgpt_input").wait_for(state="visible", timeout=15000)
        chatgpt.locator("chatgpt_ask_chatgpt_input").fill("hello")
        chatgpt_page.keyboard.press("Enter")
        custom_random_wait(3, 5)

        print(f"[OK] Account fully created and verified: {email}", flush=True)
        return email

    except Exception as error:
        print(f"[ERROR] Workflow failed for {email}: {error}", flush=True)
        capture_error_screenshot(page if page else chatgpt_page)
        return None
    finally:
        if context:
            try:
                context.close()
                print(f"[INFO] Context closed for {email}.", flush=True)
            except Exception:
                pass
        try:
            if user_data_dir and os.path.exists(user_data_dir):
                shutil.rmtree(user_data_dir, ignore_errors=True)
        except Exception:
            pass


def run():
    print("[START] Script started", flush=True)

    if not DECRYPT_KEY:
        print("[ERROR] DECRYPT_KEY is missing in environment or .env", flush=True)
        sys.exit(1)

    ext_dir = _prepare_extension_dir()

    stealth = Stealth()
    playwright_manager = stealth.use_sync(sync_playwright())
    pw = playwright_manager.__enter__()

    created = []
    try:
        print(f"\n{'=' * 60}", flush=True)
        print("[INFO] Creating 1 account", flush=True)
        print(f"{'=' * 60}", flush=True)
        email = create_one_account(pw, ext_dir)
        if email:
            append_account(email)
            created.append(email)
        else:
            print("[ERROR] Failed to create account", flush=True)
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

    print(
        f"[DONE] Created {len(created)} account(s). Total in accounts.txt: {len(load_existing_accounts())}",
        flush=True,
    )


if __name__ == "__main__":
    run()