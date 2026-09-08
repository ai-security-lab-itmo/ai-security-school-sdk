# AI Security School SDK

Python-клиент для учебных агентских лабораторий. Скрипт атаки работает на вашей
машине, а SDK вызывает явно доступные **действия студента** в персональном прогоне
на платформе. Внутренние инструменты атакуемого агента через SDK не публикуются.

Требуется Python 3.12+. Исходники и релизы доступны в
[публичном репозитории](https://github.com/ai-security-lab-itmo/ai-security-school-sdk).

## Установка и токен

```sh
python -m pip install "git+https://github.com/ai-security-lab-itmo/ai-security-school-sdk.git@v0.1.0"
```

Для установки из Git нужен установленный Git. Альтернатива — готовый wheel
из релиза, который можно установить без Git:

```sh
python -m pip install "https://github.com/ai-security-lab-itmo/ai-security-school-sdk/releases/download/v0.1.0/ai_security_school_sdk-0.1.0-py3-none-any.whl"
```

Обе команды устанавливают зафиксированную версию `0.1.0`. Установка по короткому
имени `pip install ai-security-school-sdk` станет доступна после публикации в
PyPI; сейчас используйте одну из команд выше. Настройка публикации описана в
[PUBLISHING.md](https://github.com/ai-security-lab-itmo/ai-security-school-sdk/blob/main/PUBLISHING.md).

На странице операции выберите «Подключить Python SDK» и получите токен. Он
ограничен одной операцией и имеет срок действия. Передайте его через переменную
окружения `AI_SECURITY_SCHOOL_TOKEN`; не сохраняйте токен в коде или репозитории.
Необязательная `AI_SECURITY_SCHOOL_BASE_URL` по умолчанию равна
`https://plgn.aisecschool.ru`. Для удалённых серверов необходим HTTPS.

## Первый вызов

```python
from ai_security_school_sdk import Client

with Client.from_env() as client:
    for available in client.labs.list():
        print(available.lab_id, available.title)

    lab = client.labs.get("YOUR_LAB_ID")
    run = lab.runs.create()
    print("Сохраните run_id для продолжения:", run.run_id)

    for action in run.actions.list():
        print(action.name, action.description)
        print(action.input_schema)
        print(action.examples)

    # Имя и аргументы выбираются из manifest текущей CTF.
    result = run.actions.call("send_message", {"message": "Проверь новый документ"})
    print(result.data)
    print(run.observation().state)
```

`send_message` здесь — пример имени, а не встроенный метод SDK. Конкретные CTF
могут предоставлять разные действия: добавление документа, сообщение агенту,
загрузку вложения и другие операции. SDK получает их имена и JSON Schema от
сервера. Новый набор действий не требует новой версии Python-пакета.

Вызов проверяет аргументы локально и передаёт `expected_task_id`. Сервер повторно
проверяет действие, права и аргументы. SDK не загружает внешние ссылки JSON Schema
и не исполняет код из manifest. Если этап изменился через другой клиент, вызов
возвращает `ConflictError`; явно выполните `run.refresh()` и изучите новый набор.

Внешние поверхности атаки, например реестр пакетов или MCP-сервис, не перечисляются
автоматически. Если они входят в сценарий, взаимодействуйте с ними через их
собственные интерфейсы; SDK управляет только действиями, опубликованными CTF.

## Прогоны, этапы и ветвление

```python
with Client.from_env() as client:
    run = client.runs.get("SAVED_RUN_ID")
    checkpoint = run.checkpoint()
    branch = checkpoint.fork()
    print(branch.run_id, branch.task_id)

    # Здесь выполняются доступные действия атаки.
    verdict = branch.submit()
    print(verdict.passed, verdict.success_rate)
    if verdict.passed:
        branch.advance()  # Явный переход, если есть следующий этап.
        print(branch.actions.list())
```

Этапы одной ветки разделяют состояние. Разные прогоны и forks независимы. Внутри
одного прогона одновременно исполняется одно задание; для параллельного поиска
создавайте отдельные прогоны. Новый прогон не обновляет общий бюджет пользователя.
Checkpoint доступен для свободного прогона; fork сохраняет его состояние и этап.

`submit()` сдаёт записанный сервером результат вашей атаки. Проверка может
повторять действия на скрытых сценариях. Загружать Python-программу для исполнения
на сервере не требуется. Успешный обычный вызов действия сам по себе не даёт зачёт.

`run.close()` закрывает серверный прогон явно. Выход из `with Client(...)` закрывает
только HTTP-соединения и сохраняет прогон для продолжения.

## Долгие задания, повторы и ошибки

```python
from ai_security_school_sdk import Client, JobTimeoutError

with Client.from_env() as client:
    run = client.runs.get("SAVED_RUN_ID")
    job = run.actions.start_call("send_message", {"message": "Обработай заявку"})
    print("Сохраните job_id:", job.job_id)
    try:
        result = job.wait(timeout=120, poll_interval=0.5)
    except JobTimeoutError as error:
        # Истечение времени ожидания не отменяет серверное задание.
        resumed = client.jobs.get(error.job_id)
        result = resumed.wait(timeout=120)
    print(result)
```

- `run.actions.call()` и `run.submit()` запускают задание и ждут результат.
  `start_call()` и `start_submission()` сразу возвращают handle задания.
- `client.jobs.get(id)`, `job.refresh()`, `job.cancel()` и `job.result()` позволяют
  управлять уже созданным заданием. Отмена не возвращает стоимость LLM-запросов,
  которые уже отправлены.
- Все изменения имеют `Idempotency-Key`. Сетевые повторы используют тот же ключ
  и тело; SDK никогда не создаёт новый ключ внутри повторного запроса.
- Для восстановления после завершения процесса передайте сохранённый
  `idempotency_key=`. `TransportError.idempotency_key` содержит ключ запроса с
  неопределённым результатом. С тем же ключом повторяйте только то же действие,
  аргументы и текущую CTF; не создавайте новую попытку вслепую.
- По умолчанию доступны два повтора при сетевой ошибке и HTTP 429/502/503/504.
  Параметры клиента: `timeout=30`, `max_retries=2`, `retry_backoff=0.25`.
  Серверные HTTP-ошибки и ошибки самого задания после polling не запускают новую
  задачу.
- `JobTimeoutError` содержит `job_id`. `JobInterruptedError` означает, что
  безопасное автоматическое продолжение исполнения невозможно; изучите историю.
- `AuthenticationError`, `PermissionDeniedError`, `ConflictError`,
  `StageLockedError`, `LimitExceededError` наследуют `APIError` с полями `code`,
  `message`, `details`, `status_code`, `job_id`.
- `ActionValidationError` описывает локальное несоответствие схеме, а
  `ProtocolError` — некорректный ответ или неподдерживаемую ссылку схемы.

## Async и наблюдения

```python
import asyncio
from ai_security_school_sdk import AsyncClient


async def main():
    async with AsyncClient.from_env() as client:
        lab = await client.labs.get("YOUR_LAB_ID")
        run = await lab.runs.create()
        actions = await run.actions.list()
        print(actions)
        observation = await run.observation()
        page = await run.events(after=0)
        print(observation.state, observation.usage)
        for event in page.events:
            print(event.sequence, event.kind, event.data)
        # Следующая порция: await run.events(after=page.next_cursor)


asyncio.run(main())
```

Все методы с сетевым вводом-выводом у `AsyncClient` вызываются через `await`.
Конструкторы, `from_env()`, поля объектов и `job.result()` синхронные. Asyncio
отмена локальной coroutine не отменяет серверное задание; сохраняйте `job_id`.
Примеры: [первый эксперимент](https://github.com/ai-security-lab-itmo/ai-security-school-sdk/blob/v0.1.0/examples/first_experiment.py),
[параллельный поиск](https://github.com/ai-security-lab-itmo/ai-security-school-sdk/blob/v0.1.0/examples/async_search.py),
[этапы и fork](https://github.com/ai-security-lab-itmo/ai-security-school-sdk/blob/v0.1.0/examples/multistage.py).

Объекты содержат типизированный снимок в `.info`. Методы `refresh()` обновляют его;
для актуального состояния сервера не полагайтесь на старый снимок. Аргументы и
результаты конкретных действий — обычные JSON-объекты. Новые дополнительные поля
общих серверных моделей допускаются для совместимости.

## Разработка

```sh
uv sync --python 3.12
uv run pytest
uv run ruff check .
uv run mypy src
uv build
```

Пакет не импортирует backend платформы. Тесты используют HTTPX MockTransport и
проверяют общий HTTP-контракт sync/async клиентов без LLM-вызовов. API имеет базу
`/api/learner/v1`; его версия не зависит от номера выпуска SDK.
