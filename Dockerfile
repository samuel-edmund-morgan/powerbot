FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY prod/ /app/prod/
COPY test/ /app/test/
COPY scripts/ /app/scripts/
COPY sensors/ /app/sensors/
COPY nginx.default.conf /app/nginx.default.conf
COPY nginx.sensors.conf /app/nginx.sensors.conf
COPY docker/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

ENV BOT_ENV=prod

ENTRYPOINT ["/entrypoint.sh"]
