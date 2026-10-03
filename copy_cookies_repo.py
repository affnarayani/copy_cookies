import os
import sys
import gc
import shutil
import stat
import tempfile
import time
import requests
from git import Repo
from dotenv import load_dotenv

# Git ke interactive credential prompt ko band karein taaki script kabhi hang na ho.
# (Token galat/expire ho to turant error mile, na ki GUI prompt par chup-chaap wait kare.)
os.environ["GIT_TERMINAL_PROMPT"] = "0"
os.environ["GCM_INTERACTIVE"] = "never"

# Slow/stalled network par git khud abort kar de (yeh cross-platform hai; GitPython ka
# kill_after_timeout Windows pe supported nahi hai). 60 sec tak speed 1 KB/s se kam rahe
# to git abort kar dega - isse clone kabhi ghanton tak latak nahi sakta.
os.environ["GIT_HTTP_LOW_SPEED_LIMIT"] = "1000"
os.environ["GIT_HTTP_LOW_SPEED_TIME"] = "60"

def upload_to_tmpfiles(screenshot_path):
    url = "https://tmpfiles.org/api/v1/upload"
    
    with open(screenshot_path, "rb") as file:
        response = requests.post(url, files={"file": file})
        
    if response.status_code == 200:
        res_data = response.json()
        # Direct view URL banane ke liye '/dl/' replace karte hain
        page_url = res_data["data"]["url"]
        direct_url = page_url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
        print(f"[INFO] DIRECT LINK (Expires in 2 Hours): {direct_url}")
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
        
    # 2. Windows file/dir lock ke liye retry logic (Max 5 baar koshish).
    #    Sirf WinError 32 par nahi, balki kisi bhi transient OSError par retry karte hain
    #    (antivirus / search indexer / Git process kabhi-kabhi aur error codes dete hain).
    for i in range(5):
        try:
            func(path)
            return # Agar delete ho gaya toh loop se baahar
        except OSError:
            time.sleep(1) # 1 second ka pause taaki file/dir lock release ho jaye
                
    # Agar 5 baar mein bhi na ho, toh crash karne ke badle warning dekar aage badhein
    print(f"[WARNING] File release nahi ho payi, skipping: {path}")


def clean_temp_dir(path):
    """Temp workspace ko poori tarah delete karta hai aur success (True/False) return karta hai.

    Windows par kabhi-kabhi folder ka root handle der se release hota hai, isliye
    rmtree ko kuch dafa (backoff ke saath) retry karte hain.
    """
    if not os.path.exists(path):
        return True

    for attempt in range(3):
        shutil.rmtree(path, onerror=remove_readonly)
        if not os.path.exists(path):
            return True
        time.sleep(1)

    return not os.path.exists(path)

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
    raise ValueError("[ERROR] .env file mein 'PAT_TOKEN_ALL' nahi mila! Pehle use check karein.")

# Local Source Folder
SOURCE_FOLDER = "chatgpt_cookies"

# Check karein ki local source folder exist karta hai ya nahi
if not os.path.exists(SOURCE_FOLDER):
    raise FileNotFoundError(f"[ERROR] Local folder '{SOURCE_FOLDER}' nahi mila! Script ko sahi jagah se run karein.")

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
    },
    {
        "owner": "affnarayani",
        "name": "book_mint_ujjawal",
        "dest_folder": "cookies"
    }
]

# Har repo ke liye isi prefix se ek naya, unique temp folder banega
TEMP_DIR_PREFIX = "temp_destination_repo_"

any_failure = False

# Script start hone par purane (crash se bache) temp folders saaf karein
for _entry in os.listdir("."):
    if _entry.startswith(TEMP_DIR_PREFIX):
        clean_temp_dir(os.path.join(".", _entry))

# Loop chala kar har repository ko bari-bari update karenge
for dest in DESTINATIONS:
    repo_owner = dest["owner"]
    repo_name = dest["name"]
    dest_folder_name = dest["dest_folder"]
    
    # Authenticated GitHub URL
    dest_repo_url = f"https://{PAT_TOKEN_ALL}@github.com/{repo_owner}/{repo_name}.git"
    
    print("\n" + "="*50)
    print(f"[INFO] Starting sync for: {repo_name}...")
    print("="*50)

    dest_repo = None
    origin = None
    temp_dir = None
    try:
        # Har repo ke liye ek naya, unique temp folder banayein
        temp_dir = tempfile.mkdtemp(prefix=f"{TEMP_DIR_PREFIX}{repo_name}_", dir=".")
        print(f"[INFO] Created fresh temp folder: {temp_dir}")
            
        # 1. Repo Clone karein
        print(f"Cloning {repo_name}...")
        # Shallow clone (--depth 1): in repos ki history bahut bhaari hai (har run ke
        # screenshots + cookies), isliye full clone hang jaisa lagta hai. Depth 1 se sirf
        # latest snapshot aata hai -> bahut tez aur chhota.
        dest_repo = Repo.clone_from(
            dest_repo_url,
            temp_dir,
            multi_options=["--depth", "1", "--single-branch", "--no-tags"],
        )
        
        target_path = os.path.join(temp_dir, dest_folder_name)

        # 2. Copy se pehle destination folder ko poora EMPTY karein (purana content hata dein)
        # NOTE: copytree khud target folder banata hai, isliye yahan dobara create nahi karte.
        if os.path.exists(target_path):
            shutil.rmtree(target_path, onerror=remove_readonly)
            print(f"[INFO] Emptied old folder: {target_path}")
        
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
            print(f"[OK] Success! Cookies '{repo_name}' mein copy aur push ho gayi hain.")
        else:
            print(f"Silent Sync: '{repo_name}' mein koi badlav nahi mila, dono pehle se same hain.")

    except Exception as e:
        print(f"[ERROR] Error occurred while processing {repo_name}: {e}")
        upload_error_screenshot()
        any_failure = True

    finally:
        # GitPython ke SAARE references release karein, warna Windows par temp folder ka
        # handle open rehta hai aur folder delete nahi hota. Dhyan rahe: `origin` (Remote)
        # bhi repo ko reference karta hai (origin.repo), isliye use bhi None karna zaroori hai.
        origin = None
        if dest_repo is not None:
            try:
                dest_repo.close()
            except Exception:
                pass
        dest_repo = None
        gc.collect()

        # Is repo ka kaam khatam - ab sirf isi repo ka temp folder delete karein
        if temp_dir and os.path.exists(temp_dir):
            print(f"Cleaning up temporary workspace for {repo_name}...")
            if clean_temp_dir(temp_dir):
                print("Workspace cleaned successfully!")
            else:
                print(f"[WARNING] Temporary folder abhi bhi maujood hai: {temp_dir}")

if any_failure:
    print("\n[ERROR] Some repositories failed to sync. Exiting with failure.", flush=True)
    sys.exit(1)
else:
    print("\n[DONE] All repository sync processes finished successfully!")
