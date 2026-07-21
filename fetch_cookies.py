import os
import sys
import json
import random
import time
import base64
import re
import requests
from secrets import token_bytes
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
from dotenv import load_dotenv
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# =========================
# CONFIG & PATHS
# =========================
load_dotenv()

HEADLESS = True
TARGET_URL = "https://chatgpt.com/auth/login"
ATOMIC_MAIL_URL = "https://atomicmail.io/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(ROOT_DIR, "accounts.txt")
OUTPUT_DIR = os.path.join(ROOT_DIR, "chatgpt_cookies")

# Ensure output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)

# =========================
# HELPER FUNCTIONS
# =========================
def custom_random_wait(min_sec=3, max_sec=6):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)

def get_all_emails():
    if not os.path.exists(ACCOUNTS_FILE):
        raise FileNotFoundError(f"'{ACCOUNTS_FILE}' file not found.")
    
    emails = []
    with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            email = line.strip()
            if email:
                emails.append(email)
    
    if not emails:
        raise ValueError(f"No valid emails found in '{ACCOUNTS_FILE}'.")
    
    return emails

def extract_username_from_email(email):
    """Extract username from email (part before @)."""
    return email.split('@')[0]

def capture_and_upload_screenshot(page):
    """Capture a screenshot of the current page and upload to ImgBB."""
    try:
        screenshot_path = "error_screenshot.png"
        page.screenshot(path=screenshot_path, full_page=True)
        print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
        
        imgbb_key = os.getenv("IMGBBB_API_KEY")
        if imgbb_key:
            print("[OK] Uploading screenshot to ImgBB...", flush=True)
            url = f"https://api.imgbb.com/1/upload?expiration=86400&key={imgbb_key}"
            
            with open(screenshot_path, "rb") as file:
                response = requests.post(url, files={"image": file})
            
            if response.status_code == 200:
                res_data = response.json()
                direct_url = res_data["data"]["display_url"]
                print("\n" + "="*50, flush=True)
                print(f"👉 DIRECT SCREENSHOT LINK: {direct_url}", flush=True)
                print("="*50 + "\n", flush=True)
            else:
                print(f"[WARNING] ImgBB Upload Failed Status: {response.status_code}", flush=True)
        else:
            print("[WARNING] IMGBBB_API_KEY environment variable not found.", flush=True)
    except Exception as screenshot_err:
        print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)

# =========================
# ENCRYPTION LOGIC (inline)
# =========================
def _derive_key_from_password(password: bytes, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=200_000,
    )
    return kdf.derive(password)

def _encrypt_bytes(data: bytes, password: str) -> dict:
    salt = token_bytes(16)
    nonce = token_bytes(12)
    key = _derive_key_from_password(password.encode("utf-8"), salt)
    aesgcm = AESGCM(key)
    ct = aesgcm.encrypt(nonce, data, None)
    return {
        "v": 1,
        "s": base64.b64encode(salt).decode("ascii"),
        "n": base64.b64encode(nonce).decode("ascii"),
        "ct": base64.b64encode(ct).decode("ascii"),
    }

def encrypt_and_save_cookies(cookies_data, email, decrypt_key):
    plaintext = json.dumps(cookies_data).encode("utf-8")
    payload = _encrypt_bytes(plaintext, decrypt_key)
    
    filename = f"{email}_chatgpt_cookies.json.encrypted"
    target_path = os.path.join(OUTPUT_DIR, filename)
    
    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    
    print(f"[OK] Encrypted cookies successfully saved to: {target_path}", flush=True)

