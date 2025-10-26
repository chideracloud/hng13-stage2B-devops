#!/usr/bin/env python3
"""
./deploy-adapter-git.py

Git-based deploy adapter for Backend.im (and similar PaaS that accept `git push`).

Purpose:
- Push application source (or prepared build artifacts) to a remote git endpoint that triggers Backend.im's deploy.
- Support two modes:
  1) Direct push of the **current repo HEAD** to a target remote/branch: fast and simple.
  2) Clone remote into a tempdir, copy prepared build output (artifact_dir) into it, commit, and push: useful when you want to push build artifacts instead of source.

Features:
- Optional SSH key support via env var `GIT_SSH_KEY` (base64 or raw), or use existing `ssh-agent`/`~/.ssh` config.
- Optional polling of a status endpoint (`--status-url`) that Backend.im may provide to report deploy status. Polling uses `BACKEND_IM_TOKEN` for auth if provided.
- Safe checks: refuses to push if working tree has uncommitted changes unless `--allow-dirty` is set.

Usage examples:

# simple: push current HEAD to remote branch `main`
python ./deploy-adapter-git.py --remote git@backend.im:myorg/myapp.git --branch main

# push build output from ./dist (artifact_dir) into a temporary clone and push
python ./deploy-adapter-git.py --remote git@backend.im:myorg/myapp.git --branch main --artifact-dir ./dist

# use GIT_SSH_KEY (base64) to authenticate
GIT_SSH_KEY_BASE64=$(base64 -w0 ~/.ssh/id_rsa) python ./deploy-adapter-git.py --remote git@backend.im:myorg/myapp.git --branch main

# poll status URL after push (Backend.im returns JSON {status: "pending"|"done"|"failed"})
BACKEND_IM_TOKEN=xxx python ./deploy-adapter-git.py --remote git@backend.im:myorg/myapp.git --branch main --status-url https://api.backend.im/v1/deploys/123

"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import requests


def run(cmd, cwd=None, check=True, capture=False, env=None):
    if isinstance(cmd, (list, tuple)):
        display = " ".join(cmd)
    else:
        display = cmd
    print(f"$ {display}")
    res = subprocess.run(cmd, cwd=cwd, shell=isinstance(cmd, str), capture_output=capture, env=env)
    if res.returncode != 0 and check:
        stdout = res.stdout.decode() if res.stdout else ""
        stderr = res.stderr.decode() if res.stderr else ""
        print(stdout, end="")
        print(stderr, end="")
        raise SystemExit(f"Command failed: {display} (exit {res.returncode})")
    return res


def ensure_clean_worktree(allow_dirty: bool):
    res = run(["git", "status", "--porcelain"], capture=True)
    dirty = res.stdout.decode().strip()
    if dirty and not allow_dirty:
        print("Working tree has uncommitted changes:\n", dirty)
        raise SystemExit("Refusing to push with a dirty working tree. Commit or use --allow-dirty to override.")
    elif dirty and allow_dirty:
        print("Warning: working tree is dirty but --allow-dirty set — proceeding.")


def write_ssh_key_if_provided(key_b64_or_raw: Optional[str]) -> Optional[str]:
    """Write SSH key to a temp file and return path, or None if not provided."""
    if not key_b64_or_raw:
        return None
    # detect base64 (heuristic: contains whitespace? if not, try decode)
    candidate = key_b64_or_raw.strip()
    key_bytes = None
    try:
        # try base64 decode
        key_bytes = base64.b64decode(candidate)
        # simple sanity check
        if b"BEGIN" not in key_bytes[:50]:
            # maybe it was raw not base64 - treat as raw
            key_bytes = candidate.encode()
    except Exception:
        key_bytes = candidate.encode()

    fd, path = tempfile.mkstemp(prefix="git_deploy_key_", text=False)
    os.write(fd, key_bytes)
    os.close(fd)
    os.chmod(path, 0o600)
    print(f"Wrote temporary SSH key to {path}")
    return path


def git_push_current_head(remote_url: str, branch: str, ssh_key_path: Optional[str], force: bool):
    env = os.environ.copy()
    gsc = None
    if ssh_key_path:
        # Use GIT_SSH_COMMAND to point git to our key
        gsc = f"ssh -i {ssh_key_path} -o StrictHostKeyChecking=no"
        env["GIT_SSH_COMMAND"] = gsc
        print(f"Using GIT_SSH_COMMAND={gsc}")

    # add remote under temporary name and push HEAD:branch
    remote_name = "backend_im_deploy"
    # remove if exists
    run(["git", "remote", "remove", remote_name], check=False)
    run(["git", "remote", "add", remote_name, remote_url], env=env)

    push_ref = f"HEAD:refs/heads/{branch}"
    push_cmd = ["git", "push", remote_name, push_ref]
    if force:
        push_cmd.insert(2, "--force")
    run(push_cmd, env=env)

    # cleanup remote
    run(["git", "remote", "remove", remote_name], check=False)


def git_push_artifact_dir(remote_url: str, branch: str, artifact_dir: str, ssh_key_path: Optional[str], commit_msg: str, force: bool):
    env = os.environ.copy()
    if ssh_key_path:
        env["GIT_SSH_COMMAND"] = f"ssh -i {ssh_key_path} -o StrictHostKeyChecking=no"
        print(f"Using GIT_SSH_COMMAND={env['GIT_SSH_COMMAND']}")

    tmp = tempfile.mkdtemp(prefix="deploy_clone_")
    print(f"Cloning remote into tempdir {tmp} ...")
    try:
        run(["git", "clone", remote_url, tmp], env=env)
        # checkout/create branch
        run(["git", "checkout", "-B", branch], cwd=tmp, env=env)
        # clean working dir except .git
        for p in Path(tmp).iterdir():
            if p.name == ".git":
                continue
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
        # copy artifact_dir contents into tmp
        src = Path(artifact_dir)
        if not src.exists():
            raise SystemExit(f"artifact_dir {artifact_dir} does not exist")
        print(f"Copying artifact from {artifact_dir} to temp repo...")
        for item in src.iterdir():
            dest = Path(tmp) / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
        # commit and push
        run(["git", "add", "-A"], cwd=tmp, env=env)
        run(["git", "commit", "-m", commit_msg], cwd=tmp, check=False, env=env)
        push_cmd = ["git", "push", "origin", f"HEAD:refs/heads/{branch}"]
        if force:
            push_cmd.insert(2, "--force")
        run(push_cmd, cwd=tmp, env=env)
    finally:
        print("Cleaning up tempdir")
        shutil.rmtree(tmp, ignore_errors=True)


def poll_status_url(status_url: str, token: Optional[str], interval: int = 3, timeout: int = 300):
    print(f"Polling status URL: {status_url} (timeout {timeout}s)")
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            r = requests.get(status_url, headers=headers, timeout=10)
            r.raise_for_status()
            j = r.json()
            status = j.get("status") or j.get("state") or j.get("deploy_status") or None
            print("status ->", status, "payload ->", json.dumps(j))
            if status and status.lower() in ("done", "success", "ready"):
                print("Deployment finished successfully")
                return j
            if status and status.lower() in ("failed", "error"):
                raise SystemExit(f"Deployment failed: {j}")
            last = j
        except requests.RequestException as e:
            print("Status poll request error:", e)
        time.sleep(interval)
    print("Status poll timed out; last response:", last)
    return last


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--remote", required=True, help="Git remote URL for Backend.im (SSH or HTTPS)")
    p.add_argument("--branch", default="main", help="Target branch on remote to push to")
    p.add_argument("--artifact-dir", help="If set, clone remote, replace contents with this dir, commit and push")
    p.add_argument("--commit-msg", default=None, help="Commit message when pushing artifact-dir (default: deploy: <timestamp>)")
    p.add_argument("--allow-dirty", action="store_true", help="Allow pushing when working tree is dirty")
    p.add_argument("--force", action="store_true", help="Force push to remote branch")
    p.add_argument("--status-url", help="Optional status URL to poll after push (Backend.im) — expects JSON with a `status` field")
    p.add_argument("--status-timeout", type=int, default=300, help="Max seconds to poll status URL")
    p.add_argument("--skip-poll", action="store_true", help="Do not poll status URL even if provided")
    return p.parse_args()


def main():
    args = parse_args()

    git_ssh_key = os.environ.get("GIT_SSH_KEY") or os.environ.get("GIT_SSH_KEY_BASE64")
    ssh_key_path = None
    try:
        if git_ssh_key:
            ssh_key_path = write_ssh_key_if_provided(git_ssh_key)

        # If artifact-dir not set: push current HEAD
        if not args.artifact_dir:
            ensure_clean_worktree(args.allow_dirty)
            print(f"Pushing current HEAD to {args.remote} branch {args.branch}")
            git_push_current_head(args.remote, args.branch, ssh_key_path, args.force)
        else:
            commit_msg = args.commit_msg or f"deploy: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}"
            git_push_artifact_dir(args.remote, args.branch, args.artifact_dir, ssh_key_path, commit_msg, args.force)

        print(f"Push to {args.remote} branch {args.branch} complete.")

        if args.status_url and not args.skip_poll:
            token = os.environ.get("BACKEND_IM_TOKEN")
            poll_status_url(args.status_url, token, timeout=args.status_timeout)
        else:
            if args.status_url:
                print("Status URL provided but polling skipped (--skip-poll set)")
            print("Done — check Backend.im dashboard or logs for deployment status if available.")

    finally:
        # cleanup temporary SSH key
        if ssh_key_path and os.path.exists(ssh_key_path):
            try:
                os.remove(ssh_key_path)
            except Exception:
                pass


if __name__ == '__main__':
    main()
