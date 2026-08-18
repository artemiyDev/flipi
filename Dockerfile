FROM node:22-alpine AS frontend-build

WORKDIR /frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend ./
RUN npm run build


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch && pip install --no-cache-dir "fsrs[optimizer]"

COPY bot ./bot
COPY app ./app
COPY seed ./seed
COPY migrations ./migrations
COPY alembic.ini .
COPY --from=frontend-build /frontend/dist ./frontend/dist

CMD ["sh", "-c", "alembic upgrade head && python -m bot.main"]
