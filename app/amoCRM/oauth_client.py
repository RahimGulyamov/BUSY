from datetime import datetime
from aiohttp import ClientSession
from typing import Optional, Any
from urllib.parse import urlencode

from .base_client import BaseClient
from .errors import AmoException
from ..db.interface import DatabaseApi


class AmoOAuthClient(BaseClient):
    def __init__(
            self,
            access_token: str,
            refresh_token: str,
            crm_url: str,
            client_id: str,
            client_secret: str,
            redirect_uri: str,
    ):
        self._access_token = access_token
        self._refresh_token = refresh_token
        self.crm_url = crm_url if not crm_url.endswith('/') else crm_url[:-1]
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri

    async def _send_api_request(
            self,
            method: str,
            url: str,
            headers: dict = None,
            data: Any = None,
            update_tokens: bool = False,
    ) -> dict:
        try:
            dt = datetime.today().strftime("%a, %d %b %Y %H-%m-%d")
            date_time = f"{dt} UTC"
            headers = {
                "IF-MODIFIED-SINCE": f"{date_time}",
                "Content-Type": "application/json",
                'Authorization': f'Bearer {self.access_token}'
            }
            response = await super()._send_api_request(method, url, headers, data)
            return response
        except AmoException as e:
            if '401' in str(e) and not update_tokens:
                await self.update_tokens()
                return await self._send_api_request(method, url, headers, data, True)
            raise


    async def update_tokens(self):
        url = f'{self.crm_url}/oauth2/access_token'
        params = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'refresh_token',
            'refresh_token': self.refresh_token,
            'redirect_uri': self.redirect_uri,
        }
        dt = datetime.today().strftime("%a, %d %b %Y %H-%m-%d")
        date_time = f"{dt} UTC"
        headers = {
            "IF-MODIFIED-SINCE": f"{date_time}",
            "Content-Type": "application/json",
            'Authorization': f'Bearer {self.access_token}'
        }
        async with ClientSession() as session:
            session.headers.update(headers)
            async with session.post(url, json=params) as r:
                data = await r.json()
                if r.status > 204:
                    raise AmoException(data)
                self._update_token_params(data['access_token'], data['refresh_token'])
                await DatabaseApi().update_amo_tokens(data['access_token'], data['refresh_token'])

    def _update_token_params(self, access_token: str, refresh_token: str):
        self._access_token = access_token
        self._refresh_token = refresh_token

    async def _create_or_update_entities(
            self, entity: str, objects: list, update: bool = False
    ) -> dict:
        """method for create or update entities

        Args:
            entity (str): name of entities like 'leads'
            objects (list): list of obejcts
            update (bool): if True http method patch else post

        Returns:
            dict: query result
        """
        url = f'{self.crm_url}/api/v4/{entity}'
        http_method = 'patch' if update else 'post'
        return await self._send_api_request(http_method, url, data=objects)

    async def _get_entities(
            self,
            entity: str,
            limit: int = 250,
            page: int = 1,
            with_params: Optional[list] = None,
            filters: Optional[dict] = None,
            order: Optional[dict] = None,
    ) -> dict:
        url = f'{self.crm_url}/api/v4/{entity}'
        params: dict = {'limit': limit, 'page': page}
        if with_params:
            params['with'] = ','.join(param for param in with_params)  # type: ignore
        if filters:
            pass
            # filter_query = self.__create_filter_query(filters)
            # params.update(filter_query)
        if order:
            order_query = {f'order[{k}]': v for k, v in order.items()}
            params.update(order_query)
        url = f'{url}?{urlencode(params)}'
        return await self._send_api_request('get', url)

    async def create_leads(self, objects: list) -> dict:
        """create leads
        Doc: https://www.amocrm.ru/developers/content/crm_platform/leads-api#leads-add
        Args:
            objects (list): list of leads
        """
        return await self._create_or_update_entities('leads', objects)

    async def update_leads(self, objects: list) -> dict:
        """update leads
        Doc: https://www.amocrm.ru/developers/content/crm_platform/leads-api#leads-edit
        Args:
            objects (list): list of leads
        """
        return await self._create_or_update_entities('leads', objects, True)

    async def get_lead(self, lead_id: int) -> dict:
        """return lead
        Doc: https://www.amocrm.ru/developers/content/crm_platform/leads-api#lead-detail
        Args:
            lead_id (int): id of lead
        """
        url = f'{self.crm_url}/api/v4/leads/{lead_id}'
        return await self._send_api_request('get', url)

    async def get_leads(
            self,
            limit: int = 250,
            page: int = 1,
            with_params: Optional[list] = None,
            filters: Optional[dict] = None,
            order: Optional[dict] = None,
    ) -> dict:
        """Get leads
        Doc: https://www.amocrm.ru/developers/content/crm_platform/leads-api#leads-list
        Args:
            limit (int, optional): limit of rows. Defaults to 250.
            page (int, optional): number of page. Defaults to 1.
            with_params (Optional[list], optional): params. Defaults to None.
            filters (Optional[dict], optional): filter params like {'[updated_at][from]': '<timestamp>'}. Defaults to None.
            order (Optional[dict], optional): order params like {'update_at': 'asc'}. Defaults to None.
        """
        params: dict = {k: v for k, v in locals().items() if k != 'self'}
        return await self._get_entities('leads', **params)

    async def get_pipelines(self) -> dict:
        """get leads pipelines
        """
        url = f'{self.crm_url}/api/v4/leads/pipelines'
        return await self._send_api_request('get', url)

    async def get_pipeline(self, pipeline_id: int) -> dict:
        """get leads pipeline
        Doc: https://www.amocrm.ru/developers/content/crm_platform/leads_pipelines#pipelines-list
        """
        url = f'{self.crm_url}/api/v4/leads/pipelines/{pipeline_id}'
        return await self._send_api_request('get', url)

    async def get_pipeline_statuses(self, pipeline_id: int) -> dict:
        """return pipeline Statuses
        Doc: https://www.amocrm.ru/developers/content/crm_platform/leads_pipelines#statuses-list
        Args:
            pipeline_id (int): id of pipeline
        """
        url = f'{self.crm_url}/api/v4/leads/pipelines/{pipeline_id}/statuses'
        return await self._send_api_request('get', url)

    async def get_pipeline_status(self, pipeline_id: int, status_id: int) -> dict:
        """Get status
        Doc: https://www.amocrm.ru/developers/content/crm_platform/leads_pipelines#status-detail
        Args:
            pipeline_id (int): id of pipeline
            status_id (int): id of status
        """
        url = (
            f'{self.crm_url}/api/v4/leads/pipelines/{pipeline_id}/statuses/{status_id}'
        )
        return await self._send_api_request('get', url)

    async def get_contacts(
            self,
            limit: int = 250,
            page: int = 1,
            with_params: Optional[list] = None,
            filters: Optional[dict] = None,
            order: Optional[dict] = None,
    ) -> dict:
        """Get contacts
        Doc: https://www.amocrm.ru/developers/content/crm_platform/contacts-api#contacts-list
        Args:
            limit (int, optional): limit of rows. Defaults to 250.
            page (int, optional): number of page. Defaults to 1.
            with_params (Optional[list], optional): params. Defaults to None.
            filters (Optional[dict], optional): filter params like {'[updated_at][from]': '<timestamp>'}. Defaults to None.
            order (Optional[dict], optional): filter params like {'updated_at': 'asc'}. Defaults to None.

        """
        params = {k: v for k, v in locals().items() if k != 'self'}
        return await self._get_entities('contacts', **params)

    async def get_contact(self, contact_id: int) -> dict:
        """Get contact
        Doc: https://www.amocrm.ru/developers/content/crm_platform/contacts-api#contact-detail
        Args:
            contact_id (int): id of contact
        """
        url = f'{self.crm_url}/api/v4/contacts/{contact_id}'
        return await self._send_api_request('get', url)

    async def create_contacts(self, contacts: list) -> dict:
        """Create contacts
        Doc: https://www.amocrm.ru/developers/content/crm_platform/contacts-api#contacts-add
        Args:
            contacts (list): list of contacts objects
        """
        return await self._create_or_update_entities('contacts', contacts)

    async def update_contacts(self, contacts: list) -> dict:
        """Update contacts

        Args:
            contacts (list): list of contacts object


        """
        url = f'{self.crm_url}/api/v4/contacts'
        return await self._create_or_update_entities('contacts', contacts, True)

    @property
    def access_token(self) -> str:
        return self._access_token

    @property
    def refresh_token(self) -> str:
        return self._refresh_token

    @property
    def tokens(self) -> dict:
        return {
            'access_token': self.access_token,
            'refresh_token': self.refresh_token,
        }
