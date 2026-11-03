# Production Dockerfile for Render, Railway, Fly.io, or VPS
FROM python:3.11-slim

# Prevent Python from writing pyc files and buffering stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies: FFmpeg and SQLite
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# LOW-3: Create non-root system user and drop root privileges
RUN addgroup --system botgroup && adduser --system --ingroup botgroup botuser \
    && chown -R botuser:botgroup /app

USER botuser

# Start the bot
CMD ["python", "bot.py"]
