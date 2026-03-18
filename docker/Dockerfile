FROM python:3.13-slim

WORKDIR /app

# Install uv
RUN pip install --no-cache-dir uv

# Copy dependency files first (for layer caching)
COPY pyproject.toml ./

# Install dependencies (without dev extras)
RUN uv sync --no-dev

# Copy source code
COPY . .

CMD ["uv", "run", "python", "scripts/run_telegram_bot.py"]
