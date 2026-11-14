"""Fetch ChatGPT cookies using Atomic Mail for verification codes."""

import json
import os
import random
import re
import sys
import time

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from common.config import ROOT_DIR, get_config, resolve_path
from common.crypto_utils import encrypt_bytes
from common.file_utils import clear_png_screenshots
from common.locator_manager import get_smart_locator
from common.upload_utils import upload_to_onlyfiles

# =========================
# CONFIG & PATHS
# =========================
load_dotenv(os.path.join(ROOT_DIR, ".env"))

CONFIG = get_config()
HEADLESS = CONFIG["browser"]["headless"]
TARGET_URL = CONFIG["browser"]["target_url"]
ATOMIC_MAIL_URL = CONFIG["browser"]["atomicmail_url"]
USER_AGENT = CONFIG["browser"]["user_agent"]

ACCOUNTS_FILE = resolve_path(CONFIG["paths"]["accounts_file"])
OUTPUT_DIR = resolve_path(CONFIG["paths"]["output_dir"])
ERROR_SCREENSHOT = resolve_path(CONFIG["paths"]["error_screenshot"])

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(os.path.dirname(ERROR_SCREENSHOT), exist_ok=True)


# =========================
# HELPER FUNCTIONS
# =========================
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


def clear_existing_png_screenshots():
    """Delete old PNG screenshots from the output directory before a run."""
    clear_png_screenshots(OUTPUT_DIR)


def remove_blocked_email_from_accounts_file(target_email):
    """Remove a blocked email from accounts.txt and delete its encrypted cookies."""
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as accounts_file:
                lines = accounts_file.readlines()

            remaining_lines = [
                line for line in lines if line.strip() != target_email.strip()
            ]

            with open(ACCOUNTS_FILE, "w", encoding="utf-8") as accounts_file:
                accounts_file.writelines(remaining_lines)

            print(
                f"[ACCOUNT BLOCKED] Removed '{target_email}' from {ACCOUNTS_FILE}",
                flush=True,
            )
        except OSError as error:
            print(
                f"[WARNING] Failed to remove '{target_email}' from {ACCOUNTS_FILE}: {error}",
                flush=True,
            )

    cookie_filename = f"{target_email}_chatgpt_cookies.json.encrypted"
    cookie_filepath = os.path.join(OUTPUT_DIR, cookie_filename)
    if os.path.exists(cookie_filepath):
        try:
            os.remove(cookie_filepath)
            print(
                f"[ACCOUNT BLOCKED] Deleted encrypted cookie file: {cookie_filepath}",
                flush=True,
            )
        except OSError as error:
            print(
                f"[WARNING] Could not delete encrypted cookie file {cookie_filepath}: {error}",
                flush=True,
            )


def get_all_emails():
    if not os.path.exists(ACCOUNTS_FILE):
        raise FileNotFoundError(f"'{ACCOUNTS_FILE}' file not found.")

    emails = []
    with open(ACCOUNTS_FILE, "r", encoding="utf-8") as accounts_file:
        for line in accounts_file:
            email = line.strip()
            if email:
                emails.append(email)

    if not emails:
        raise ValueError(f"No valid emails found in '{ACCOUNTS_FILE}'.")

    return emails


def extract_username_from_email(email):
    """Return the part of an email address before the @ sign."""
    return email.split("@", 1)[0]


def capture_error_screenshot(page):
    """Save the current page to error/error_screenshot.png and upload it."""
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


# =========================
# ENCRYPTION LOGIC
# =========================
def encrypt_and_save_cookies(cookies_data, email, decrypt_key):
    plaintext = json.dumps(cookies_data).encode("utf-8")
    payload = encrypt_bytes(
        plaintext,
        decrypt_key,
        CONFIG["encryption"]["iterations"],
        CONFIG["encryption"]["key_length"],
        CONFIG["encryption"]["salt_bytes"],
        CONFIG["encryption"]["nonce_bytes"],
    )

    filename = f"{email}_chatgpt_cookies.json.encrypted"
    target_path = os.path.join(OUTPUT_DIR, filename)

    with open(target_path, "w", encoding="utf-8") as cookie_file:
        json.dump(payload, cookie_file, separators=(",", ":"))

    print(f"[OK] Encrypted cookies saved to: {target_path}", flush=True)


