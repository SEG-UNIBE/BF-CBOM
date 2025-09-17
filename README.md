# `BF-CBOM`: Benchmarking Framework for Cryptographic Bill of Materials

> Benchmarking Cryptographic Bill of Material (CBOM) generators end-to: coordinating containerized jobs, normalizing outputs, and scoring results across ecosystems.
> In short, your **b**est **f**riend for generating and analyzing CBOMs.

[![DOI](https://zenodo.org/badge/1058056469.svg)](https://doi.org/10.5281/zenodo.17140610)
[![Python](https://img.shields.io/badge/python-v3.12%2B-blue.svg)](#)
[![Docker Compose](https://img.shields.io/badge/Docker-Compose-success.svg)](#)
[![Streamlit](https://img.shields.io/badge/Streamlit-app-red.svg)](#)
[![License](https://img.shields.io/badge/License-GPL--3.0--only-blue.svg)](./LICENSE)

<div align="center">
  <!-- Replace the src below with your actual logo path if/when available -->
  <img width="50%" src="logo.png" alt="CBOMB logo" />
</div>

<div align="center">
  <strong>🚀 <a href="#add-additional-workers">Add Workers</a> | 🛠️ <a href="#developer-notes">Developer Notes</a> | 🔍 <a href="#tool-under-scrutinize">Tools</a></strong>
</div>

</br>

---

- **BF-CBOM**: Benchmarking Framework for Cryptographic Bill of Materials *(your **b**est **f**riend for CBOMs)*
- **BF-CBOM**: Benchmarking Cryptographic Bills of Materials
- **CANDi** CBOM Analysis & Normalization for Differential Insights
- **CRISP** CBOM Result Inspection & Speed Profiling

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
