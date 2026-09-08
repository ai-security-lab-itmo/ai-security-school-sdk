# AI Security School SDK

Python-клиент для работы с агентными средами полигона AI Security School.
SDK получает документацию конкретной задачи и вызывает тот же `agent-env` API,
которым пользуется браузер: состояние, действия, проверку и сброс.

## Установка

Требуется Python 3.12 или новее.

```sh
python -m pip install ai-security-school-sdk
```

Для воспроизводимой установки этой версии:

```sh
python -m pip install ai-security-school-sdk==0.2.0
```

Версия 0.2.0 меняет публичный интерфейс SDK. Вместо отдельных лабораторий и
прогонов используются существующие экземпляры `agent-env` и их задачи.

## Подключение и документация

На странице операции откройте «Подключить Python SDK», создайте токен и передайте
его через переменную окружения. Токен ограничен выбранной операцией.
`AI_SECURITY_SCHOOL_BASE_URL` можно задать для другого развёртывания; по умолчанию
используется `https://plgn.aisecschool.ru`.

```sh
export AI_SECURITY_SCHOOL_TOKEN="YOUR_TOKEN"
```

```python
from ai_security_school_sdk import Client

with Client.from_env() as client:
    for env in client.envs.list():
        print(env.instance_id, env.title)
        for task in env.tasks.list():
            print(task.task_id, task.title)

    task = client.tasks.get("YOUR_TASK_ID")
    docs = task.documentation()
    print(docs.instructions)
    print(docs.action_payload_schema)
    print(docs.action_payload_examples)
    for action in docs.actions:
        print(action.name, action.description, action.input_schema, action.examples)
```

`client.envs.get(instance_id)` возвращает одну среду. `env.tasks.list()` возвращает
задачи из полученного списка; `env.tasks.get(task_id)` загружает документацию
выбранной задачи. Среда соответствует `agent-env-instance`, задача —
`ctf-instance`. Документация описывает доступные студенту точки входа, а не
внутренние инструменты агента. Набор действий и схемы приходят с сервера, поэтому
новая задача не требует новой версии SDK.

## Выполнение действий

Для действия с именем используйте `task.actions.call(name, arguments)`.
Имя и аргументы выбираются из документации конкретной задачи:

```python
with Client.from_env() as client:
    task = client.tasks.get("YOUR_TASK_ID")
    print(task.actions.list())

    # Используйте это имя только если оно есть в документации выбранной задачи.
    result = task.actions.call("send_message", {"message": "Проверь новый документ"})
    print(result.status, result.response, result.state)
    print(task.state().model_dump())
```

Метод вставляет поле `action` в тело запроса. В `arguments` передаются остальные
поля; `input_schema` и `examples` описанного действия не содержат `action`.
SDK проверяет аргументы и полное тело по JSON Schema перед отправкой. Сервер
применяет проверки существующего обработчика действия, а также контролирует
доступ, пререквизиты и бюджет пользователя.

`task.act(payload)` принимает полное нативное тело действия. Так поддерживаются
и существующие среды, у которых нет именованных действий, например чат:

```python
with Client.from_env() as client:
    task = client.tasks.get("YOUR_CHAT_TASK_ID")
    docs = task.documentation()
    print(docs.action_payload_schema, docs.action_payload_examples)
    result = task.act({"message": "Привет"})
    print(result.model_dump())
```

Пустой `docs.actions` не означает отсутствие возможностей: используйте полную
схему `action_payload_schema`. SDK не угадывает имена действий по коду runtime.
Документация не исполняется как Python-код, внешние ссылки JSON Schema не
загружаются. Внешние поверхности сценария, например MCP-сервис или реестр
зависимостей, используются через их собственные интерфейсы.

## Состояние, проверка и сброс

```python
with Client.from_env() as client:
    task = client.tasks.get("YOUR_TASK_ID")
    current = task.state()
    print(current.status, current.missing_prerequisites)

    if task.documentation().supports_grading:
        verdict = task.grade()
        print(verdict.grader_passed, verdict.grader_result, verdict.completed)

    # При необходимости передайте task.grade({...}) полезную нагрузку проверки.
    # Явный сброс через существующее поведение среды:
    # task.reset()
```

