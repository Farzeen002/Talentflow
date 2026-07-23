FROM python:3.11-slim

WORKDIR /app

# Install system deps (optional but useful)
RUN apt-get update && apt-get install -y gcc

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Combined API + RQ workers for free-tier single-process hosting (Render).
RUN chmod +x /app/start_render.sh

CMD ["/app/start_render.sh"]