import asyncio
import logging
from json import JSONDecodeError, loads

from aiohttp import ClientSession, ClientConnectorError, ServerTimeoutError, ClientResponse
from typing import Any


from .errors import AmoException

logger = logging.getLogger('amocrm_wrapper')


class BaseClient(object):
    crm_url: str = ''

    async def _parse_response_body(self, response: ClientResponse) -> dict:
        raw_data = await response.json()
        if not raw_data:
            return {}
        data = loads(raw_data)
        return data

    async def _process_request(self, response: ClientResponse, method: str, url: str, data: Any = None) -> dict:
        if response.status == 204:
            return {}
        elif response.status == 429:
            await asyncio.sleep(5)
            logger.warning('429 http error, sleep 5 sec')
            return await self._send_api_request(method, url, data)

        data = await response.json()
        if 'error' in data or response.status >= 400:
            raise AmoException(data, code=response.status)
        json_data = data['response'] if 'response' in data else data
        return json_data

    async def _send_api_request(
        self, method: str, url: str, headers: dict = None, data: Any = None, _connection_counter: int = 0
    ) -> dict:
        try:
            async with ClientSession() as session:
                session.headers.update(headers)
                if method == 'get':
                    async with session.get(url, json=data) as response:
                        return await self._process_request(response, method, url, data)

                elif method == 'post':
                    async with session.post(url, json=data) as response:
                        return await self._process_request(response, method, url, data)

                elif method == 'patch':
                    async with session.patch(url, json=data) as response:
                        return await self._process_request(response, method, url, data)

                else:
                    return {}

        except JSONDecodeError as e:
            raise AmoException({'error': str(e)})
        except AmoException as e:
            raise AmoException({'error': str(e)}, code=401)
        except (ServerTimeoutError, ClientConnectorError) as e:
            if _connection_counter > 3:
                raise
            await asyncio.sleep(2)
            _connection_counter += 1
            return await self._send_api_request(method, url, headers, data, _connection_counter)

