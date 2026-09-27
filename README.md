# HookCourier

Невеликий сервіс приймання HTTP webhook подій. Перший етап створює подію в PostgreSQL, захищає API ключем та підтримує ідемпотентність. Доставка через Redis і Celery запланована на другий тиждень.

## План роботи

Конкретні задачі й критерії перевірки першого тижня: [docs/week-1.md](docs/week-1.md).

## Структура

```text
src/config/                  Django settings та URL routing
src/deliveries/              Моделі, Admin, API, бізнес-правила
src/deliveries/migrations/   Міграції PostgreSQL
tests/                       Перевірки API і правил приймання
docs/                        План етапів
```

`demo/receiver.py`, Celery worker, сторінка статусу та CI з'являться на відповідних етапах плану.

## Локальний запуск

1. Скопіюйте `.env.example` у `.env` і замініть усі значення `change-this-...` на власні секрети.
2. Запустіть `docker compose up --build -d`.
3. Створіть власника: `docker compose exec web python src/manage.py createsuperuser`.
4. Увійдіть у [Django Admin](http://localhost:8000/admin/) та створіть активну `Destination`.

Після створення адреси API приймає запит:

```bash
curl -i http://localhost:8000/api/v1/events/ \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_API_KEY' \
  -H 'Idempotency-Key: appointment-123' \
  -d '{"destination_id":1,"event_type":"appointment.created","payload":{"appointment_id":123}}'
```

Очікувана відповідь першого тижня: `202 Accepted`, ID події та стан `pending`. Повтор із тим самим ключем і даними повертає ту саму подію; інші дані дають `409 Conflict`. Стан читається через `GET /api/v1/events/{id}/` з тим самим API ключем. Поки воркер не доданий, `pending` не змінюється автоматично.

## Тести

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
DB_ENGINE=sqlite .venv/bin/pytest
```

SQLite використовується лише для швидких локальних тестів; основний сервіс працює з PostgreSQL. Команда перевірки міграцій: `DB_ENGINE=sqlite .venv/bin/python src/manage.py makemigrations --check --dry-run`.