# =========================
# ATOMIC MAIL HELPERS
# =========================
def navigate_to_chatgpt_email(atomic_page, atomic):
    """Navigate to the ChatGPT verification email from the Atomic Mail inbox.

    Flow:
    1. Wait for mailbox to load (mailbox-loading-wrapper with navigation)
    2. Check if ChatGPT email link is visible (means we're on main inbox page)
    3. Click the ChatGPT email link to open the verification email
    4. Wait for email reader to load
    """
    try:
        print("[INFO] Waiting for mailbox to load...", flush=True)
        atomic.locator("atomicmail_mailbox_navigation").wait_for(state="visible", timeout=15000)
        custom_random_wait(2, 3)

        print("[INFO] Checking for ChatGPT email link...", flush=True)
        chatgpt_link = atomic.locator("atomicmail_chatgpt_email_link")
        if chatgpt_link.is_visible(timeout=5000):
            print("[INFO] ChatGPT email link found. Clicking to open verification email...", flush=True)
            chatgpt_link.click(timeout=5000)
            custom_random_wait(3, 5)
            return True

        print("[INFO] ChatGPT email link not visible. May already be in email reader view.", flush=True)
        return True

    except Exception as error:
        print(f"[WARNING] Error navigating to ChatGPT email: {error}", flush=True)
        return False


def find_verification_code_in_email(atomic_page, atomic):
    """Extract the six-digit verification code from the open email."""
    try:
        print("[INFO] Looking for verification code cell inside email...", flush=True)
        code_cells = atomic.locator("atomicmail_verification_code_cell").raw().filter(has_text=re.compile(r"\d{6}"))
        count = code_cells.count()

        for index in range(count):
            try:
                cell_text = code_cells.nth(index).text_content()
                if not cell_text:
                    continue

                lowered_text = cell_text.lower()
                markers = (
                    "temporary verification code",
                    "login code",
                    "temporary code",
                )
                if not any(marker in lowered_text for marker in markers):
                    continue

                code_match = re.search(r"(\d{6})", cell_text)
                if code_match:
                    verification_code = code_match.group(1)
                    print(f"[OK] Extracted verification code", flush=True)
                    return verification_code
            except Exception:
                continue

        print("[WARNING] No verification code cell found in email.", flush=True)
        return None
    except Exception as error:
        print(f"[WARNING] Error while reading email: {error}", flush=True)
        return None


def go_back_to_inbox(atomic_page, atomic):
    """Click the back button to return to the inbox from email reader view."""
    try:
        print("[INFO] Clicking back button to return to inbox...", flush=True)
        back_button = atomic.locator("atomicmail_reader_back_button")
        if back_button.is_visible(timeout=3000):
            back_button.click(timeout=5000)
            custom_random_wait(2, 4)
        else:
            print("[INFO] Back button not visible. Pressing Escape...", flush=True)
            atomic_page.keyboard.press("Escape")
            custom_random_wait(1, 2)
    except Exception as error:
        print(f"[WARNING] Error clicking back button: {error}", flush=True)


