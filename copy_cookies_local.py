import os
import shutil
import stat
import time

# ==================== CONFIGURATION (PATHS) ====================
# Agar 'chatgpt_cookies' folder isi script ke sath same location par hai,
# toh yeh default sahi hai. Aap chahein toh iska absolute path bhi de sakte hain.
SOURCE_FOLDER = "chatgpt_cookies"

DESTINATION_FOLDERS = [
    r"D:\Coding\red_suite\cookies",
    r"D:\Coding\medium_forge\cookies",
    r"D:\Coding\writer_stack\cookies",
    r"D:\Coding\q_rise\chatgpt_cookies",
    r"D:\Coding\pin_pilot_ujjawal\cookies",
    r"D:\Coding\link_boost_priyanka\chatgpt_cookies",
    r"D:\Coding\link_boost_ujjawal\chatgpt_cookies",
    r"D:\Coding\link_boost_umang\chatgpt_cookies",
    r"D:\Coding\face_flow_ashwini\chatgpt_cookies",
    r"D:\Coding\face_flow_priyanka\chatgpt_cookies",
    r"D:\Coding\face_flow_ujjawal\chatgpt_cookies",
    r"D:\Coding\face_flow_umang\chatgpt_cookies",
    r"D:\Coding\clear_llm\cookies_gpt",
    r"D:\Coding\book_mint_ujjawal\cookies"
]
# ===============================================================

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
            return  # Agar delete ho gaya toh loop se baahar
        except OSError as e:
            # Agar error 'File being used by another process' hai, toh thoda wait karein
            if getattr(e, 'winerror', None) == 32 or e.errno == 32:
                time.sleep(1)  # 1 second ka pause taaki file lock release ho jaye
            else:
                break

    # Agar 3 baar mein bhi na ho, toh crash karne ke badle warning dekar aage badhein
    print(f"[WARNING] File release nahi ho payi, skipping: {path}")


def empty_destination_folder(dest_path):
    """Destination folder ka poora content delete karke use fresh/empty banata hai."""
    # Purana folder (aur uske andar ki saari files/subfolders) hata dein
    if os.path.exists(dest_path):
        shutil.rmtree(dest_path, onerror=remove_readonly)

    # Ab ek fresh empty folder banayein
    os.makedirs(dest_path)


def copy_cookie_files():
    # 1. Check if source folder exists
    if not os.path.exists(SOURCE_FOLDER):
        print(f"[ERROR] Source folder '{SOURCE_FOLDER}' nahi mila. Kripya path check karein.")
        return

    # 2. Get all files from the source folder
    files_to_copy = [
        f for f in os.listdir(SOURCE_FOLDER) 
        if os.path.isfile(os.path.join(SOURCE_FOLDER, f))
    ]

    if not files_to_copy:
        print(f"[WARNING] Source folder '{SOURCE_FOLDER}' mein koi file nahi mili.")
        return

    print(f"Total {len(files_to_copy)} files copy hone ke liye taiyar hain...\n")

    # 3. Loop through each destination and copy files
    for dest_path in DESTINATION_FOLDERS:
        try:
            # Copy karne se pehle destination folder ko poora EMPTY karein
            was_existing = os.path.exists(dest_path)
            empty_destination_folder(dest_path)
            if was_existing:
                print(f"[INFO] Emptied old folder: {dest_path}")
            else:
                print(f"[INFO] Created new folder: {dest_path}")

            # Files copy karne ka process
            for file_name in files_to_copy:
                source_file_path = os.path.join(SOURCE_FOLDER, file_name)
                dest_file_path = os.path.join(dest_path, file_name)
                
                # shutil.copy2 use karne se file ka metadata (date, time) bhi maintain rehta hai
                shutil.copy2(source_file_path, dest_file_path)

            print(f"[OK] Successfully copied files to: {dest_path}")

        except Exception as e:
            print(f"[ERROR] Error while copying to {dest_path}: {e}")

if __name__ == "__main__":
    copy_cookie_files()