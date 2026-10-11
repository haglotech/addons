FROM python:3.12-alpine

RUN pip install --no-cache-dir "aiohttp>=3.9,<4"

COPY app /app
WORKDIR /app

CMD ["python", "-u", "server.py"]