# =========================
# MAIN FLOW
# =========================
def process_email(email, decrypt_key, pw):
    """Process one email and return a (status, error) tuple."""
    print(f"\n{'=' * 60}", flush=True)
    print(f"[INFO] Processing email: {email}", flush=True)
    print(f"{'=' * 60}", flush=True)

    browser = None
    page = None
    try:
        browser = pw.chromium.launch(
            headless=HEADLESS,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        context = browser.new_context(
            no_viewport=True,
            user_agent=USER_AGENT,
        )

        # =========================
        # TAB MONITOR: close unexpected tabs and ads
        # =========================
        def handle_new_page(new_page):
            def is_allowed(url):
                return (
                    "chatgpt.com" in url
                    or "openai.com" in url
                    or "atomicmail.io" in url
                    or url == "about:blank"
                )

            if not is_allowed(new_page.url):
                print(f"[AUTO-CLOSE] Closing extra tab: {new_page.url}", flush=True)
                try:
                    new_page.close()
                except Exception:
                    pass
                return

            def on_framenavigated(frame):
                if frame == new_page.main_frame and not is_allowed(frame.url):
                    print(
                        f"[AUTO-CLOSE] Extra tab navigated to an unallowed URL: {frame.url}",
                        flush=True,
                    )
                    try:
                        new_page.close()
                    except Exception:
                        pass

            new_page.on("framenavigated", on_framenavigated)

        context.on("page", handle_new_page)

        # =========================
        # STEP 1: Log in to ChatGPT
        # =========================
        page = context.new_page()
        chatgpt = get_smart_locator(page, "chatgpt", CONFIG)

        print(f"[STEP] Navigating to URL: {TARGET_URL}...", flush=True)
        page.goto(TARGET_URL, wait_until="domcontentloaded")
        print(f"[OK] {TARGET_URL} opened completely.", flush=True)
        absolute_wait()

        print("[STEP] Checking if the email field is already visible...", flush=True)
        email_field = chatgpt.locator("chatgpt_email_input")
        if email_field.is_visible():
            print("[INFO] Email field already visible. Skipping login button click.", flush=True)
        else:
            print("[STEP] Clicking login button...", flush=True)
            chatgpt.locator("chatgpt_login_button").click()
            absolute_wait()

        print("[STEP] Entering email address...", flush=True)
        chatgpt.locator("chatgpt_email_input").fill(email)
        absolute_wait()

        print("[STEP] Clicking continue...", flush=True)
        chatgpt.locator("chatgpt_continue_button").click()
        absolute_wait()

        # =========================
        # STEP 2: Open Atomic Mail and log in
        # =========================
        print("[STEP] Opening Atomic Mail in a new tab...", flush=True)
        atomic_page = context.new_page()
        atomic = get_smart_locator(atomic_page, "atomicmail", CONFIG)
        atomic_page.goto(ATOMIC_MAIL_URL, wait_until="domcontentloaded")
        print(f"[OK] {ATOMIC_MAIL_URL} opened completely.", flush=True)
        absolute_wait()

        print("[STEP] Clicking sign in link...", flush=True)
        atomic.locator("atomicmail_sign_in_link").click()
        absolute_wait()

        print("[STEP] Waiting for username field to be ready...", flush=True)
        username_input = atomic.locator("atomicmail_username_input")
        username_input.wait_for(state="visible", timeout=10000)
        custom_random_wait(1, 2)

        username = extract_username_from_email(email)
        print(f"[STEP] Entering username: {username}...", flush=True)
        username_input.fill(username)
        custom_random_wait(2, 4)

        print("[STEP] Clicking login submit...", flush=True)
        submit_button = atomic.locator("atomicmail_login_submit_button")
        submit_button.wait_for(state="visible", timeout=5000)
        submit_button.click()
        custom_random_wait(2, 4)

        print("[STEP] Entering password...", flush=True)
        password_input = atomic.locator("atomicmail_password_input")
        password_input.wait_for(state="visible", timeout=10000)
        password_input.fill(decrypt_key)
        custom_random_wait(2, 4)

        print("[STEP] Clicking sign in button...", flush=True)
        sign_in_button = atomic.locator("atomicmail_sign_in_button")
        sign_in_button.wait_for(state="visible", timeout=5000)
        sign_in_button.click()
        custom_random_wait(5, 8)

        # =========================
        # STEP 3: Find the ChatGPT verification code email
        # =========================
        print("[STEP] Looking for ChatGPT verification code email...", flush=True)
        verification_code = None
        max_outer_retries = CONFIG["retry"]["max_outer_retries"]

        # First, navigate to the ChatGPT email from the inbox
        if not navigate_to_chatgpt_email(atomic_page, atomic):
            print("[ERROR] Failed to navigate to ChatGPT email.", flush=True)
            capture_error_screenshot(page)
            return "FAILED", None

        for outer_attempt in range(max_outer_retries):
            print(
                f"\n[INFO] Outer retry set {outer_attempt + 1}/{max_outer_retries}",
                flush=True,
            )

            max_inner_retries = CONFIG["retry"]["max_inner_retries"]
            for inner_attempt in range(max_inner_retries):
                print(
                    f"[INFO] Inner retry {inner_attempt + 1}/{max_inner_retries} "
                    "checking for verification email...",
                    flush=True,
                )

                verification_code = find_verification_code_in_email(atomic_page, atomic)
                if verification_code:
                    break

                go_back_to_inbox(atomic_page, atomic)
                # After going back, navigate to ChatGPT email again
                if not navigate_to_chatgpt_email(atomic_page, atomic):
                    print("[WARNING] Failed to re-navigate to ChatGPT email.", flush=True)
                custom_random_wait(5, 8)

            if verification_code:
                break

            print(
                "[STEP] Code not found. Switching to ChatGPT to click 'Resend email'...",
                flush=True,
            )
            page.bring_to_front()
            custom_random_wait(2, 4)

            print("[STEP] Clicking 'Resend email' button...", flush=True)
            try:
                chatgpt.locator("chatgpt_resend_email_button").click(timeout=5000)
            except Exception as error:
                print(f"[WARNING] Could not click 'Resend email': {error}", flush=True)
            custom_random_wait(3, 5)

            print("[STEP] Switching back to Atomic Mail...", flush=True)
            atomic_page.bring_to_front()
            custom_random_wait(2, 4)
            # After resend, navigate to ChatGPT email again
            if not navigate_to_chatgpt_email(atomic_page, atomic):
                print("[WARNING] Failed to re-navigate to ChatGPT email after resend.", flush=True)

        if verification_code is None:
            print(
                "[ERROR] Could not find verification code email after all retries.",
                flush=True,
            )
            capture_error_screenshot(page)
            return "FAILED", None

        # =========================
        # STEP 4: Enter the verification code
        # =========================
        print("[STEP] Switching back to ChatGPT to enter verification code...", flush=True)
        page.bring_to_front()
        custom_random_wait(2, 4)

        print("[STEP] Typing verification code...", flush=True)
        custom_random_wait(1, 2)
        page.keyboard.press("Tab")
        custom_random_wait(1, 2)

        code_field = chatgpt.locator("chatgpt_verification_code_input")
        code_field.wait_for(state="visible", timeout=5000)
        code_field.click()
        page.keyboard.type(
            verification_code,
            delay=CONFIG["browser"]["typing_delay_ms"],
        )
        custom_random_wait(1, 2)

        print("[STEP] Clicking continue button...", flush=True)
        chatgpt.locator("chatgpt_continue_button_after_code").click()
        absolute_wait()

        # Check whether the account is blocked.
        print("[STEP] Checking for account block error...", flush=True)
        try:
            # Use wait_for instead of is_visible to trigger Gemini Vision if element exists
            blocked_locator = chatgpt.locator("chatgpt_account_block_error")
            if blocked_locator.wait_for(state="visible", timeout=5000):
                print(
                    f"[ERROR] Account is blocked: {email}. Removing it from accounts and cookies...",
                    flush=True,
                )
                remove_blocked_email_from_accounts_file(email)
                return "BLOCKED", None
        except Exception:
            # If wait_for times out, account is not blocked (or Gemini couldn't find it)
            pass

        # Open an existing workspace when the button is present.
        print("[STEP] Checking for existing workspace 'Open' button...", flush=True)
        try:
            workspace_button = chatgpt.locator("chatgpt_existing_workspace_open_button")
            if workspace_button.is_visible(timeout=3000):
                print("[INFO] Existing workspace found. Clicking 'Open'...", flush=True)
                workspace_button.click()
                custom_random_wait(5, 8)
        except Exception:
            print("[INFO] Workspace button not present or skipped.", flush=True)

        print("[STEP] Waiting for login completion...", flush=True)
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        custom_random_wait(5, 8)

        # =========================
        # STEP 5: Verify login success
        # =========================
        print("[STEP] Verifying login success...", flush=True)
        try:
            chatgpt.locator("chatgpt_profile_button").wait_for(
                state="visible",
                timeout=15000,
            )
            print("[OK] Login successful!", flush=True)
        except Exception:
            print(
                "[ERROR] Login failed or took too long. Profile button not found.",
                flush=True,
            )
            capture_error_screenshot(page)
            return "FAILED", None

        print("[STEP] Harvesting context cookies...", flush=True)
        cookies = context.cookies()

        if cookies:
            encrypt_and_save_cookies(cookies, email, decrypt_key)
        else:
            print("[WARNING] No cookies captured. Check if login was blocked.", flush=True)
            capture_error_screenshot(page)

        return "SUCCESS", None

    except Exception as error:
        print(f"[ERROR] Workflow failed for {email}: {error}", flush=True)
        capture_error_screenshot(page)
        return "FAILED", error

    finally:
        if browser:
            try:
                browser.close()
                print(f"[INFO] Browser closed for {email}.", flush=True)
            except Exception:
                pass


def run():
    print("[START] Script started", flush=True)

    clear_existing_png_screenshots()

    try:
        emails = get_all_emails()
        print(f"[INFO] Found {len(emails)} email(s) to process.", flush=True)
    except Exception as error:
        print(f"[ERROR] Credential error: {error}", flush=True)
        sys.exit(1)

    decrypt_key = os.getenv("DECRYPT_KEY")
    if not decrypt_key:
        print("[ERROR] DECRYPT_KEY is missing in environment or .env", flush=True)
        sys.exit(1)

    stealth = Stealth()
    playwright_manager = stealth.use_sync(sync_playwright())
    pw = playwright_manager.__enter__()

    try:
        max_email_retries = CONFIG["retry"]["max_email_retries"]
        for email in emails:
            success = False
            status = "FAILED"

            for attempt in range(1, max_email_retries + 1):
                print(
                    f"\n[INFO] Attempt {attempt}/{max_email_retries} for email: {email}",
                    flush=True,
                )

                status, _error = process_email(email, decrypt_key, pw)

                if status == "SUCCESS":
                    print(f"[OK] Successfully processed email: {email}", flush=True)
                    success = True
                    break

                if status == "BLOCKED":
                    print(
                        f"[SKIP] Account is blocked for email: {email}. Skipping retries.",
                        flush=True,
                    )
                    success = True
                    break

                print(
                    f"[WARNING] Attempt {attempt}/{max_email_retries} failed for email: {email}",
                    flush=True,
                )
                if attempt < max_email_retries:
                    print(
                        f"[INFO] Retrying email: {email} with a fresh browser session...",
                        flush=True,
                    )
                    custom_random_wait(3, 5)

            if not success and status != "BLOCKED":
                print(
                    f"[ERROR] All {max_email_retries} attempts failed for email: {email}. "
                    "Exiting with failure.",
                    flush=True,
                )
                sys.exit(1)

    except Exception as error:
        print(f"[ERROR] Unexpected error: {error}", flush=True)
        sys.exit(1)
    finally:
        try:
            playwright_manager.__exit__(None, None, None)
        except Exception:
            pass

    print("[DONE] Process terminated cleanly.", flush=True)


if __name__ == "__main__":
    run()