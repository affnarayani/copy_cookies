import os
import shutil

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
    r"D:\Coding\link_boost_ujjawal\chatgpt_cookies",
    r"D:\Coding\link_boost_umang\chatgpt_cookies",
    r"D:\Coding\clear_llm\cookies_gpt"
]
# ===============================================================

def copy_cookie_files():
    # 1. Check if source folder exists
    if not os.path.exists(SOURCE_FOLDER):
        print(f"❌ Error: Source folder '{SOURCE_FOLDER}' nahi mila. Kripya path check karein.")
        return

    # 2. Get all files from the source folder
    files_to_copy = [
        f for f in os.listdir(SOURCE_FOLDER) 
        if os.path.isfile(os.path.join(SOURCE_FOLDER, f))
    ]

    if not files_to_copy:
        print(f"⚠️ Source folder '{SOURCE_FOLDER}' mein koi file nahi mili.")
        return

    print(f"Total {len(files_to_copy)} files copy hone ke liye taiyar hain...\n")

    # 3. Loop through each destination and copy files
    for dest_path in DESTINATION_FOLDERS:
        try:
            # Agar destination directory nahi bani hai, toh create karein
            if not os.path.exists(dest_path):
                os.makedirs(dest_path)
                print(f"📁 Created new folder: {dest_path}")

            # Files copy karne ka process
            for file_name in files_to_copy:
                source_file_path = os.path.join(SOURCE_FOLDER, file_name)
                dest_file_path = os.path.join(dest_path, file_name)
                
                # shutil.copy2 use karne se file ka metadata (date, time) bhi maintain rehta hai
                shutil.copy2(source_file_path, dest_file_path)

            print(f"✅ Successfully copied files to: {dest_path}")

        except Exception as e:
            print(f"❌ Error while copying to {dest_path}: {e}")

if __name__ == "__main__":
    copy_cookie_files()