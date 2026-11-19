"""Helper script to test CAPTCHA solving using Nonecap API on hCaptcha demo page.

Conventions followed from this project:
- Waits use custom_random_wait() and absolute_wait() driven by json/config.json.
- .env provides NONECAP_API_KEY (for Nonecap API).
- captcha_helper.py always runs headless=False.
"""

import os
import random
import shutil
import sys
import tempfile
import time

import requests
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from common.config import ROOT_DIR, get_config, resolve_path
from common.upload_utils import upload_to_onlyfiles

load_dotenv(os.path.join(ROOT_DIR, ".env"))

CONFIG = get_config()
HEADLESS = True
USER_AGENT = CONFIG["browser"]["user_agent"]
TYPING_DELAY = int(CONFIG["browser"]["typing_delay_ms"])
NONECAP_API_KEY = os.getenv("NONECAP_API_KEY")

HCAPTCHA_DEMO_URL = "https://accounts.hcaptcha.com/demo"
HCAPTCHA_SITEKEY = "a5f74b19-9e45-40e0-b45d-47ff91b7a6c2"

ERROR_SCREENSHOT = resolve_path(CONFIG["paths"]["error_screenshot"])
NONECAP_API_URL = "https://api.nonecap.com/v1/solves"


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


def solve_hcaptcha_with_nonecap(page, sitekey, url):
    """Use Nonecap API to solve hCaptcha and inject token."""
    if not NONECAP_API_KEY:
        print("[ERROR] NONECAP_API_KEY not set in environment", flush=True)
        return False

    print(f"[STEP] Submitting hCaptcha to Nonecap API...", flush=True)
    
    headers = {
        "Authorization": f"Bearer {NONECAP_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "type": "hcaptcha",
        "sitekey": sitekey,
        "url": url
    }
    
    try:
        # Submit solve request with wait=30 to get result directly
        response = requests.post(
            f"{NONECAP_API_URL}?wait=30",
            headers=headers,
            json=payload,
            timeout=60
        )
        
        if response.status_code != 200:
            print(f"[ERROR] Nonecap API error: {response.status_code} - {response.text}", flush=True)
            return False
        
        result = response.json()
        
        # Extract token from response
        token = result.get("data", {}).get("token") or result.get("token")
        if not token:
            print(f"[ERROR] No token in Nonecap response", flush=True)
            return False
        
        print(f"[OK] Got hCaptcha token from Nonecap (length: {len(token)})", flush=True)
        
        # Inject token into page
        print("[STEP] Injecting hCaptcha token into page...", flush=True)
        
        # Method 1: Set the hidden textarea value and trigger callback
        page.evaluate(f"""() => {{
            // hCaptcha stores response in a textarea
            const textarea = document.querySelector('[name="h-captcha-response"]') || 
                           document.querySelector('[name="g-recaptcha-response"]') ||
                           document.querySelector('textarea#h-captcha-response');
            if (textarea) {{
                textarea.value = '{token}';
                textarea.dispatchEvent(new Event('change', {{ bubbles: true }}));
                textarea.dispatchEvent(new Event('input', {{ bubbles: true }}));
                console.log('Set h-captcha-response textarea');
            }}
            
            // Also try calling hcaptcha.setResponse if available
            if (window.hcaptcha) {{
                try {{
                    window.hcaptcha.setResponse('{token}');
                    console.log('Called hcaptcha.setResponse');
                }} catch (e) {{ console.log('hcaptcha.setResponse failed:', e); }}
            }}
            
            // Try to execute callback if exists
            if (window.hcaptcha && window.hcaptcha.callback) {{
                try {{
                    window.hcaptcha.callback('{token}');
                    console.log('Called hcaptcha.callback');
                }} catch (e) {{}}
            }}
            
            // Try to execute enterprise callback
            if (window.hcaptcha && window.hcaptcha.enterprise && window.hcaptcha.enterprise.callback) {{
                try {{
                    window.hcaptcha.enterprise.callback('{token}');
                    console.log('Called hcaptcha.enterprise.callback');
                }} catch (e) {{}}
            }}
            
            // Trigger any callbacks on forms
            const forms = document.querySelectorAll('form');
            forms.forEach(form => form.dispatchEvent(new Event('submit', {{ bubbles: true }})));
            
            // Try to find and call any global callback functions
            const possibleCallbacks = ['onCaptchaSolved', 'onCaptchaSuccess', 'onSuccess', 'captchaCallback', 'hCaptchaCallback', 'onChallengeSuccess'];
            possibleCallbacks.forEach(cbName => {{
                if (typeof window[cbName] === 'function') {{
                    try {{ window[cbName]('{token}'); console.log('Called global callback:', cbName); }} catch (e) {{}}
                }}
            }});
        }}""")
        
        custom_random_wait(2, 3)
        
        # Verify token was set (masked)
        token_check = page.evaluate("""() => {
            const textarea = document.querySelector('[name="h-captcha-response"]');
            return textarea ? textarea.value : 'not found';
        }""")
        if token_check and token_check != 'not found':
            print(f"[DEBUG] Token in textarea: {token_check[:20]}... (masked)", flush=True)
        else:
            print("[DEBUG] Token in textarea: not found", flush=True)
        
        # Click verify/submit buttons on main page
        print("[STEP] Looking for verify/submit buttons on main page...", flush=True)
        try:
            for selector in [
                'button:has-text("Verify")',
                'button:has-text("Submit")',
                'button:has-text("Continue")',
                'button[type="submit"]',
                'button[id*="verify"]',
                'button[class*="verify"]',
                '.verify-button',
                '#verify-button',
                'input[type="submit"]'
            ]:
                try:
                    btns = page.locator(selector).all()
                    for btn in btns:
                        if btn.is_visible(timeout=1000):
                            btn.click()
                            print(f"[OK] Clicked button on main page: {selector}", flush=True)
                            custom_random_wait(1, 2)
                except Exception:
                    continue
        except Exception:
            pass
        
        # Try triggering hCaptcha verification directly
        print("[STEP] Triggering hCaptcha verification...", flush=True)
        try:
            page.evaluate(f"""() => {{
                // Try hcaptcha.execute if available
                if (window.hcaptcha) {{
                    try {{
                        window.hcaptcha.execute();
                        console.log('Called hcaptcha.execute()');
                    }} catch (e) {{ console.log('hcaptcha.execute failed:', e); }}
                }}
                
                // Try to find and call any verification function
                const verifyFuncs = ['verifyCaptcha', 'verifyHcaptcha', 'onVerify', 'doVerify', 'submitCaptcha'];
                verifyFuncs.forEach(fn => {{
                    if (typeof window[fn] === 'function') {{
                        try {{ window[fn](); console.log('Called:', fn); }} catch (e) {{}}
                    }}
                }});
                
                // Try clicking any button with hcaptcha-related text
                document.querySelectorAll('button').forEach(btn => {{
                    const text = btn.innerText || btn.textContent || '';
                    if (/verify|submit|continue|confirm/i.test(text)) {{
                        try {{ btn.click(); console.log('Clicked hcaptcha-related button:', text); }} catch (e) {{}}
                    }}
                }});
            }}""")
            custom_random_wait(2, 3)
        except Exception:
            pass
        
        return True
        
    except requests.exceptions.Timeout:
        print("[ERROR] Nonecap API timeout", flush=True)
        return False
    except Exception as e:
        print(f"[ERROR] Nonecap API request failed: {e}", flush=True)
        return False


