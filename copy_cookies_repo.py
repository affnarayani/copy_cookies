import os
import sys
import shutil
import stat
import time
import requests
from git import Repo
from dotenv import load_dotenv

def upload_to_tmpfiles(screenshot_path):
    url = "https://tmpfiles.org/api/v1/upload"
    
    with open(screenshot_path, "rb") as file:
        response = requests.post(url, files={"file": file})
        
    if response.status_code == 200:
        res_data = response.json()
        # Direct view URL banane ke liye '/dl/' replace karte hain
        page_url = res_data["data"]["url"]
        direct_url = page_url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
        print(f"👉 DIRECT LINK (Expires in 2 Hours): {direct_url}")
        return direct_url
    else:
        print(f"[WARNING] Upload Failed: {response.status_code}")
        return None
    
# Robust cleanup handler: Read-only aur locked files ko handle karne ke liye
def remove_readonly(func, path, excinfo):
    # 1. Pehle permission ko writable banayein
    try:
        os.chmod(path, stat.S_IWRITE)
    except Exception:
        pass
        
    # 2. Windows File Lock (WinError 32) ke liye retry logic (Max 3 baar koshish)
    for i in range(3):
        try:
            func(path)
            return # Agar delete ho gaya toh loop se baahar
        except OSError as e:
            # Agar error 'File being used by another process' hai, toh thoda wait karein
            if getattr(e, 'winerror', None) == 32 or e.errno == 32:
                time.sleep(1) # 1 second ka pause taaki Git process release ho jaye
            else:
                break
                
    # Agar 3 baar mein bhi na ho, toh crash karne ke badle warning dekar aage badhein
    print(f"⚠️ Temporary file release nahi ho payi, skipping: {path}")

def upload_error_screenshot():
    """Upload error_screenshot.png to ImgBB if it exists."""
    screenshot_path = "error_screenshot.png"
    if not os.path.exists(screenshot_path):
        print("[INFO] No error_screenshot.png found to upload.", flush=True)
        return
    try:
        upload_to_tmpfiles(screenshot_path)
    except Exception as screenshot_err:
        print(f"[WARNING] Could not upload screenshot: {screenshot_err}", flush=True)

# 1. .env file ko load karein
load_dotenv()

# 2. Token ko environment variables se read karein
PAT_TOKEN_ALL = os.getenv("PAT_TOKEN_ALL")

if not PAT_TOKEN_ALL:
    raise ValueError("❌ Error: .env file mein 'PAT_TOKEN_ALL' nahi mila! Pehle use check karein.")

# Local Source Folder
SOURCE_FOLDER = "chatgpt_cookies"

# Check karein ki local source folder exist karta hai ya nahi
if not os.path.exists(SOURCE_FOLDER):
    raise FileNotFoundError(f"❌ Error: Local folder '{SOURCE_FOLDER}' nahi mila! Script ko sahi jagah se run karein.")

# --- MULTIPLE DESTINATIONS CONFIGURATION ---
DESTINATIONS = [
    {
        "owner": "affnarayani",
        "name": "red_suite",
        "dest_folder": "cookies"
    },
    {
        "owner": "affnarayani",
        "name": "medium_forge",
        "dest_folder": "cookies"
    },
    {
        "owner": "affnarayani",
        "name": "writer_stack",
        "dest_folder": "cookies"
    },
    {
        "owner": "affnarayani",
        "name": "q_rise",
        "dest_folder": "chatgpt_cookies"
    },
    {
        "owner": "affnarayani",
        "name": "pin_pilot_ujjawal",
        "dest_folder": "cookies"
    },
    {
        "owner": "affnarayani",
        "name": "link_boost_priyanka",
        "dest_folder": "chatgpt_cookies"
    },
    {
        "owner": "affnarayani",
        "name": "link_boost_ujjawal",
        "dest_folder": "chatgpt_cookies"
    },
    {
        "owner": "affnarayani",
        "name": "link_boost_umang",
        "dest_folder": "chatgpt_cookies"
    },
    {
        "owner": "affnarayani",
        "name": "face_flow_ashwini",
        "dest_folder": "chatgpt_cookies"
    },
    {
        "owner": "affnarayani",
        "name": "face_flow_priyanka",
        "dest_folder": "chatgpt_cookies"
    },
    {
        "owner": "affnarayani",
        "name": "face_flow_ujjawal",
        "dest_folder": "chatgpt_cookies"
    },
    {
        "owner": "affnarayani",
        "name": "face_flow_umang",
        "dest_folder": "chatgpt_cookies"
    },
    {
        "owner": "affnarayani",
        "name": "clear_llm",
        "dest_folder": "cookies_gpt"
    }
]

TEMP_DIR = "./temp_destination_repo"

any_failure = False

# Loop chala kar har repository ko bari-bari update karenge
for dest in DESTINATIONS:
    repo_owner = dest["owner"]
    repo_name = dest["name"]
    dest_folder_name = dest["dest_folder"]
    
    # Authenticated GitHub URL
    dest_repo_url = f"https://{PAT_TOKEN_ALL}@github.com/{repo_owner}/{repo_name}.git"
    
    print("\n" + "="*50)
    print(f"🔄 Starting sync for: {repo_name}...")
    print("="*50)

    try:
        # Purana koi temp folder bacha ho toh use pehle clean karein
        if os.path.exists(TEMP_DIR):
            shutil.rmtree(TEMP_DIR, onerror=remove_readonly)
            
        # 1. Repo Clone karein
        print(f"Cloning {repo_name}...")
        dest_repo = Repo.clone_from(dest_repo_url, TEMP_DIR)
        
        target_path = os.path.join(TEMP_DIR, dest_folder_name)

        # 2. Fresh copy ke liye purana target folder saaf karein
        if os.path.exists(target_path):
            shutil.rmtree(target_path, onerror=remove_readonly)
        
        # 3. Contents copy karein
        print(f"Copying '{SOURCE_FOLDER}' contents to '{dest_folder_name}'...")
        shutil.copytree(SOURCE_FOLDER, target_path)

        # 4. Changes Push karein
        print("Pushing changes to GitHub...")
        dest_repo.git.add(A=True)
        
        if dest_repo.is_dirty():
            dest_repo.index.commit("Automated Sync: Updated cookies via multi-repo script")
            origin = dest_repo.remote(name='origin')
            origin.push()
            print(f"🎉 Success! Cookies '{repo_name}' mein copy aur push ho gayi hain.")
        else:
            print(f"Silent Sync: '{repo_name}' mein koi badlav nahi mila, dono pehle se same hain.")

    except Exception as e:
        print(f"❌ Error occurred while processing {repo_name}: {e}")
        upload_error_screenshot()
        any_failure = True

    finally:
        # Har repo ka kaam khatam hone ke baad temp folder saaf karein
        if os.path.exists(TEMP_DIR):
            print(f"Cleaning up temporary workspace for {repo_name}...")
            try:
                shutil.rmtree(TEMP_DIR, onerror=remove_readonly)
                print("Workspace cleaned successfully!")
            except Exception as cleanup_error:
                print(f"⚠️ Temporary folder delete nahi ho paya. Error: {cleanup_error}")

if any_failure:
    print("\n❌ Some repositories failed to sync. Exiting with failure.", flush=True)
    sys.exit(1)
else:
    print("\n🚀 All repository sync processes finished successfully!")
