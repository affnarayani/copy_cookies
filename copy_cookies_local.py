"""Copy cookie files to multiple local destination folders."""

import os
import shutil

from common.config import ROOT_DIR, get_config, resolve_path
from common.file_utils import remove_readonly, reset_directory as empty_destination_folder


def copy_cookie_files():
    config = get_config()
    source_folder = resolve_path(config["paths"]["output_dir"])
    destinations = config["destinations"]

    if not os.path.exists(source_folder):
        print(f"[ERROR] Source folder '{source_folder}' not found. Check the path.", flush=True)
        return

    files_to_copy = [
        file_name
        for file_name in os.listdir(source_folder)
        if os.path.isfile(os.path.join(source_folder, file_name))
    ]

    if not files_to_copy:
        print(f"[WARNING] Source folder '{source_folder}' contains no files.", flush=True)
        return

    print(f"Total {len(files_to_copy)} files ready to copy...\n", flush=True)

    for destination in destinations:
        dest_path = destination["local_path"]
        try:
            was_existing = os.path.exists(dest_path)
            empty_destination_folder(dest_path)
            if was_existing:
                print(f"[INFO] Emptied old folder: {dest_path}", flush=True)
            else:
                print(f"[INFO] Created new folder: {dest_path}", flush=True)

            for file_name in files_to_copy:
                source_file = os.path.join(source_folder, file_name)
                dest_file = os.path.join(dest_path, file_name)
                shutil.copy2(source_file, dest_file)

            print(f"[OK] Successfully copied files to: {dest_path}", flush=True)

        except Exception as error:
            print(f"[ERROR] Error while copying to {dest_path}: {error}", flush=True)


if __name__ == "__main__":
    copy_cookie_files()