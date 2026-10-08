# Recipe the hosting service follows to run this app. You never need to edit this.
FROM python:3.11-slim

WORKDIR /app

# Install the Python libraries first so rebuilds are fast when only code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code (data/ is excluded — see .dockerignore)
COPY core/ ./core/
COPY api/ ./api/
COPY web/ ./web/

# The public scoreboard: no password, no optimizer tabs, no ETR files
ENV DFS_MODE=scoreboard
EXPOSE 8080

CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