def find_verification_code_in_email(atomic_page):
    """
    Click on the first email in the list using xpath //main//li[1]//a[1],
    then look for a cell containing the verification code text.
    Uses getByRole('cell', { name: regex }) to find the code.
    Returns the code or None if not found.
    """
    try:
        # Click first email in the inbox list
        print("[INFO] Clicking first email in inbox...", flush=True)
        try:
            atomic_page.locator('//main//li[1]//a[1]').click(timeout=5000)
        except Exception:
            print("[WARNING] First email locator not found. Pressing Escape and retrying...", flush=True)
            atomic_page.keyboard.press('Escape')
            custom_random_wait(1, 2)
            atomic_page.locator('//main//li[1]//a[1]').click()
        custom_random_wait(3, 5)

        # Look for a cell containing the verification code text
        print("[INFO] Looking for verification code cell inside email...", flush=True)
        # Use filter with has_text to find the cell that contains the e
        code_cells = atomic_page.get_by_role('cell').filter(has_text=re.compile(r'\d{6}'))
        count = code_cells.count()
        
        found_code = None
        for i in range(count):
            try:
                cell_text = code_cells.nth(i).text_content()
                if 'temporary verification code' in cell_text or 'login code' in cell_text or 'temporary code' in cell_text:
                    print(f"[INFO] Found verification code cell.", flush=True)
                    code_match = re.search(r'(\d{6})', cell_text)
                    if code_match:
                        found_code = code_match.group(1)
                        print(f"[OK] Extracted verification code: {found_code}", flush=True)
                        return found_code
            except:
                pass
        
        if not found_code:
            print("[WARNING] No verification code cell found in email.", flush=True)
            return None
        
        print("[WARNING] No verification code cell found in email.", flush=True)
        return None
    except Exception as e:
        print(f"[WARNING] Error while reading email: {e}", flush=True)
        return None

def go_back_to_inbox(atomic_page):
    """Click the back button to return to inbox."""
    try:
        print("[INFO] Clicking back button to return to inbox...", flush=True)
        try:
            atomic_page.get_by_test_id('reader-back-button').click(timeout=5000)
        except Exception:
            print("[WARNING] Back button not found. Pressing Escape and retrying...", flush=True)
            atomic_page.keyboard.press('Escape')
            custom_random_wait(1, 2)
            atomic_page.get_by_test_id('reader-back-button').click()
        custom_random_wait(2, 4)
    except Exception as e:
        print(f"[WARNING] Error clicking back button: {e}", flush=True)

