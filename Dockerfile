FROM python:3.12-slim

# uv（來自官方映像）
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

# LightGBM 需要 OpenMP runtime；python:3.12-slim 沒有帶。少了它 `import lightgbm`
# 會炸在 libgomp.so.1，等於容器內完全不能推論——每日 pipeline 的 ML 步驟會永遠失敗。
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 先裝相依（Docker 層快取：pyproject/lock 不變則不重裝）
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --group api --group serving --no-install-project

# 再裝專案本體
COPY src ./src
COPY configs ./configs
RUN uv sync --frozen --no-dev --group api --group serving

# 資料湖掛在 volume（見 docker-compose）
VOLUME ["/app/data"]

ENTRYPOINT ["python", "-m", "twquant"]
CMD ["--help"]
