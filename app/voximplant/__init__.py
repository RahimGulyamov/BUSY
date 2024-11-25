from __future__ import annotations

import json
import logging
from typing import Optional, Tuple
import aiohttp
from datetime import datetime

from voximplant.apiclient import VoximplantAPI, VoximplantException

client: VoximplantAsyncApi


# Don't want to store credentials in a file
class VoximplantAPICustom(VoximplantAPI):
    def __init__(self, credentials: str, endpoint=None):
        self.credentials = json.loads(credentials)
        if self.credentials is None:
            raise VoximplantException("Credentials not found")
        self.account_id = self.credentials["account_id"]
        if not (endpoint is None):
            self.endpoint = endpoint
        else:
            self.endpoint = "api.voximplant.com"


class VoximplantAsyncApi:
    def __init__(self, credentials: str, application_name, outbound_call_rule_id, endpoint=None):
        self.__voximplant = VoximplantAPICustom(credentials, endpoint)
        self.__application_name = application_name
        self.__outbound_call_rule_id = outbound_call_rule_id

    async def send_sms_message(self, source: str, destination: str, sms_body: str) -> dict:
        params = dict()
        params['source'] = source
        params['destination'] = destination
        params['sms_body'] = sms_body

        res = await self._perform_request('SendSmsMessage', params)
        if 'error' in res:
            raise VoximplantException(res['error']['msg'], res['error']['code'])

        return res

    async def start_outbound_call(self, caller: str, destination: str, voximplant_number: str, call_id: str) -> dict:
        custom_data = json.dumps({'caller': caller, 'destination': destination, 'voximplantNumber': voximplant_number,
                                  'callId': call_id})
        return await self.__start_scenarios(self.__outbound_call_rule_id, script_custom_data=custom_data)

    async def buy_new_number(self) -> Optional[Tuple[str, float, float]]:
        """
        if we can't buy new number, returns None
        else returns (number, installation_price, monthly_price)
        """
        COUNTRY_CODE = 'RU'
        PHONE_CATEGORY = 'MOBILE'
        REGION = 177  # Moscow

        new_number_info = await self.__pick_number(COUNTRY_CODE, PHONE_CATEGORY, REGION)

        if new_number_info is None:
            return None
        number, installation_price, monthly_price = new_number_info
        buying_info = await self.__attach_phone_number(COUNTRY_CODE, PHONE_CATEGORY, REGION, phone_number=[number])

        if len(buying_info['phone_numbers']) != 1 or 'required_verification' in buying_info['phone_numbers'][0]:
            return None

        await self.__control_sms(number, 'enable')

        await self.__bind_phone_number_to_application(phone_number=[number], rule_id=self.__outbound_call_rule_id,
                                                      application_name=self.__application_name, bind=True)
        # can't create user with empty password by http request
        await self.__add_user(number, number, '7a4wy2nA?&_', application_name=self.__application_name)

        return number, installation_price, monthly_price

    async def get_transcript(self, session_id: str) -> str:
        DATE_FORMAT = '%Y-%m-%d %H:%M:%S'
        MIN_DATE = datetime.strptime('1970-01-01 00:00:00', DATE_FORMAT)
        MAX_DATE = datetime.strptime('3000-01-01 00:00:00', DATE_FORMAT)
        result = await self.__get_call_history(MIN_DATE, MAX_DATE, call_session_history_id=session_id,
                                               with_records=True)
        transcript_url = result['result'][0]['records'][0]['transcription_url']
        # todo: store session in field
        async with aiohttp.ClientSession() as session:
            async with session.get(transcript_url) as response:
                OK_STATUS = 200
                if response.status != OK_STATUS:
                    raise VoximplantException(response.status)
                return await response.text()

    async def __pick_number(self, country_code, phone_category, region) -> Optional[Tuple[str, float, float]]:
        COUNT = 20
        numbers_info = (await self.__get_new_phone_numbers(country_code, phone_category, region, count=COUNT))['result']
        PERIOD_ONE_MONTH = '0-1-0 0:0:0'
        MAX_PHONE_PRICE = 500.0
        for number_info in numbers_info:
            if number_info['phone_period'] != PERIOD_ONE_MONTH:
                continue
            if 'sms support' not in number_info['phone_region_name'].lower():
                continue
            phone_price = number_info['phone_price']
            installation_price = number_info['phone_installation_price']
            if phone_price < MAX_PHONE_PRICE and installation_price < MAX_PHONE_PRICE:
                return number_info['phone_number'], installation_price, phone_price

    async def __get_call_history(self, from_date, to_date, call_session_history_id=None, application_id=None,
                         application_name=None, user_id=None, rule_name=None, remote_number=None, local_number=None,
                         call_session_history_custom_data=None, with_calls=None, with_records=None,
                         with_other_resources=None, child_account_id=None, children_calls_only=None, with_header=None,
                         desc_order=None, with_total_count=None, count=None, offset=None, output=None, is_async=None):
        """
        Gets the call history.

        :rtype: dict
        """
        params = dict()

        passed_args = []
        if application_id is not None:
            passed_args.append('application_id')
        if application_name is not None:
            passed_args.append('application_name')

        if len(passed_args) > 1:
            raise VoximplantException(", ".join(passed_args) + " passed simultaneously into get_call_history")

        params['from_date'] = self.__voximplant._py_datetime_to_api(from_date)

        params['to_date'] = self.__voximplant._py_datetime_to_api(to_date)

        if call_session_history_id is not None:
            params['call_session_history_id'] = self.__voximplant._serialize_list(call_session_history_id)

        if application_id is not None:
            params['application_id'] = application_id

        if application_name is not None:
            params['application_name'] = application_name

        if user_id is not None:
            params['user_id'] = self.__voximplant._serialize_list(user_id)

        if rule_name is not None:
            params['rule_name'] = rule_name

        if remote_number is not None:
            params['remote_number'] = self.__voximplant._serialize_list(remote_number)

        if local_number is not None:
            params['local_number'] = self.__voximplant._serialize_list(local_number)

        if call_session_history_custom_data is not None:
            params['call_session_history_custom_data'] = call_session_history_custom_data

        if with_calls is not None:
            params['with_calls'] = with_calls

        if with_records is not None:
            params['with_records'] = with_records

        if with_other_resources is not None:
            params['with_other_resources'] = with_other_resources

        if child_account_id is not None:
            params['child_account_id'] = self.__voximplant._serialize_list(child_account_id)

        if children_calls_only is not None:
            params['children_calls_only'] = children_calls_only

        if with_header is not None:
            params['with_header'] = with_header

        if desc_order is not None:
            params['desc_order'] = desc_order

        if with_total_count is not None:
            params['with_total_count'] = with_total_count

        if count is not None:
            params['count'] = count

        if offset is not None:
            params['offset'] = offset

        if output is not None:
            params['output'] = output

        if is_async is not None:
            params['is_async'] = is_async

        res = await self._perform_request('GetCallHistory', params)
        if 'error' in res:
            raise VoximplantException(res['error']['msg'], res['error']['code'])
        if 'result' in res:
            for p in res['result']:
                self.__voximplant._preprocess_call_session_info_type(p)
        return res

    async def __add_user(self, user_name, user_display_name, user_password, application_id=None, application_name=None,
                 parent_accounting=None, user_active=None, user_custom_data=None):
        """
        Adds a new user.

        :rtype: dict
        """
        params = dict()

        passed_args = []
        if application_id is not None:
            passed_args.append('application_id')
        if application_name is not None:
            passed_args.append('application_name')

        if len(passed_args) > 1:
            raise VoximplantException(", ".join(passed_args) + " passed simultaneously into add_user")
        if len(passed_args) == 0:
            raise VoximplantException("None of application_id, application_name passed into add_user")

        params['user_name'] = user_name

        params['user_display_name'] = user_display_name

        params['user_password'] = user_password

        if application_id is not None:
            params['application_id'] = application_id

        if application_name is not None:
            params['application_name'] = application_name

        if parent_accounting is not None:
            params['parent_accounting'] = parent_accounting

        if user_active is not None:
            params['user_active'] = user_active

        if user_custom_data is not None:
            params['user_custom_data'] = user_custom_data

        res = await self._perform_request('AddUser', params)
        if "error" in res:
            raise VoximplantException(res["error"]["msg"], res["error"]["code"])

        return res

    async def __control_sms(self, phone_number, command):
        """
        Enables or disables sending and receiving SMS for the phone number.

        :rtype: dict
        """
        params = dict()

        params['phone_number'] = phone_number

        params['command'] = command

        res = await self._perform_request('ControlSms', params)
        if 'error' in res:
            raise VoximplantException(res['error']['msg'], res['error']['code'])

        return res

    async def __add_rule(self, rule_name, rule_pattern, application_id=None, application_name=None,
                         rule_pattern_exclude=None, video_conference=None, scenario_id=None, scenario_name=None):
        """
        Adds a new rule for the application.

        :rtype: dict
        """
        params = dict()

        passed_args = []
        if application_id is not None:
            passed_args.append('application_id')
        if application_name is not None:
            passed_args.append('application_name')

        if len(passed_args) > 1:
            raise VoximplantException(", ".join(passed_args) + " passed simultaneously into add_rule")
        if len(passed_args) == 0:
            raise VoximplantException("None of application_id, application_name passed into add_rule")

        passed_args = []
        if scenario_id is not None:
            passed_args.append('scenario_id')
        if scenario_name is not None:
            passed_args.append('scenario_name')

        if len(passed_args) > 1:
            raise VoximplantException(", ".join(passed_args) + " passed simultaneously into add_rule")
        if len(passed_args) == 0:
            raise VoximplantException("None of scenario_id, scenario_name passed into add_rule")

        params['rule_name'] = rule_name

        params['rule_pattern'] = rule_pattern

        if application_id is not None:
            params['application_id'] = application_id

        if application_name is not None:
            params['application_name'] = application_name

        if rule_pattern_exclude is not None:
            params['rule_pattern_exclude'] = rule_pattern_exclude

        if video_conference is not None:
            params['video_conference'] = video_conference

        if scenario_id is not None:
            params['scenario_id'] = self.__voximplant._serialize_list(scenario_id)

        if scenario_name is not None:
            params['scenario_name'] = self.__voximplant._serialize_list(scenario_name)

        res = await self._perform_request('AddRule', params)
        if 'error' in res:
            raise VoximplantException(res['error']['msg'], res['error']['code'])

        return res

    async def __bind_phone_number_to_application(self, phone_id=None, phone_number=None, application_id=None,
                                                 application_name=None, rule_id=None, rule_name=None, bind=None):
        """
        Bind the phone number to the application or unbind the phone number from the application.
        You should specify the application_id or application_name if you specify the rule_name.

        :rtype: dict
        """
        params = dict()

        passed_args = []
        if phone_id is not None:
            passed_args.append('phone_id')
        if phone_number is not None:
            passed_args.append('phone_number')

        if len(passed_args) > 1:
            raise VoximplantException(
                ", ".join(passed_args) + " passed simultaneously into bind_phone_number_to_application")
        if len(passed_args) == 0:
            raise VoximplantException("None of phone_id, phone_number passed into bind_phone_number_to_application")

        passed_args = []
        if application_id is not None:
            passed_args.append('application_id')
        if application_name is not None:
            passed_args.append('application_name')

        if len(passed_args) > 1:
            raise VoximplantException(
                ", ".join(passed_args) + " passed simultaneously into bind_phone_number_to_application")
        if len(passed_args) == 0:
            raise VoximplantException(
                "None of application_id, application_name passed into bind_phone_number_to_application")

        passed_args = []
        if rule_id is not None:
            passed_args.append('rule_id')
        if rule_name is not None:
            passed_args.append('rule_name')

        if len(passed_args) > 1:
            raise VoximplantException(
                ", ".join(passed_args) + " passed simultaneously into bind_phone_number_to_application")

        if phone_id is not None:
            params['phone_id'] = self.__voximplant._serialize_list(phone_id)

        if phone_number is not None:
            params['phone_number'] = self.__voximplant._serialize_list(phone_number)

        if application_id is not None:
            params['application_id'] = application_id

        if application_name is not None:
            params['application_name'] = application_name

        if rule_id is not None:
            params['rule_id'] = rule_id

        if rule_name is not None:
            params['rule_name'] = rule_name

        if bind is not None:
            params['bind'] = bind

        res = await self._perform_request('BindPhoneNumberToApplication', params)
        if 'error' in res:
            raise VoximplantException(res['error']['msg'], res['error']['code'])

        return res

    async def __attach_phone_number(self, country_code, phone_category_name, phone_region_id, phone_count=None,
                                    phone_number=None, country_state=None, regulation_address_id=None):
        """
        Attach the phone number to the account. Note that phone numbers of some countries may require additional verification steps.

        :rtype: dict
        """
        params = dict()

        passed_args = []
        if phone_count is not None:
            passed_args.append('phone_count')
        if phone_number is not None:
            passed_args.append('phone_number')

        if len(passed_args) > 1:
            raise VoximplantException(", ".join(passed_args) + " passed simultaneously into attach_phone_number")
        if len(passed_args) == 0:
            raise VoximplantException("None of phone_count, phone_number passed into attach_phone_number")

        params['country_code'] = country_code

        params['phone_category_name'] = phone_category_name

        params['phone_region_id'] = phone_region_id

        if phone_count is not None:
            params['phone_count'] = phone_count

        if phone_number is not None:
            params['phone_number'] = self.__voximplant._serialize_list(phone_number)

        if country_state is not None:
            params['country_state'] = country_state

        if regulation_address_id is not None:
            params['regulation_address_id'] = regulation_address_id

        res = await self._perform_request('AttachPhoneNumber', params)
        if 'error' in res:
            raise VoximplantException(res['error']['msg'], res['error']['code'])

        return res

    async def __get_new_phone_numbers(self, country_code, phone_category_name, phone_region_id, country_state=None,
                                      count=None, offset=None):
        """
        Gets the new phone numbers.

        :rtype: dict
        """
        params = dict()

        params['country_code'] = country_code

        params['phone_category_name'] = phone_category_name

        params['phone_region_id'] = phone_region_id

        if country_state is not None:
            params['country_state'] = country_state

        if count is not None:
            params['count'] = count

        if offset is not None:
            params['offset'] = offset

        res = await self._perform_request('GetNewPhoneNumbers', params)
        if 'error' in res:
            raise VoximplantException(res['error']["msg"], res['error']['code'])
        if 'result' in res:
            for p in res['result']:
                self.__voximplant._preprocess_new_phone_info_type(p)
        return res

    async def __start_scenarios(self, rule_id, user_id=None, user_name=None, application_id=None,
                                application_name=None, script_custom_data=None, reference_ip=None):
        """
        Runs JavaScript scenarios on a Voximplant server. The scenarios run in a new media session.

        :rtype: dict
        """
        params = dict()

        passed_args = []
        if user_id is not None:
            passed_args.append('user_id')
        if user_name is not None:
            passed_args.append('user_name')

        if len(passed_args) > 1:
            raise VoximplantException(", ".join(passed_args) + " passed simultaneously into start_scenarios")

        passed_args = []
        if application_id is not None:
            passed_args.append('application_id')
        if application_name is not None:
            passed_args.append('application_name')

        if len(passed_args) > 1:
            raise VoximplantException(", ".join(passed_args) + " passed simultaneously into start_scenarios")

        params['rule_id'] = rule_id

        if user_id is not None:
            params['user_id'] = user_id

        if user_name is not None:
            params['user_name'] = user_name

        if application_id is not None:
            params['application_id'] = application_id

        if application_name is not None:
            params['application_name'] = application_name

        if script_custom_data is not None:
            params['script_custom_data'] = script_custom_data

        if reference_ip is not None:
            params['reference_ip'] = reference_ip

        res = await self._perform_request('StartScenarios', params)
        if 'error' in res:
            raise VoximplantException(res['error']['msg'], res['error']['code'])

        return res

    async def _perform_request(self, cmd, args) -> dict:
        params = args.copy()
        params['cmd'] = cmd
        headers = {'Authorization': self.__voximplant.build_auth_header()}
        OK_STATUS = 200
        # todo: store session in field
        async with aiohttp.ClientSession() as session:
            async with session.post('https://{}/platform_api'.format(self.__voximplant.endpoint),
                                    data=params, headers=headers) as response:
                if response.status != OK_STATUS:
                    raise VoximplantException(response.status)
                return await response.json()


async def run() -> None:
    import config
    if config.VOX_CREDENTIALS is None:
        logging.error("No Voximplant credentials found in env. Stopping.")
        return

    global client
    
    # todo: move variables to config
    application_name = 'busy-prod.levashov.n4.voximplant.com'
    outbound_call_rule_id = '3602168'
    test = True
    if test:
        application_name = 'phone-assistant.levashov.n4.voximplant.com'
        outbound_call_rule_id = '3584070'

    client = VoximplantAsyncApi(config.VOX_CREDENTIALS, application_name, outbound_call_rule_id)

    # No cleanup and no background task, so we can exit immediately


__all__ = [
    "run",
    "client",
]
