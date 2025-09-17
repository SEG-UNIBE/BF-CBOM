# `BF-CBOM`: Benchmarking Framework for CBOM Generator Tools

*Benchmarking Cryptographic Bill of Material (CBOM) generators end-to: coordinating containerized jobs, normalizing outputs, and scoring results across ecosystems.
In short, your **b**est **f**riend for generating and analyzing CBOMs.*

<div align="center">
  <!-- Replace the src below with your actual logo path if/when available -->
  <img width="50%" src="logo.png" alt="CBOMB logo" />
</div>

</br>

<div align="center">
  <strong>🚀 <a href="#add-additional-workers">Add Workers</a> | 🛠️ <a href="#developer-notes">Developer Notes</a> | 🔍 <a href="#tool-under-scrutinize">Tools</a></strong>
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
</br>

## Setup

### macOS / Linux

1. Install Docker (Desktop or Engine) with Compose v2 enabled and allocate at least 6 GB RAM to the Docker runtime.
2. Install Python 3.12 and `uv` (e.g., `brew install uv` on macOS or `pipx install uv` on Linux). The project expects `uv` on the `PATH`.
3. Clone the repository and change into it:
   ```bash
   git clone https://github.com/SEG-UNIBE/BF-CBOM.git
   cd BF-CBOM
   ```
4. Sync dependencies once for local tooling: `uv sync --frozen --no-dev`.
5. Start the full stack: `make up-all`. Other common targets are `make up-dev` (skip heavy workers) and `make down` to stop services.

### Windows (Git Bash)

1. Install Docker Desktop with the WSL 2 backend and confirm virtualization is enabled in BIOS/UEFI.
2. Install Git for Windows (includes Git Bash) and run all future commands from a Git Bash shell.
3. Install GNU Make (e.g., `choco install make` or the MSYS2 package). Verify `make --version` inside Git Bash.
4. Install Python 3.12 (via the official installer or `winget install Python.Python.3.12`) and add it to `PATH`. Then install `uv` with `pipx install uv` (requires `pipx`, which ships with the Python installer) and ensure `uv --version` succeeds in Git Bash.
5. Clone the repository using Git Bash and enter the folder:
   ```bash
   git clone https://github.com/SEG-UNIBE/BF-CBOM.git
   cd BF-CBOM
   ```
6. Initialize dependencies: `uv sync --frozen --no-dev` (still from Git Bash).
7. Launch services with `make up-all`. If you need a lighter profile, use `make up-dev`. Always invoke `make down` from Git Bash to stop the stack cleanly.

On both platforms the first `make up-…` run can take several minutes while worker images build. Subsequent runs are much faster thanks to caching.

### Disposable Builder Container (optional)

If you want to avoid installing Git, Make, and Python tooling locally, you can run the workflow from inside a purpose-built container that only requires Docker on the host:

1. Build the helper image:
   ```bash
   docker build -f docker/Dockerfile.builder -t bf-cbom/builder .
   ```
2. Run the builder, mounting the Docker socket so it can orchestrate sibling containers. If the repository is private, pass a Git token via `GIT_TOKEN` (or mount the already-cloned repo into `/workspace` instead of cloning inside the container). Replace the clone URL below with the remote you use:
   ```bash
   docker run --rm -it \
     -v /var/run/docker.sock:/var/run/docker.sock \
     --name bf-cbom-builder \
    bf-cbom/builder -lc "\
      git clone --branch dev --single-branch https://github.com/SEG-UNIBE/BF-CBOM.git repo && \
       cd repo && \
       make up-dev \
     "
   ```
3. When you're done, stop everything with `make down` (either within the running builder session or by re-running the container with `make down`).

This approach keeps all build tooling inside an ephemeral container while still using the host's Docker daemon for the heavy lifting.

## Tool under Scrutinize

- [`CBOMKit`](https://github.com/PQCA/cbomkit): Reference backend used here to standardize CBOM requests, normalize outputs, and provide APIs for storage, comparison, and scoring across workers.
- [`cdxgen`](https://github.com/CycloneDX/cdxgen): Open‑source CycloneDX SBOM generator that detects dependencies across many ecosystems (e.g., Node.js, Python, Java, Go, containers) and emits CycloneDX BOMs.
- [`DeepSeek`](https://www.deepseek.com/): LLM‑assisted analysis prototype explored for inferring libraries and cryptographic usage from source and docs; experimental and not a drop‑in SBOM generator.
- [`sbom-tool`](https://github.com/microsoft/sbom-tool): Microsoft’s SBOM CLI that scans build drops or directories and produces SPDX 2.2 SBOMs with provenance metadata, suited for CI and release pipelines.

## Add Additional Workers

Follow these steps to integrate a new CBOM worker efficiently:

1. Copy the `workers/skeleton` directory to `workers/<mytool>` and implement your tool logic in `handle_instruction(instr) -> JobResult`.
2. Create a per-worker environment file `docker/env/<mytool>.env` with relevant secrets and configurations.
3. Clone `docker/Dockerfile.worker-skeleton`. By default, workers inherit from the shared base image via `ARG BASE_IMAGE` and `BASE_TAG`. You can use this common base (recommended) or define your own base image if your tool has special requirements.
4. Add your worker service to `docker-compose.yml`, referencing the corresponding `env_file`.
5. Register your worker in the Makefile by adding its name to the `AVAILABLE_WORKERS` variable at the top of the file. It will then be picked up automatically by the `up-all` and `up-prod` targets.

## Developer Notes

- Formatting/lint: configured via Ruff in `pyproject.toml`. Suggested commands:
  - `uv run ruff format` to format
  - `uv run ruff check --fix` to auto-fix common issues
  
- CLI usage (runs outside Docker once Redis is up):
  - `uv sync --frozen --no-dev`
  - `uv run cli.py --help`
