## Backend
Репозиторий для backend-части проекта Busy.

### Требования к коду
[См. confluence](https://workshop.samcs.ru/confluence/x/1wDUAw)

### Структура проекта
 - `app` - весь код приложения в форме python-пакета
 - `tests` - тесты. Название файла должно начинаться с `test_`
 - `requirements.txt` - зависимости проекта

### Запуск приложения
```bash
python -m app --config <config file>
```

Параметр `--config` является обязательным (см. ниже), также можно указать некоторые другие параметры запуска (при запуске без параметров показывается справка)

### Запуск тестов
```bash
python -m unittest discover -s tests
```

### Docker
Сборки контейнера:
```bash
docker build -t <tag> .
```
Локальный запуск приложения (API доступно на localhost:8000):
```bash
docker run -p 8000:8000 <tag>
```
(добавьте `-it`, чтобы увидеть стандартый вывод)

Тесты в контейнере запускаются так: 
```bash
docker exec -it <container id> python -m unittest discover -s tests
```

### Конфигурация
Под разные сценарии работы предусмотрены отдельные файлы конфигурации: `config_{local|test|deploy}.py` для локального тестирования, тестового сервера и продакшена соответственно. По умолчанию в Dockerfile прописан конфиг для локального тестирования, при деплое на тест и прод - используются соответствующие конфиги.

Вся более-менее секретная информация вроде API-ключей не должна храниться в файлах - ее следует получать из переменных окружения, задаваемых CI/CD. На данный момент определены следующие переменные окружения:
- `TELEGRAM_BOT_SECRET` - API-ключ телеграм-бота

### CI/CD

Организован автоматический пайплайн, включающий в себя сборку нового образа на основе содержимого репозитория, запуск тестов и развертывание обновленного образа на сервере
- Изменения в ветке `test` триггерят пайплайн для развертывания на тестовом-сервере
- В ветке `master` - на продакшен-сервере соответственно

### Настройка venv (необязательно)
```bash
python -m venv venv
.venv/bin/activate
pip install -r requirements.txt
```
### Инструкция по локальному запуску 
- Клонировать репозиторий бэкенда
    ```bash
    git clone https://workshop.samcs.ru/bitbucket/scm/busy/backend.git
    ```
- Создать виртуальное окружение python и установить зависимости в клонированном репозитории
    ```bash
    python -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```
    Возможно потребуется ещё
    ```bash
    apt-get install -y ffmpeg
    ```
- Создать базу данных busydblocal, пользователя с именем postgres и с паролем postgres в postgresql (Можно создать со своими названиями и паролем, но тогда их нужно записать их в config_local.py или в соответсвующие переменные окружения)
    ```bash
    sudo service postgresql start
    psql -U postgres
    CREATE DATABASE busydblocal;
    ALTER USER postgres WITH PASSWORD 'postgres';
    ```
- Применить миграции к созданной бд с помощью alembic
    ```bash
    alembic upgrade head
    ```
- Создать тестового бота в телеграмме, которого будет использовать приложение, и записать его API ключ в `TELEGRAM_BOT_SECRET` в config_local.py или в переменную окружения (по умолчанию используется бот https://t.me/BusyTest2Bot)
- Настроить переменную VOX_CREDENTIALS:
    ```bash
    export VOX_CREDENTIALS=$(cat credentials.json)
    ```
- Запустить приложение с локальным конфигом
    ```bash
    python -m app --config config_local.py --no-amo --reset-plans --no-api --reset-default-prefs
    ```
- Подключится к тестовому сценарию на voximplant - зависит от сценария