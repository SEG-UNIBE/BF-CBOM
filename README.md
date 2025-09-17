# `BF-CBOM`: Benchmarking Framework for CBOM Generator Tools

*Benchmarking Cryptographic Bill of Material (CBOM) generators end-to-end: coordinating containerized jobs, normalizing outputs, and scoring results across ecosystems. In short, your **b**est **f**riend for generating and analyzing CBOMs.*

<div align="center">
  <img width="50%" src="logo.png" alt="BF-CBOM logo" />
</div>

</br>

<div align="center">
  <strong>🚀 <a href="#setup">Setup</a> | 🛠️ <a href="#developer-notes">Developer Notes</a> | 🔍 <a href="#tools-under-scrutinize">Tools</a></strong>
</div>

</br>

<div align="center">
  <a href="https://doi.org/10.5281/zenodo.17140610"><img src="https://zenodo.org/badge/1058056469.svg" alt="DOI" /></a>
  <a href="#"><img src="https://img.shields.io/badge/python-v3.12%2B-blue.svg" alt="Python 3.12+" /></a>
  <a href="#"><img src="https://img.shields.io/badge/Docker-Compose-success.svg" alt="Docker Compose" /></a>
  <a href="#"><img src="https://img.shields.io/badge/Streamlit-app-red.svg" alt="Streamlit" /></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-GPL--3.0--only-blue.svg" alt="GPL-3.0-only" /></a>
</div>

</br>

## Introduction

BF-CBOM is a research-grade harness for comparing heterogeneous CBOM generators side-by-side. It orchestrates full container stacks, captures worker outputs, normalizes results, and surfaces scoring dashboards for reviewers.

**Key highlights**
- **Coordinator-first design** – a Streamlit control plane backed by Redis manages benchmark lifecycles and result aggregation.
- **Pluggable workers** – each CBOM generator runs inside its own Docker container, driven by a unified instruction protocol.
- **Native CLI** – a Typer-based CLI scripts benchmarks and exports configs or CBOM bundles for offline analysis.
- **Reproducible envs** – `.env` templates, Docker build recipes, and uv-managed Python tooling keep runs deterministic.

## Setup

BF-CBOM is a multi-container environment (Redis, the coordinator UI, and one container per CBOM generation tool), so Docker must be installed locally.

