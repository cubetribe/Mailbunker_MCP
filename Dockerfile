FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Copy project files
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install dependencies
RUN uv pip install --system -e .

# Create volume mount points
RUN mkdir -p /app/data /app/obsidian_vault

ENV STORAGE_PATH=/app/data
ENV PYTHONUNBUFFERED=1

VOLUME ["/app/data", "/app/obsidian_vault"]

# Default entrypoint starts the Mailbunker background push daemon
ENTRYPOINT ["mailbunker"]
CMD ["start"]
