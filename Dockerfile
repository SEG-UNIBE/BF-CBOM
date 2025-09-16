# Simple Dockerfile for BF-CBOM
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_TRUSTED_HOST=pypi.org \
    PIP_TRUSTED_HOST=pypi.python.org \
    PIP_TRUSTED_HOST=files.pythonhosted.org

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home --shell /bin/bash app

# Set working directory
WORKDIR /app

# Copy source code and config
COPY src ./src
COPY pyproject.toml ./

# Install the project and dependencies
RUN pip install --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host files.pythonhosted.org -e .

# Switch to non-root user
USER app

# Expose port
EXPOSE 8000

# Default command
CMD ["python", "-m", "bf_cbom.server"]