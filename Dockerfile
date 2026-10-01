# 1) 프론트 빌드
FROM node:24-bookworm-slim AS web
WORKDIR /web
COPY web/package.json web/.npmrc ./
RUN npm install
COPY web/ ./
RUN npm run build

# 2) Python 런타임 (FastAPI + 빌드된 SPA, 단일 포트)
FROM python:3.12-slim AS app
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
COPY --from=web /web/dist ./web/dist
RUN python -c "import api" && echo "BUILD OK"
# 스냅샷은 GCS 볼륨(/mnt/cache)에 — 서비스와 새로고침 Job 이 같은 버킷을 본다
ENV PORT=8080 CACHE_DIR=/mnt/cache ALLOWED_DOMAIN=musinsa.com
EXPOSE 8080
CMD ["python", "serve.py"]