def run_captcha_test(pw):
    user_data_dir = tempfile.mkdtemp(prefix="captcha_helper_")

    context = None
    page = None
    try:
        print("[DEBUG] Launching browser context...", flush=True)
        launch_args = [
            "--start-maximized",
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
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
        page.wait_for_timeout(5000)

        print(f"[STEP] Navigating to hCaptcha demo: {HCAPTCHA_DEMO_URL}", flush=True)
        page.goto(HCAPTCHA_DEMO_URL, wait_until="domcontentloaded")
        absolute_wait()

        # Wait for checkbox iframe and click it
        print("[STEP] Waiting for hCaptcha checkbox...", flush=True)
        checkbox_frame = page.frame_locator('iframe[src*="hcaptcha.com"][src*="frame=checkbox"]')
        checkbox_frame.locator('#checkbox, .checkbox, [role="checkbox"]').first.click()
        print("[OK] Clicked checkbox", flush=True)
        custom_random_wait(2, 3)

        # Wait for challenge to appear (or success) - up to 60 seconds
        print("[STEP] Waiting for challenge or success...", flush=True)
        start = time.time()
        challenge_appeared = False
        while time.time() - start < 60:
            try:
                # Check for success first
                if page.get_by_text("Challenge Success!").is_visible(timeout=1000):
                    print("[OK] Challenge Success! found (no challenge needed)", flush=True)
                    return True
                
                # Check for challenge iframe
                challenge_frame = page.frame_locator('iframe[src*="hcaptcha.com"][src*="frame=challenge"]')
                if challenge_frame.locator('body').is_visible(timeout=1000):
                    challenge_appeared = True
                    print("[OK] Challenge iframe appeared", flush=True)
                    break
            except Exception:
                pass
            custom_random_wait(1, 2)
        
        if not challenge_appeared:
            # Final check for success
            try:
                if page.get_by_text("Challenge Success!").is_visible(timeout=3000):
                    print("[OK] Challenge Success! found", flush=True)
                    return True
            except Exception:
                pass
            print("[INFO] No challenge appeared", flush=True)
            return False

        # Solve with Nonecap API
        print("[STEP] Solving hCaptcha with Nonecap API...", flush=True)
        success = solve_hcaptcha_with_nonecap(page, HCAPTCHA_SITEKEY, HCAPTCHA_DEMO_URL)
        
        if not success:
            print("[ERROR] Failed to solve hCaptcha with Nonecap", flush=True)
            return False
        
        # Wait and check for success - poll for up to 60 seconds
        print("[STEP] Waiting for Challenge Success! after token injection...", flush=True)
        start = time.time()
        while time.time() - start < 60:
            try:
                if page.get_by_text("Challenge Success!").is_visible(timeout=2000):
                    print("[OK] Challenge Success! found after solving", flush=True)
                    return True
            except Exception:
                pass
            custom_random_wait(1, 2)
        
        print("[ERROR] Challenge Success! not found after solving", flush=True)
        return False

    except Exception as error:
        print(f"[ERROR] CAPTCHA test failed: {error}", flush=True)
        capture_error_screenshot(page)
        return False
    finally:
        if context:
            try:
                context.close()
                print("[INFO] Context closed.", flush=True)
            except Exception:
                pass
        try:
            if user_data_dir and os.path.exists(user_data_dir):
                shutil.rmtree(user_data_dir, ignore_errors=True)
                print("[INFO] Temporary browser profile cleaned up.", flush=True)
        except Exception:
            pass


def run():
    print("[START] CAPTCHA Helper (Nonecap API) started", flush=True)

    if not NONECAP_API_KEY:
        print("[ERROR] NONECAP_API_KEY not set in .env or environment", flush=True)
        sys.exit(1)

    stealth = Stealth()
    playwright_manager = stealth.use_sync(sync_playwright())
    pw = playwright_manager.__enter__()

    success = False
    try:
        print(f"\n{'=' * 60}", flush=True)
        print("[INFO] Running CAPTCHA test on hCaptcha demo page (Nonecap API)", flush=True)
        print(f"{'=' * 60}", flush=True)
        success = run_captcha_test(pw)
        if success:
            print("[OK] CAPTCHA test passed!", flush=True)
        else:
            print("[ERROR] CAPTCHA test failed", flush=True)
        
        # Print Nonecap account balance
        try:
            print("[STEP] Fetching Nonecap account balance...", flush=True)
            me_response = requests.get(
                "https://api.nonecap.com/v1/me",
                headers={"Authorization": f"Bearer {NONECAP_API_KEY}"},
                timeout=10
            )
            if me_response.status_code == 200:
                me = me_response.json()
                credits = me.get("credits_balance") or me.get("balance") or me.get("credits") or "unknown"
                print(f"[INFO] Nonecap Balance: {credits} credits", flush=True)
            else:
                print(f"[WARNING] Failed to fetch balance: {me_response.status_code}", flush=True)
        except Exception as e:
            print(f"[WARNING] Could not fetch Nonecap balance: {e}", flush=True)
        
        sys.exit(0 if success else 1)
    except Exception as error:
        print(f"[ERROR] Unexpected error: {error}", flush=True)
        sys.exit(1)
    finally:
        try:
            playwright_manager.__exit__(None, None, None)
        except Exception:
            pass


if __name__ == "__main__":
    run()