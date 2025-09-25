# `BF-CBOM`: Benchmarking Framework for CBOM Generator Tools

*Benchmarking Cryptography Bill of Material (CBOM) generators end-to-end: coordinating containerized jobs, normalizing outputs, and scoring results across ecosystems. In short, your **b**est **f**riend for generating and analyzing CBOMs.*

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
  <a href="https://github.com/SEG-UNIBE/BF-CBOM/releases"><img src="https://img.shields.io/github/v/release/SEG-UNIBE/BF-CBOM?include_prereleases" alt="Changelog" /></a>
  </br>
  <a href="#"><img src="https://img.shields.io/badge/python-v3.12%2B-blue.svg" alt="Python 3.12+" /></a>
  <a href="#"><img src="https://img.shields.io/badge/Docker-Compose-success.svg" alt="Docker Compose" /></a>
  <a href="#"><img src="https://img.shields.io/badge/Streamlit-app-red.svg" alt="Streamlit" /></a>
  <a href="./LICENSE"><img src="https://img.shields.io/badge/License-GPL--3.0--only-blue.svg" alt="GPL-3.0-only" /></a>
</div>

</br>


https://github.com/user-attachments/assets/9926c193-6b1f-4e1e-9556-95ebcc3e0401



## Introduction

BF-CBOM is a research-grade harness for comparing heterogeneous CBOM generators side-by-side.
It orchestrates full container stacks, captures worker outputs, normalizes results, and surfaces scoring dashboards for reviewers.

### Key Features

- **Coordinator-first control plane:** a Streamlit UI backed by Redis schedules benchmarks, tracks job state, and surfaces results for reviewers.
- **Containerised worker plugins:** every CBOM generator runs in its own Docker image but speaks the same Redis-driven instruction protocol.
- **Scriptable CLI:** a Typer-based interface can launch benchmarks, export configs, or bundle CBOM artefacts for offline analysis.
- **Reproducible runs:** checked-in `.env` templates, Dockerfiles, and `uv`-managed Python dependencies keep environments consistent across machines.

### Tools Under Scrutinize

