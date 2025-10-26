# Backend.im DevOps Flow — Developer Guide

## Overview

This repository enables a **one-command backend deployment workflow** using open-source tools, Claude Code, and minimal configuration. It connects local code directly to **Backend.im**, where your backend is built, packaged, and deployed automatically.

---

## Architecture Summary

1. **Developer Laptop → Claude Code CLI**
   Developer triggers deployment with a natural-language command like:

   ```bash
   claude code deploy my backend to staging
   ```

2. **Git Operations** — Code changes are staged, committed, and pushed.
3. **Dagger Workflow** — Builds, tests, and packages the app into an artifact (Docker image or ZIP).
4. **Artifact Push** — The artifact is pushed to a registry (e.g., GHCR, Docker Hub, or S3).
5. **Deploy Adapter** — The artifact reference is sent to Backend.im via API, CLI, or git.
6. **Backend.im Deployment** — Backend.im provisions and deploys the backend automatically.
7. **Result Output** — Claude Code returns the live URL and status, optionally opening it in the browser.

---

## Tools Used

- **Git** – Version control and source deployment.
- **Dagger** – Orchestrates build, test, and packaging steps declaratively.
- **Docker** – Containerizes backend apps for consistent deployments.
- **Backend.im API / CLI** – Receives artifact references and handles the final deployment.
- **Claude Code** – Front-end CLI layer that interprets natural-language commands.

---

## Local Setup

```bash
# Clone your repo
 git clone <repo-url>
 cd <repo>

# Install dependencies (example)
 pip install dagger-io

# Make scripts executable
 chmod +x ./deploy.sh

# Test the build flow
 ./dagger.py

# Deploy manually (for testing)
 ./deploy.sh --remote git@backend.im:<user>/<app>.git
```

---

## Deployment Flow (CLI-Driven)

1. Claude Code executes `./deploy.sh` behind the scenes.
2. The script builds the app (via `./dagger.py`).
3. Artifacts are uploaded, and a deployment is triggered through `./deploy-adapter-git.py`.
4. Backend.im deploys and returns a status/URL.
5. Claude Code prints the output, optionally opening the live URL.

---

## Minimal Custom Code

- Modify `./dagger.py` to match your app’s build steps.
- Optionally extend `./deploy-adapter-git.py` to handle different deployment APIs or modes.

---

## Example CLI Flow

```bash
# Developer command
claude code deploy

# Under the hood
- git add . && git commit -m 'auto-deploy'
- git push origin main
- dagger run build/test/package
- push artifact to GHCR/S3
- trigger deploy via Backend.im API
- return live URL
```

---

## Outcome

Developers can move from **local code → live deployment** with a single command, using entirely open-source and lightweight tooling. The system can be extended easily for staging, production, or multi-region deployments.

---

**Maintainer:** DevOps Research Team – Backend.im