# =========================
# MAIN FLOW
# =========================
def process_email(email, decrypt_key, pw):
    """
    Process a single email: login to ChatGPT, get verification code from Atomic Mail,
    enter code, save cookies. Returns True on success, False on failure.
    """
    print(f"\n{'='*60}", flush=True)
    print(f"[INFO] Processing email: {email}", flush=True)
    print(f"{'='*60}", flush=True)

    browser = None
    page = None
    try:
        browser = pw.chromium.launch(
            headless=HEADLESS,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled"
            ]
        )

        context = browser.new_context(
            no_viewport=True,
            user_agent=USER_AGENT
        )

        # =========================
        # STEP 1: Login to ChatGPT
        # =========================
        page = context.new_page()

        print(f"[STEP] Navigating to URL: {TARGET_URL}...", flush=True)
        page.goto(TARGET_URL, wait_until="domcontentloaded")
        print(f"[OK] {TARGET_URL} opened completely.", flush=True)
        custom_random_wait(6, 12)

        print("[STEP] Checking if email field is already visible...", flush=True)
        email_field = page.get_by_role('textbox', name='Email address')
        if email_field.is_visible():
            print("[INFO] Email field already visible. Skipping login button click.", flush=True)
        else:
            print("[STEP] Clicking Login Button...", flush=True)
            login_btn = page.get_by_test_id('login-button')
            if not login_btn.is_visible():
                login_btn = page.get_by_label('Chat history').get_by_role('button', name='Log in')
            
            login_btn.click()
            custom_random_wait(6, 12)

        print("[STEP] Entering email address...", flush=True)
        page.get_by_role('textbox', name='Email address').fill(email)
        custom_random_wait(6, 12)

        print("[STEP] Clicking Continue...", flush=True)
        page.get_by_role('button', name='Continue', exact=True).click()
        custom_random_wait(6, 12)

        # =========================
        # STEP 2: Open Atomic Mail in new tab and login
        # =========================
        print("[STEP] Opening Atomic Mail in new tab...", flush=True)
        atomic_page = context.new_page()
        atomic_page.goto(ATOMIC_MAIL_URL, wait_until="domcontentloaded")
        print(f"[OK] {ATOMIC_MAIL_URL} opened completely.", flush=True)
        custom_random_wait(6, 12)

        print("[STEP] Clicking Sign In link...", flush=True)
        atomic_page.get_by_role('link', name='Sign In').click()
        custom_random_wait(6, 12)

        # Wait for the username field to be visible using multiple possible selectors
        print("[STEP] Waiting for username field to be ready...", flush=True)
        username_input = atomic_page.locator("input[name='username'], input[id='username'], input[type='text'], [data-testid='username-input']").first
        username_input.wait_for(state="visible", timeout=10000)
        custom_random_wait(1, 2)

        username = extract_username_from_email(email)
        print(f"[STEP] Entering username: {username}...", flush=True)
        username_input.fill(username)
        custom_random_wait(2, 4)

        print("[STEP] Clicking login submit...", flush=True)
        submit_btn = atomic_page.locator("button[type='submit'], input[type='submit'], [data-testid='login-submit'], button:has-text('Continue'), button:has-text('Sign In')").first
        submit_btn.wait_for(state="visible", timeout=5000)
        submit_btn.click()
        custom_random_wait(2, 4)

        print("[STEP] Entering password...", flush=True)
        password_input = atomic_page.locator("input[name='password'], input[id='password'], input[type='password'], [data-testid='password-input']").first
        password_input.wait_for(state="visible", timeout=10000)
        password_input.fill(decrypt_key)
        custom_random_wait(2, 4)

        print("[STEP] Clicking sign in button...", flush=True)
        sign_in_btn = atomic_page.locator("button[type='submit'], input[type='submit'], [data-testid='login-sign-in'], button:has-text('Sign In'), button:has-text('Log in')").first
        sign_in_btn.wait_for(state="visible", timeout=5000)
        sign_in_btn.click()
        custom_random_wait(5, 8)

        # =========================
        # STEP 3: Find the ChatGPT verification code email with retry logic
        # =========================
        print("[STEP] Looking for ChatGPT verification code email...", flush=True)
        verification_code = None
        max_outer_retries = 5

        for outer_attempt in range(max_outer_retries):
            print(f"\n[INFO] Outer retry set {outer_attempt + 1}/{max_outer_retries}", flush=True)
            
            # Inner retries: check atomic mail page up to 5 times
            max_inner_retries = 5
            for inner_attempt in range(max_inner_retries):
                print(f"[INFO] Inner retry {inner_attempt + 1}/{max_inner_retries} - checking for verification email...", flush=True)
                
                # Click first email in inbox and look for code
                verification_code = find_verification_code_in_email(atomic_page)
                if verification_code:
                    break
                
                # Go back to inbox and reload
                go_back_to_inbox(atomic_page)
                atomic_page.reload(wait_until="domcontentloaded")
                custom_random_wait(5, 8)
            
            if verification_code:
                break
            
            print("[STEP] Code not found. Switching to ChatGPT page to click 'Resend email'...", flush=True)
            page.bring_to_front()
            custom_random_wait(2, 4)
            
            print("[STEP] Clicking 'Resend email' button...", flush=True)
            try:
                page.get_by_role('button', name='Resend email').click(timeout=5000)
            except Exception:
                print("[WARNING] 'Resend email' button by role not found. Trying CSS selector fallback...", flush=True)
                page.locator("button[value='resend']").click()
            custom_random_wait(3, 5)
            
            # Switch back to atomic mail for next retry set
            print("[STEP] Switching back to Atomic Mail...", flush=True)
            atomic_page.bring_to_front()
            custom_random_wait(2, 4)

        if verification_code is None:
            print("[ERROR] Could not find verification code email after all retries.", flush=True)
            if page:
                capture_and_upload_screenshot(page)
            return False

        # =========================
        # STEP 4: Go back to ChatGPT page and enter the code
        # =========================
        print("[STEP] Switching back to ChatGPT page to enter verification code...", flush=True)
        page.bring_to_front()
        custom_random_wait(2, 4)

        print("[STEP] Typing verification code...", flush=True)
        custom_random_wait(1, 2)
        page.keyboard.press('Tab')
        custom_random_wait(1, 2)
        # Find the code input field and fill it (using xpath= prefix for XPath selector)
        code_field = page.locator("xpath=/html[1]/body[1]/div[1]/div[1]/fieldset[1]/form[1]/div[1]/div[1]/div[1]/div[1]/label[1]/div[1]")
        code_field.wait_for(state="visible", timeout=5000)
        code_field.click()
        page.keyboard.type(verification_code, delay=50)
        custom_random_wait(1, 2)

        print("[STEP] Clicking Continue button...", flush=True)
        page.get_by_role('button', name='Continue').click()
        
        print("[STEP] Waiting for login completion...", flush=True)
        page.wait_for_load_state("networkidle")
        custom_random_wait(5, 8)

        # =========================
        # STEP 5: Verify login success
        # =========================
        print("[STEP] Verifying login success...", flush=True)
        try:
            profile_btn = page.get_by_role('button').filter(has_text='Free')
            profile_btn.wait_for(timeout=10000)
            print(f"[OK] Login successful!", flush=True)
        except Exception:
            print("[ERROR] Login failed or took too long. Profile button not found.", flush=True)
            if page:
                capture_and_upload_screenshot(page)
            return False

        print("[STEP] Harvesting context cookies...", flush=True)
        cookies = context.cookies()
        
        if cookies:
            encrypt_and_save_cookies(cookies, email, decrypt_key)
        else:
            print("[WARNING] No cookies captured. Check if login was blocked.", flush=True)
            if page:
                capture_and_upload_screenshot(page)

        return True

    except Exception as e:
        print(f"[ERROR] Workflow failed for {email}: {e}", flush=True)
        if page:
            capture_and_upload_screenshot(page)
        return False

    finally:
        if browser:
            try:
                browser.close()
                print(f"[INFO] Browser closed for {email}.", flush=True)
            except:
                pass


