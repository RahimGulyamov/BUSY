from __future__ import annotations

import json
import typing
import unittest
from aiohttp.test_utils import AioHTTPTestCase
from aiohttp import web
from pathlib import Path

from app.api.handlers import is_correct_api_key, hello, call_from_voximplant, websocket_handler, \
    start_outbound_call
from app import config_helper

config_helper.import_config('config_test.py')
import config
api_key = config.API_KEY


class TestIsCorrectApiKey(unittest.TestCase):
    def test_correct_api_key(self) -> None:
        url = f"https://api.busy.contact/logs/hello?apiKey={api_key}"
        self.assertTrue(is_correct_api_key(url))

    def test_wrong_api_key(self) -> None:
        wrong_api_key = "fkdldo94rkkrfrl"
        url = f"https://api.busy.contact/logs/hello?apiKey={wrong_api_key}"
        self.assertFalse(is_correct_api_key(url))

    def test_no_api_key(self) -> None:
        url = f"https://api.busy.contact/logs/hello?apiKe=mdkek"
        with self.assertRaises(KeyError):
            is_correct_api_key(url)


class TestCallFromVoximplant(AioHTTPTestCase):
    async def get_application(self):
        app = web.Application()
        app.router.add_get('/', hello)
        app.router.add_post('/voximplant/v1/calls/connection', call_from_voximplant)
        return app

    async def test_example(self):
        async with self.client.request("GET", "/") as response:
            self.assertEqual(response.status, 200)
            text = await response.text()
            self.assertIn("Hello, world", text)

    async def test_wrong_api_key(self) -> None:
        async with self.client.request("POST", "/voximplant/v1/calls/connection?apiKey=13f") as response:
            self.assertEqual(response.status, 200)
            text = await response.text()
            self.assertIn("wrongKey", text)

    async def test_no_data_params(self) -> None:
        payload = json.dumps({'key1': 'value1', 'key2': 'value2'})
        async with self.client.request("POST", f"/voximplant/v1/calls/connection?apiKey={api_key}", data=payload):
            self.assertRaises(KeyError)

    async def test_wrong_data(self) -> None:
        payload = json.dumps({'userNumber': '890837821', 'callerNumber': None})
        async with self.client.request("POST", f"/voximplant/v1/calls/connection?apiKey={api_key}", data=payload) as response:
            self.assertEqual(response.status, 400)
            text = await response.text()
            self.assertIn("wrongData", text)


class TestOutboundCallConnection(AioHTTPTestCase):
    async def get_application(self):
        app = web.Application()
        app.router.add_post('/voximplant/v1/calls/{callId}/connection/outbound', start_outbound_call)
        return app

    async def test_no_data_params(self) -> None:
        payload = json.dumps({'key1': 'value1', 'key2': 'value2'})
        async with self.client.request("POST", f"/voximplant/v1/calls/connection/outbound?apiKey={api_key}", data=payload):
            self.assertRaises(KeyError)

