# ---- Build stage (pip install) ----
FROM python:3.12-slim AS builder
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --target=/install -r requirements.txt

# ---- Runtime stage ----
FROM python:3.12-slim AS runner
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    MCP_PORT=5115 \
    MCP_HOST=0.0.0.0

RUN mkdir -p /app/data

COPY --from=builder /install /usr/local/lib/python3.12/site-packages
COPY app ./app
COPY server.py .

EXPOSE 5115

CMD ["python", "server.py"]
