FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml /app/
COPY src /app/src
COPY demo /app/demo
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["python", "src/manage.py", "runserver", "0.0.0.0:8000"]