- [`CBOMKit`](https://github.com/PQCA/cbomkit): PQCA's reference backend that normalises requests, aggregates worker responses, and produces scored CBOM comparisons.
- [`cdxgen`](https://github.com/CycloneDX/cdxgen): Open-source CycloneDX generator maintained by the OWASP CycloneDX project (OWASP Foundation); supports Node.js, Python, Java, Go, container images, and more.
- [`DeepSeek`](https://www.deepseek.com/): Research prototype from DeepSeek Inc. (China) exploring large language models to infer cryptographic usage from documentation and source code.
- [`sbom-tool`](https://github.com/microsoft/sbom-tool): Microsoft's official SPDX 2.2 generator designed for CI/CD pipelines and Azure DevOps release processes.

## Setup

**🚩 1. Docker**

BF-CBOM is a multi-container environment (Redis, the coordinator UI, and one container per CBOM generation tool), so Docker must be installed locally.
Install **Docker Desktop** using the official guide for [macOS](https://docs.docker.com/desktop/setup/install/mac-install/) or [Windows](https://docs.docker.com/desktop/setup/install/windows-install/).

After installation, open a new terminal and run the following command to confirm that Docker and Docker Compose are available.
If everything is set up correctly, you will see two version strings.

```bash
docker --version && docker compose version
```

**🚩 2. This Repo**

Clone the repository and navigate into it:

```bash
git clone https://github.com/SEG-UNIBE/BF-CBOM.git
cd BF-CBOM
```

**🚩 3. Environment Variables**

Prepare the environment files under `docker/env/`. Each service ships with a `*.env.template` describing the secrets it requires. Duplicate every template, drop the `.template` suffix, and keep the resulting `.env` files local (they are git-ignored). After this step the directory should resemble:

```text
├── docker
│   └── env
│       ├── coordinator.env
│       ├── coordinator.env.template
│       ├── worker-cbomkit.env
│       ├── worker-cbomkit.env.template
│       └── ...  (and so on)
```

> [!NOTE]
> Run `make ensure-env` on macOS/Linux or `pwsh ./scripts/ensure_env.ps1` on Windows to create the `.env` files automatically.

At minimum set `GITHUB_TOKEN` inside `docker/env/coordinator.env`. In case you do not have one already, see [how to create a personal access token (classic)](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#creating-a-personal-access-token-classic).

From here on, there are two options on how to continue with the setup as described below.

### Option 1 – Disposable Builder Container

Use this when you want to keep tooling off your host.

**🚩 4. Build the Builder Container**

Build the helper image that bundles all required tooling:

```bash
docker build -f docker/Dockerfile.builder -t bf-cbom/builder .
```

**🚩 5. Run the Builder Container**

Run the builder container. It clones the repo inside the container, reuses your local `.env` templates, and brings the stack up:

```bash
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

Exit with `Ctrl+C` when you are done benchmarking.
The session is ephemeral; all tooling lives inside the container.

> [!NOTE]
> **Windows** users need backticks (`) for line continuations in PowerShell, so the snippet below keeps the disposable builder flow copy/paste-friendly:
> 
> ```powershell
> $pwdPath = (Get-Location).Path
> docker run --rm -it `
>   -v /var/run/docker.sock:/var/run/docker.sock `
>   -v "$pwdPath/docker/env:/workspace/secrets/env:ro" `
>   --name bf-cbom-builder `
>   bf-cbom/builder -lc "git clone --branch main https://github.com/SEG-UNIBE/BF-CBOM.git repo && cp -vf /workspace/secrets/env/*.env repo/docker/env/ && cd repo && make up-prod"
> ```

### Option 2 – Makefile

In this option, you will build and compose the docker compose environment directly using your host machine.

#### MacOS / Linux


**🚩 4. Build using Makefile**

The project uses [GNU Make](https://www.gnu.org/software/make/) to simplify container orchestration.
Most Linux distributions include `make` by default.
On macOS, it comes with the *Xcode command line tools*, which you can install by running `xcode-select --install` in a terminal if not already present.

From the repository's root folder, start the full stack of services with:

```bash
make up-prod
```

Docker Compose will launch and manage all containers. Stop the stack anytime with `Ctrl+C`.

**🚩 5. Local CLI (optional)**

If `make up-prod` completed successfully and the containers are running, you can also interact with BF-CBOM through its command-line interface (CLI).
From the repository's root folder, run:

```bash
uv run misc/cli/cli.py
```

#### Windows

**🚩 4. Install Git Bash**

Install [Git for Windows](https://git-scm.com/downloads/win) and use Git Bash for all setup commands. It provides the Unix-compatible environment expected by the scripts.

**🚩 5. Install GNU Make**

Install GNU Make and verify `make --version` in Git Bash.

> [!NOTE]
> Package managers like [Chocolatey](https://chocolatey.org/) (`choco install make`) or [MSYS2](https://www.msys2.org/) (`pacman -S make`) simplify this step.


**🚩 6. Launch the Stack**

From the repository root (inside Git Bash), start the environment:

```bash
make up-prod
```

Stop anytime with `Ctrl+C`.

### Local CLI (optional)

If `make up-prod` completed successfully and the containers are running, you can also interact with BF-CBOM through its command-line interface (CLI) in addition to the GUI.
Because the CLI runs locally, a minimal Python setup is required before invoking it.

**🎏 1. Python and `uv`**

Install Python 3.12 and `uv`, the dependency manager used by BF-CBOM.

- For Python, download at least the version 3.12 from the [official site](https://www.python.org/downloads/).
- To install `uv`, follow the `curl` instructions on [uv's webpage](https://docs.astral.sh/uv/#installation).

> [!NOTE]
> Using a package manager is often easiest: `brew install python@3.12 uv` on macOS, or `sudo apt-get install python3.12 uv` on Linux.

**🎏 2. Launch CLI**

From the repository's root folder, run:

```bash
uv run misc/cli/cli.py
```

The command prints the CLI's commands and options in the terminal.

## Developer Notes

### Adding Additional Workers

1. Duplicate `workers/skeleton` to `workers/<mytool>` and implement `handle_instruction`.
2. Create `docker/env/<mytool>.env` for credentials and configuration specific to the worker.
3. Start from `docker/Dockerfile.worker-skeleton` (or craft a bespoke Dockerfile if required).
4. Register the worker service in `docker-compose.yml`, pointing at the new `env_file`.
5. Append the worker name to the `AVAILABLE_WORKERS` list in the `Makefile` so shared targets pick it up.

### Formatting and Linting

Ruff is configured in `pyproject.toml` for both formatting and linting:

- `uv run ruff format`
- `uv run ruff check --fix`
