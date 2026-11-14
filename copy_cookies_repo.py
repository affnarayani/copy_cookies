"""Push cookie files to multiple GitHub repositories."""

import gc
import os
import shutil
import sys
import tempfile
import time

import requests
from git import Repo

from common.config import ROOT_DIR, get_config, resolve_path
from common.file_utils import clean_temp_dir, remove_readonly
from common.upload_utils import upload_to_onlyfiles

# Git: disable interactive credential prompts so the script never hangs
os.environ["GIT_TERMINAL_PROMPT"] = "0"
os.environ["GCM_INTERACTIVE"] = "never"

# Git: abort slow or stalled network operations
os.environ["GIT_HTTP_LOW_SPEED_LIMIT"] = "1000"
os.environ["GIT_HTTP_LOW_SPEED_TIME"] = "60"


def upload_error_screenshot(config):
    """Upload error_screenshot.png to OnlyFiles if it exists."""
    screenshot_path = resolve_path(config["paths"]["error_screenshot"])
    if not os.path.exists(screenshot_path):
        print("[INFO] No error_screenshot.png found to upload.", flush=True)
        return

    try:
        upload_to_onlyfiles(screenshot_path, config)
    except Exception as error:
        print(f"[WARNING] Could not upload screenshot: {error}", flush=True)


def main():
    config = get_config()
    git_config = config["git"]
    destinations = config["destinations"]

    pat_token = os.getenv("PAT_TOKEN_ALL")
    if not pat_token:
        raise ValueError("[ERROR] .env file is missing 'PAT_TOKEN_ALL'")

    source_folder = resolve_path(config["paths"]["output_dir"])
    if not os.path.exists(source_folder):
        raise FileNotFoundError(
            f"[ERROR] Local folder '{source_folder}' not found. Run the script from the correct location."
        )

    temp_dir_prefix = git_config["temp_dir_prefix"]
    any_failure = False

    # Clean up any leftover temp folders from previous crashed runs
    for entry in os.listdir("."):
        if entry.startswith(temp_dir_prefix):
            clean_temp_dir(os.path.join(".", entry))

    for dest in destinations:
        repo_owner = config["repo_owner"]
        repo_name = dest["name"]
        dest_folder_name = dest["repo_folder"]

        dest_repo_url = f"https://{pat_token}@github.com/{repo_owner}/{repo_name}.git"

        print("\n" + "=" * 50, flush=True)
        print(f"[INFO] Starting sync for: {repo_name}...", flush=True)
        print("=" * 50, flush=True)

        dest_repo = None
        origin = None
        temp_dir = None

        try:
            temp_dir = tempfile.mkdtemp(prefix=f"{temp_dir_prefix}{repo_name}_", dir=".")
            print(f"[INFO] Created fresh temp folder: {temp_dir}", flush=True)

            # Clone the repository with shallow depth
            print(f"Cloning {repo_name}...", flush=True)
            dest_repo = Repo.clone_from(
                dest_repo_url,
                temp_dir,
                multi_options=[
                    "--depth",
                    str(git_config["clone_depth"]),
                    "--single-branch",
                    "--no-tags",
                ],
            )

            target_path = os.path.join(temp_dir, dest_folder_name)

            # Empty the destination folder before copying
            if os.path.exists(target_path):
                shutil.rmtree(target_path, onerror=remove_readonly)
                print(f"[INFO] Emptied old folder: {target_path}", flush=True)

            # Copy the cookie files
            print(f"Copying '{config['paths']['output_dir']}' contents to '{dest_folder_name}'...", flush=True)
            shutil.copytree(source_folder, target_path)

            # Push changes
            print("Pushing changes to GitHub...", flush=True)
            dest_repo.git.add(A=True)

            if dest_repo.is_dirty():
                dest_repo.index.commit(git_config["commit_message"])
                origin = dest_repo.remote(name="origin")
                origin.push()
                print(f"[OK] Success! Cookies copied and pushed to {repo_name}.", flush=True)
            else:
                print(f"Silent Sync: '{repo_name}' already has the same content.", flush=True)

        except Exception as error:
            print(f"[ERROR] Error while processing {repo_name}: {error}", flush=True)
            upload_error_screenshot(config)
            any_failure = True

        finally:
            # Release GitPython references so the temp folder can be deleted on Windows
            origin = None
            if dest_repo is not None:
                try:
                    dest_repo.close()
                except Exception:
                    pass
            dest_repo = None
            gc.collect()

            if temp_dir and os.path.exists(temp_dir):
                print(f"Cleaning up temporary workspace for {repo_name}...", flush=True)
                if clean_temp_dir(temp_dir):
                    print("Workspace cleaned successfully!", flush=True)
                else:
                    print(f"[WARNING] Temporary folder still exists: {temp_dir}", flush=True)

    if any_failure:
        print("\n[ERROR] Some repositories failed to sync. Exiting with failure.", flush=True)
        sys.exit(1)
    else:
        print("\n[DONE] All repository sync processes finished successfully!", flush=True)


if __name__ == "__main__":
    main()