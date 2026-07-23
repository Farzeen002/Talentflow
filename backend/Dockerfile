FROM python:3.11-slim

WORKDIR /app

# gcc for some Python wheels; redis-server for free-tier single-container RQ.
RUN apt-get update && apt-get install -y --no-install-recommends gcc redis-server \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Combined Redis + RQ workers + API for free-tier single-process hosting (Render).
RUN chmod +x /app/start_render.sh \
    && mkdir -p /var/run/redis /var/lib/redis

CMD ["/app/start_render.sh"]
