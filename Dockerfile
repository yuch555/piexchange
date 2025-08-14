# できるだけ互換性の高い Debian slim ベース
FROM python:3.11-slim

# 環境設定
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=UTC

# 必要に応じて、証明書/圧縮/ロケールなど最低限のOSパッケージ
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依存インストールはキャッシュ効かせるため先にコピー
COPY requirements.txt /app/
RUN pip install --upgrade pip && pip install -r requirements.txt

# アプリ本体
COPY . /app

COPY .env /app/.env
# 必要な環境変数を設定

# 実行（ENTRYPOINTでもCMDでもOK）
CMD ["python", "main.py"]
