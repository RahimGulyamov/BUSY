from __future__ import annotations
import typing
import os
import logging

BRANCH: typing.Final[str] = "test"
API_KEY: typing.Final[str] = \
    "rZc0wwF5lKs8Zvr2rvtbZSlwMcNYlMv2ARhKy8nIGXib8VGWMqvfSuzjCFNm8ZRSF9cuK8fJw1dK9SAPzgsC52wcYC65HMUaQOo3q2LQag4qXHrqyLswf4VP7QnMydXV"

DB_HOST: typing.Final[str] = "c-c9qdjc9k90rc9599sgsv.rw.mdb.yandexcloud.net:6432"
DB_NAME: typing.Final[str] = "busy-db-test"
DB_USER: typing.Final[str | None] = os.getenv("DB_USER", 'postgres')
DB_PASS: typing.Final[str | None] = os.getenv("DB_PASS", 'postgres')

REDIS_URL: typing.Final[str] = "172.17.0.3"
REDIS_PORT: typing.Final[int] = 6379

TELEGRAM_BOT_SECRET: typing.Final[str | None] = os.getenv("TELEGRAM_BOT_SECRET")

VOX_CREDENTIALS: typing.Final[str] = os.getenv("VOX_CREDENTIALS")
VOX_MAIN_NUMBER: typing.Final[str] = "79014170842"

ONESIGNAL_APP_ID: typing.Final[str | None] = os.getenv("ONESIGNAL_APP_ID")
ONESIGNAL_REST_API_KEY: typing.Final[str | None] = os.getenv("ONESIGNAL_REST_API_KEY")

CP_PUBLIC_ID: typing.Final[str | None] = os.getenv("CP_PUBLIC_ID")
CP_API_SECRET: typing.Final[str | None] = os.getenv("CP_API_SECRET")

DATABASE_ENGINE_ARGS: typing.Final[typing.Any] = {"pool_pre_ping": True, "pool_size": 75, "max_overflow": 0}

AWS_ACCESS_KEY_ID: typing.Final[str | None] = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY: typing.Final[str | None] = os.getenv("AWS_SECRET_ACCESS_KEY")

AMO_CRM_URL: typing.Final[str | None] = "https://busy.amocrm.ru"
AMO_CRM_CLIENT_ID: typing.Final[str | None] = "bf84da9f-b7ad-4908-9f62-a1a174eb76e4"
AMO_CRM_ACCESS_TOKEN: typing.Final[str | None] = os.getenv("AMO_CRM_ACCESS_TOKEN")
AMO_CRM_REFRESH_TOKEN: typing.Final[str | None] = os.getenv("AMO_CRM_REFRESH_TOKEN")
AMO_CRM_CLIENT_SECRET: typing.Final[str | None] = os.getenv("AMO_CRM_CLIENT_SECRET")

LOG_LEVEL = logging.DEBUG
