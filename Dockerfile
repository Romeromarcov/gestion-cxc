# Stage 1: Build the Vue.js Frontend
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend

# Copy frontend package files
COPY package.json package-lock.json* ./

# Install dependencies
RUN npm ci

# Copy frontend source code
COPY src ./src
COPY public ./public
COPY index.html vite.config.js ./

# Build the frontend
RUN npm run build

# Stage 2: Build the Python Backend
FROM python:3.11-slim AS backend-builder

WORKDIR /app

# Install system dependencies required for Python packages
RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Stage 3: Final Runtime Image
FROM python:3.11-slim

WORKDIR /app

# Create a non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app

# Install runtime dependencies (e.g., postgres client)
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 && rm -rf /var/lib/apt/lists/*

# Copy Python dependencies from builder
COPY --from=backend-builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=backend-builder /usr/local/bin /usr/local/bin

# Copy application code
COPY --chown=appuser:appuser . .

# Copy built frontend assets to the static directory
# Assuming the backend serves static files from /app/static
COPY --from=frontend-builder --chown=appuser:appuser /app/frontend/dist ./static

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]