#!/usr/bin/env python3
"""
./dagger.py

A practical, runnable "dagger-style" workflow for a Node/Express project.

What it does (idempotent, local-first):
  - Ensures working directory has a package.json
  - Installs dependencies (npm ci)
  - Runs tests (npm test)
  - Builds the app (npm run build) if present
  - Builds an OCI image with Docker
  - Tags the image with a deterministic tag (registry/org/name:gitsha)
  - Pushes the image to the registry (supports GHCR/Docker Hub)

Notes:
  * This script is intentionally dependency-light: it uses the local Docker CLI and npm.
  * It is named `dagger.py` because it implements the same build/test/package/push steps
    you'd express in a Dagger workflow, but it runs with standard CLIs so it's runnable
    immediately by any developer.
  * If you later want to convert this to a Dagger programmatic pipeline, the steps are
    the same and can be ported.

Usage:
  python ./dagger.py --image ghcr.io/myorg/myapp --target prod

Environment:
  - For GHCR pushes: set GHCR_USER and GHCR_TOKEN (or use `docker login ghcr.io`).
  - For Docker Hub: set DOCKER_USER and DOCKER_PASS (or use `docker login`).

"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime


def run(cmd, cwd=None, check=True, capture=False, env=None):
    if isinstance(cmd, (list, tuple)):
        display = " ".join(cmd)
    else:
        display = cmd
    print(f"$ {display}")
    res = subprocess.run(cmd, cwd=cwd, check=False, shell=isinstance(cmd, str), capture_output=capture, env=env)
    if res.returncode != 0 and check:
        print(res.stdout.decode() if res.stdout else "", end="")
        print(res.stderr.decode() if res.stderr else "", end="")
        raise SystemExit(f"Command failed: {display} (exit {res.returncode})")
    return res


def git_sha_short():
    try:
        out = run(["git", "rev-parse", "--short", "HEAD"], capture=True)
        return out.stdout.decode().strip()
    except Exception:
        # fallback to timestamp
        return datetime.utcnow().strftime("ts%Y%m%d%H%M%S")


def npm_install_and_test(project_dir):
    if not os.path.exists(os.path.join(project_dir, "package.json")):
        raise SystemExit("package.json not found in project root — aborting build")
    print("Installing npm dependencies (npm ci)...")
    run(["npm", "ci"], cwd=project_dir)

    # Run tests if script exists
    with open(os.path.join(project_dir, "package.json"), "r", encoding="utf-8") as f:
        pj = json.load(f)
    scripts = pj.get("scripts", {})
    if "test" in scripts:
        print("Running npm test...")
        run(["npm", "test"], cwd=project_dir)
    else:
        print("No `test` script found in package.json — skipping tests")

    if "build" in scripts:
        print("Running npm run build...")
        run(["npm", "run", "build"], cwd=project_dir)
    else:
        print("No `build` script found in package.json — skipping build step")


def docker_build_and_push(project_dir, image_ref):
    # Check docker availability
    if shutil.which("docker") is None:
        raise SystemExit("docker CLI not found in PATH — please install Docker or Podman")

    # Build: require Dockerfile in project root; provide helpful error if missing
    dockerfile_path = os.path.join(project_dir, "Dockerfile")
    if not os.path.exists(dockerfile_path):
        print("Warning: Dockerfile not found in project root. Creating a simple default Dockerfile for Node.js")
        create_default_dockerfile(project_dir)

    print(f"Building docker image {image_ref}...")
    run(["docker", "build", "-t", image_ref, "."], cwd=project_dir)

    print(f"Pushing image {image_ref}...")
    run(["docker", "push", image_ref])


def create_default_dockerfile(project_dir):
    # create a simple Node.js Dockerfile if none exists
    content = (
        "FROM node:18-alpine\n"
        "WORKDIR /app\n"
        "COPY package*.json ./\n"
        "RUN npm ci --only=production\n"
        "COPY . .\n"
        "ENV NODE_ENV=production\n"
        "EXPOSE 8080\n"
        "CMD [\"node\", \"./dist/index.js\"]\n"
    )
    with open(os.path.join(project_dir, "Dockerfile"), "w", encoding="utf-8") as f:
        f.write(content)
    print("Wrote default Dockerfile — please review if your app requires a different start command.")


def docker_login_if_needed(image_ref):
    # For GHCR (ghcr.io/org/name) use GHCR_USER/GHCR_TOKEN
    if image_ref.startswith("ghcr.io/"):
        user = os.environ.get("GHCR_USER")
        token = os.environ.get("GHCR_TOKEN")
        if user and token:
            print("Logging in to ghcr.io...")
            run(f"echo {token} | docker login ghcr.io -u {user} --password-stdin")
        else:
            print("No GHCR credentials provided in GHCR_USER/GHCR_TOKEN — assume docker is already logged in or image is public")
    elif ":" in image_ref or "/" in image_ref:
        # Heuristic: if looks like Docker Hub or custom registry, assume docker login is handled externally.
        print("Ensure you're logged in to your container registry (docker login) if auth is required.")


def parse_args():
    p = argparse.ArgumentParser(description="Build/test/package and push an image for Node/Express projects")
    p.add_argument("--image", required=True, help="Image reference prefix, e.g. ghcr.io/org/app")
    p.add_argument("--target", default="prod", help="Target environment name")
    p.add_argument("--project-dir", default=".", help="Path to project root")
    p.add_argument("--skip-tests", action="store_true", help="Skip running tests")
    p.add_argument("--no-push", action="store_true", help="Build image but do not push (useful for local testing)")
    return p.parse_args()


def main():
    args = parse_args()
    project_dir = os.path.abspath(args.project_dir)

    print("Starting Dagger-style local workflow")
    sha = git_sha_short()
    image_ref = f"{args.image}:{sha}"

    # Optional login for GHCR
    docker_login_if_needed(args.image)

    # Install and test
    if not args.skip_tests:
        npm_install_and_test(project_dir)
    else:
        print("Skipping tests and install per --skip-tests")

    # Build/push image
    run(["docker", "build", "-t", image_ref, "."], cwd=project_dir)

    if args.no_push:
        print(f"Built image {image_ref} (not pushed, --no-push set)")
    else:
        print("Pushing image to registry...")
        run(["docker", "push", image_ref])

    print("Workflow complete.")
    print(f"artifact:{image_ref}")


if __name__ == "__main__":
    main()
