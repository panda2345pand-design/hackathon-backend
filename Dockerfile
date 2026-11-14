FROM python:3.13-slim

WORKDIR /src

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリ本体に加えて、起動時のマイグレーションに必要な migrations/ と alembic.ini もここで入る(.dockerignore で除外していないこと)
COPY . .

# Cloud Run は PORT 環境変数でポートを渡してくる(既定 8080)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
