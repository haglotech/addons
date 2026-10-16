FROM python:3.12-alpine

RUN pip install --no-cache-dir "aiohttp>=3.9,<4"

# Sprachsteuerung: Matterbridge + Home-Assistant-Plugin (Versionen fest, damit Updates bewusst passieren)
RUN apk add --no-cache nodejs npm \
    && npm install -g --omit=dev --no-audit --no-fund matterbridge@3.10.14 matterbridge-hass@1.5.0 \
    && npm cache clean --force

COPY app /app
WORKDIR /app

CMD ["python", "-u", "server.py"]
