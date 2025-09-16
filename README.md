# BF-CBOM
Benchmarking Framework for Cryptographic Bill of Materials

This repository contains a multi-container Docker system for the Benchmarking Framework for Cryptographic Bill of Materials (BF-CBOM).

## Quick Start

### Prerequisites
- Docker and Docker Compose
- UV package manager (for local development)
- Make (optional, for convenience commands)

### Development Setup

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd BF-CBOM
   ```

2. **Install dependencies locally (optional)**
   ```bash
   make install
   # or
   uv sync --all-extras
   ```

3. **Start the development environment**
   ```bash
   make dev-workflow
   # or
   docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
   ```

4. **Check service status**
   ```bash
   make status
   # or
   docker compose ps
   ```

### Available Services

- **API Service**: http://localhost:8000 - Main FastAPI application
- **Grafana**: http://localhost:3000 - Monitoring dashboard (admin/admin)
- **Prometheus**: http://localhost:9090 - Metrics collection
- **Database**: PostgreSQL on port 5432
- **Redis**: Redis on port 6379

### Common Commands

```bash
# Show all available commands
make help

# Start all services
make up

# Build and start services
make up-build

# View logs
make logs
make logs-api
make logs-worker

# Run tests
make test

# Lint code
make lint

# Format code
make format

# Stop services
make down

# Clean up all resources
make clean-all
```

### CLI Usage

The BF-CBOM CLI can be used for standalone analysis:

```bash
# Analyze a project
uv run bf-cbom analyze --input /path/to/project --output results.json

# Run benchmarks
uv run bf-cbom benchmark

# Show help
uv run bf-cbom --help
```

### API Usage

Once the API service is running, you can:

1. **Check health**: `curl http://localhost:8000/health`
2. **View API docs**: http://localhost:8000/docs
3. **Analyze a project**: 
   ```bash
   curl -X POST "http://localhost:8000/analyze" \
        -H "Content-Type: application/json" \
        -d '{"project_url": "https://github.com/example/repo"}'
   ```

### Development

- Source code is in `src/bf_cbom/`
- Tests are in `tests/`
- Docker configuration in `docker-compose.yml` and `docker-compose.dev.yml`
- Build automation in `Makefile`

### Architecture

- **API Service**: FastAPI-based REST API
- **Worker Service**: Background task processing
- **Database**: PostgreSQL for persistent data
- **Cache**: Redis for caching and task queues
- **Proxy**: Nginx reverse proxy
- **Monitoring**: Prometheus + Grafana

### Environment Variables

Key environment variables for configuration:

- `DATABASE_URL`: PostgreSQL connection string
- `REDIS_URL`: Redis connection string  
- `ENV`: Environment (development/production)
- `DEBUG`: Enable debug mode

See `docker-compose.yml` for full configuration.