1. Install **Docker Desktop** using the official guide for [macOS](https://docs.docker.com/desktop/setup/install/mac-install/) or [Windows](https://docs.docker.com/desktop/setup/install/windows-install/).

2. Clone the repository and navigate into it:

    ```bash
    git clone https://github.com/SEG-UNIBE/BF-CBOM.git
    cd BF-CBOM
    ```

3. Prepare the environment files under `docker/env/`. Each service ships with a `*.env.template` describing the secrets it requires. Duplicate every template, drop the `.template` suffix, and keep the resulting `.env` files local (they are git-ignored). After this step the directory should resemble:

    ```text
    ├── docker
    │   └── env
    │       ├── coordinator.env
    │       ├── coordinator.env.template
    │       ├── worker-cbomkit.env
    │       ├── worker-cbomkit.env.template
    │       └── …
    ```

    > [!NOTE]
    > Run `make ensure-env` on macOS/Linux or `pwsh ./scripts/ensure_env.ps1` on Windows to create the `.env` files automatically.

4. Provide credentials. At minimum set `GITHUB_TOKEN` inside `docker/env/coordinator.env`. In case you do not have one already, see [how to create a personal access token (classic)](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-personal-access-token-classic).

From here on, there are two options on how to continue with the setup as described below.

### Option 1 – Disposable Builder Container

Use this when you want to keep tooling off your host.

5. Build the helper image that bundles all required tooling:

    ```bash
    docker build -f docker/Dockerfile.builder -t bf-cbom/builder .
    ```

2. Run the builder container. It clones the repo inside the container, reuses your local `.env` templates, and brings the stack up:

   ```bash
   docker build -f docker/Dockerfile.builder -t bf-cbom/builder . && \
   docker run --rm -it \
      -v /var/run/docker.sock:/var/run/docker.sock \
      -v "$(pwd)/docker/env":/workspace/secrets/env:ro \
      --name bf-cbom-builder \
      bf-cbom/builder -lc "\
        git clone --branch main https://github.com/SEG-UNIBE/BF-CBOM.git repo && \
        cp -vf /workspace/secrets/env/*.env repo/docker/env/ && \
        cd repo && \
        make up-prod \
      "
   ```

3. Exit with `Ctrl+C` when you are done benchmarking. The session is ephemeral; all tooling lives inside the container.

> [!NOTE]
> **Windows** users need backticks for line continuations in PowerShell, so this variant keeps the disposable builder flow copy/paste-friendly.
> ```powershell
> docker build -f docker/Dockerfile.builder -t bf-cbom/builder .
> 
> $pwdPath = (Get-Location).Path
> docker run --rm -it `
>   -v /var/run/docker.sock:/var/run/docker.sock `
>   -v "$pwdPath/docker/env:/workspace/secrets/env:ro" `
>   --name bf-cbom-builder `
>   bf-cbom/builder -lc "git clone --branch main https://github.com/SEG-UNIBE/BF-CBOM.git repo && cp -vf /workspace/secrets/env/*.env repo/docker/env/ && cd repo && make up-prod"
> ```

### Option 2 – Makefile

Bring the stack up on your host using GNU Make and Docker Compose.

#### macOS / Linux

1. Confirm Docker Desktop/Engine (Compose v2) is running on this machine with the resource limits noted above.
2. Ensure Python 3.12 and `uv` are available on `PATH` (e.g., `brew install uv` or `pipx install uv`).
3. Generate `.env` files from templates: `./scripts/ensure_env.sh`.
4. Start the services with the worker profile of choice:
   - `make up-all` – run every worker
   - `make up-dev` – only the lightweight development workers

   These commands attach logs; stop them with `Ctrl+C`.
5. *(Optional)* If you want local CLI usage, sync dependencies once: `uv sync --frozen --no-dev`.

#### Windows

1. Confirm Docker Desktop is running with the WSL 2 backend enabled and the resource limits noted above.
2. Install Git for Windows and always use **Git Bash** (or WSL) for the commands below.
3. Install GNU Make (`choco install make` or via MSYS2) and verify `make --version` inside Git Bash.
4. Install Python 3.12 and `pipx`, then `pipx install uv` so `uv --version` succeeds.
5. From the repo root, create the `.env` files: `pwsh ./scripts/ensure_env.ps1` (PowerShell) **or** `./scripts/ensure_env.sh` (Git Bash/WSL).
6. Launch the stack from Git Bash:
   - `make up-all`
   - `make up-dev`

   Stop the foreground logs with `Ctrl+C`.
7. *(Optional)* Enable the CLI by running `uv sync --frozen --no-dev` inside Git Bash.

## Tools Under Scrutinize

- [`CBOMKit`](https://github.com/PQCA/cbomkit): Backbone service for normalizing requests and scoring responses.
- [`cdxgen`](https://github.com/CycloneDX/cdxgen): Ecosystem-spanning CycloneDX generator (Node.js, Python, Java, Go, containers).
- [`DeepSeek`](https://www.deepseek.com/): LLM-assisted prototype for inferring cryptographic usage from docs/source.
- [`sbom-tool`](https://github.com/microsoft/sbom-tool): Microsoft SPDX 2.2 generator tailored for CI/release pipelines.

## Add Additional Workers

1. Copy `workers/skeleton` to `workers/<mytool>` and implement `handle_instruction`.
2. Create `docker/env/<mytool>.env` with any secrets or configuration knobs.
3. Derive a Dockerfile from `docker/Dockerfile.worker-skeleton` (or roll your own if required).
4. Register the worker in `docker-compose.yml`, referencing the new `env_file`.
5. Add the worker name to `AVAILABLE_WORKERS` in the `Makefile` so standard targets pick it up.

## Developer Notes

- Formatting/lint via Ruff (`pyproject.toml`):
  - `uv run ruff format`
  - `uv run ruff check --fix`
- Handy CLI commands once Redis is running:
  - `uv run misc/cli/cli.py --help`
  - `uv run misc/cli/cli.py export config <BENCH_ID> -o bench.json`
  - `uv run misc/cli/cli.py export cboms <BENCH_ID> --dest ./downloads`
- Environment helpers:
  - macOS/Linux: `./scripts/ensure_env.sh`
  - Windows PowerShell: `pwsh ./scripts/ensure_env.ps1`