def run():
    print("[START] Script started", flush=True)
    
    try:
        emails = get_all_emails()
        print(f"[INFO] Found {len(emails)} email(s) to process.", flush=True)
    except Exception as e:
        print(f"[ERROR] Credential error: {e}", flush=True)
        sys.exit(1)
        
    decrypt_key = os.getenv("DECRYPT_KEY")
    if not decrypt_key:
        print("[ERROR] DECRYPT_KEY is missing in environment/.env", flush=True)
        sys.exit(1)

    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()

    any_success = False
    try:
        for email in emails:
            success = False
            max_retries = 5
            
            for attempt in range(1, max_retries + 1):
                print(f"\n[INFO] Attempt {attempt}/{max_retries} for email: {email}", flush=True)
                
                success = process_email(email, decrypt_key, pw)
                
                if success:
                    print(f"[OK] Successfully processed email: {email}", flush=True)
                    any_success = True
                    break
                else:
                    print(f"[WARNING] Attempt {attempt}/{max_retries} failed for email: {email}", flush=True)
                    if attempt < max_retries:
                        print(f"[INFO] Retrying email: {email} with fresh browser session...", flush=True)
                        custom_random_wait(3, 5)
            
            if not success:
                print(f"[ERROR] All {max_retries} attempts failed for email: {email}. Skipping to next email.", flush=True)

    except Exception as e:
        print(f"[ERROR] Unexpected error: {e}", flush=True)
        sys.exit(1)
    finally:
        try:
            pw_cm.__exit__(None, None, None)
        except:
            pass

    if not any_success:
        print("[ERROR] No emails were processed successfully. Exiting with failure.", flush=True)
        sys.exit(1)

    print("[DONE] Process terminated cleanly.", flush=True)

if __name__ == "__main__":
    run()