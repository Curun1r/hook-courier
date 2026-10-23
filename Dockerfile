FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings_production

WORKDIR /app
COPY pyproject.toml /app/
COPY src /app/src
COPY demo /app/demo
COPY gunicorn.conf.py /app/gunicorn.conf.py
RUN pip install --no-cache-dir . \
    && DJANGO_DEBUG=1 DJANGO_SETTINGS_MODULE=config.settings_static python src/manage.py collectstatic --noinput \
    && groupadd --gid 10001 hookcourier \
    && useradd --uid 10001 --gid hookcourier --create-home hookcourier

USER hookcourier

EXPOSE 8000
CMD ["gunicorn", "--config", "/app/gunicorn.conf.py", "config.wsgi:application"]