У одного пользователя задачи одной среды разделяют состояние с браузером и
другими скриптами. Получение нового handle или создание второго клиента не
создаёт отдельную попытку. Сброс затрагивает общее состояние среды; сохранение
зачётов и пререквизитов определяется её существующим поведением. Локальный
контекстный менеджер закрывает только HTTP-соединения.

Алгоритм атаки работает в вашем Python-процессе. Вызовы возвращают обычные ответы
runtime без фоновых заданий SDK, checkpoint, fork или воспроизведения сценария.
Вызовы одной среды выполняйте последовательно: параллельные кандидаты будут
менять одно состояние. Async-клиент удобен для неблокирующего ожидания и работы
с разными независимыми средами.

## Async

```python
import asyncio
from ai_security_school_sdk import AsyncClient


async def main():
    async with AsyncClient.from_env() as client:
        task = await client.tasks.get("YOUR_TASK_ID")
        docs = await task.documentation()
        print(docs.instructions)
        result = await task.act({"message": "Привет"})  # Если разрешено схемой.
        print(result.response)
        print((await task.state()).state)


asyncio.run(main())
```

Методы async-ресурсов, включая `env.tasks.list()`, вызываются через `await`.
Конструкторы, `from_env()`, поля `.info`, `.task_id`, `.instance_id` и `.title`
синхронные. `task.documentation()` обновляет снимок `.info`; `task.state()`
получает текущее серверное состояние. Дополнительные поля ответов сохраняются
в моделях и доступны через `model_dump()`.

Примеры: [документация и вызов](https://github.com/ai-security-lab-itmo/ai-security-school-sdk/blob/v0.2.0/examples/first_experiment.py),
[последовательный поиск кандидатов](https://github.com/ai-security-lab-itmo/ai-security-school-sdk/blob/v0.2.0/examples/async_search.py),
[связанные задачи одной среды](https://github.com/ai-security-lab-itmo/ai-security-school-sdk/blob/v0.2.0/examples/multistage.py).

## Ошибки и сетевые повторы

- `APIError` содержит `code`, `message`, `status_code`, `details` и `usage`.
  Для HTTP 401/403/404/409/429 используются `AuthenticationError`,
  `PermissionDeniedError`, `NotFoundError`, `ConflictError`, `LimitExceededError`.
- Успешный HTTP-ответ с `status="locked"` остаётся `RuntimeResponse`:
  проверьте `status` и `missing_prerequisites` перед дальнейшими действиями.
- `ActionValidationError` означает локальное несоответствие схеме,
  `ProtocolError` — некорректный ответ или неподдерживаемую ссылку в схеме.
- Автоматические повторы допускаются только для GET: при сетевых ошибках и
  HTTP 429/502/503/504. Параметры клиента: `timeout=120`, `max_retries=2`,
  `retry_backoff=0.25`; `max_retries=0` отключает повторы.
- Действия, проверка и сброс **никогда не повторяются автоматически**.
  При сетевом сбое `TransportError.may_have_executed` показывает, что изменение
  могло уже выполниться. Сначала изучите `task.state()` и только затем решайте,
  нужен ли повтор. Таймаут или отмена async-корутины не доказывают, что сервер
  остановил исполнение.
- Для удалённого сервера требуется HTTPS. HTTP доступен для локальной разработки;
  перенаправления HTTP не выполняются, чтобы не передавать токен другому адресу.

## Разработка

```sh
uv sync --python 3.12
uv run pytest
uv run ruff check .
uv run mypy src
uv build
```

Пакет не импортирует backend платформы. Sync/async тестируются через HTTPX
MockTransport против одного контракта `/api/agent-env`.
Публикация описана в [PUBLISHING.md](https://github.com/ai-security-lab-itmo/ai-security-school-sdk/blob/main/PUBLISHING.md).
