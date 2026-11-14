"""Filesystem helpers for cleanup and screenshot management."""

import os
import shutil
import stat
import time


def remove_readonly(func, path, _exc_info):
    """Handle read-only and locked files during deletion."""
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass

    for _attempt in range(5):
        try:
            func(path)
            return
        except OSError:
            time.sleep(1)

    print(f"[WARNING] Could not release file, skipping: {path}", flush=True)


def clean_temp_dir(path: str) -> bool:
    """Delete a temporary directory and return whether it is gone."""
    if not os.path.exists(path):
        return True

    for _attempt in range(3):
        shutil.rmtree(path, onerror=remove_readonly)
        if not os.path.exists(path):
            return True
        time.sleep(1)

    return not os.path.exists(path)


def reset_directory(path: str) -> None:
    """Delete a directory and recreate it empty."""
    if os.path.exists(path):
        shutil.rmtree(path, onerror=remove_readonly)
    os.makedirs(path, exist_ok=True)


def clear_png_screenshots(directory: str) -> None:
    """Delete all PNG files in a directory without touching other files."""
    if not os.path.exists(directory):
        return

    for file_name in os.listdir(directory):
        if not file_name.lower().endswith(".png"):
            continue
        file_path = os.path.join(directory, file_name)
        if not os.path.isfile(file_path):
            continue
        try:
            os.remove(file_path)
            print(f"[INFO] Deleted old screenshot: {file_path}", flush=True)
        except OSError as error:
            print(f"[WARNING] Could not delete {file_path}: {error}", flush=True)


def remove_file(path: str) -> None:
    """Remove a file if it exists."""
    if not os.path.exists(path):
        return
    try:
        os.remove(path)
    except OSError as error:
        print(f"[WARNING] Could not delete file {path}: {error}", flush=True)