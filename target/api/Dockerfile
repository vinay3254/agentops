FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends procps && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common.py .
COPY api/ api/
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8001"]
