import typing
from dataclasses import dataclass
import copy

from .. import pymorphy2


@dataclass
class Voice:
    id: str
    name: str


ASSISTANT_VOICES: typing.Dict[str, Voice] = {
    "default_female": Voice("default_female", "Стандартный женский"),
    "default_male": Voice("default_male", "Стандартный мужской")
}


ASSISTANT_REPLICAS = {
  "VOX_CANT_HEAR_GOODBYE": {"text": "Не могу разобрать, что вы говорите. Попробуйте перезвонить"},
  "VOX_CANT_HEAR_LIST": [{"text": "Вас не слышно"}, {"text": "Я вас не слышу"}, {"text": "Говорите громче"}],
  "VOX_FIRST_REPLICA": {"text": "Алло"},
  "VOX_WELCOME_LIST": [{"text": "Снова привет"}, {"text": "Здравствуйте"}, {"text": "Доброго времени суток"}],
  "VOX_WHO_ARE_YOU_LIST": [{"text": "Я голосовой помощник. Я передам всё, что Вы скажете"}],
  "VOX_CALL_USER_LIST": [{"text": "Не могу, он сейчас занят. Я всё передам"}],
  "VOX_ORDER_LIST": [{"text": "Я робот и не могу дать подтверждение"}],
  "VOX_SPAM_LIST": [{"text": "Уважаемый, вы не ошиблись? Это прокуратура"}, {"text": "Ваше предложение выглядит не очень полезным, но я всё равно его передам. До свидания"}],
  "VOX_DELIVERY_LIST": [{"text": "Извините, но я всего лишь помощник. Давайте я передам $USER_DATV, что вы звонили, и он вам перезвонит?"}],
  "VOX_BANK_LIST": [{"text": "Статья 159.3 УК РФ предусматривает уголовную ответственность за мошенничество с использованием платежных карт и наказание в виде лишения свободы на срок до десяти лет со штрафом в размере до одного миллиона рублей"}, {"text": "Отлично, я как раз существую для защиты от телефонных мошенников"}],
  "VOX_GOODBYE_LIST": [{"text": "До свидания!"}, {"text": "Пока"}],
  "VOX_RECRUITMENT_OFFICE_LIST": [{"text": "Я передам $USER_DATV, что вы звонили. До свидания"}],
  "VOX_UNKNOWN_LIST": [{"text": "Окей, я всё передам"}, {"text": "Хорошо, я всё передам"}],
  "VOX_BUSY": {"text": "Извините, но $USER_NOMN сейчас занят. Я передам, что вы звонили. Хотите ли вы передать что-нибудь еще?"},
  "VOX_BUSY_GOODBYE_LIST": [{"text": "Спасибо, хорошего дня"}],
  "VOX_BUSY_UNKNOWN_LIST": [{"text": "Всё записано"}, {"text": "Хотите ещё что-нибудь передать?"}],
  "VOX_RECALL": {"text": "К сожалению, $USER_NOMN сейчас занят. Я передам, что вы звонили, и он вам перезвонит"},
  "VOX_BUSY_CANT_HEAR_LIST": [{"text": "Один момент, пожалуйста"}, {"text": "Подождите еще немного, уточняю"}],
  "VOX_END_LOOP_GOODBYE_LIST": [{"text": "До свидания!"}, {"text": "Пока"}, {"text": "Приятно было пообщаться"}, {"text": "Всего доброго"}],
  "VOX_END_LOOP_UNKNOWN_LIST": [{"text": "Хорошо, я всё записываю"}, {"text": "Хотите передать что-нибудь ещё"}, {"text": "Я слушаю"},
                      {"text": "Вы можете рассказать мне всё, а я передам"}, {"text": "Может что-то ещё?"}],
  "VOX_LONG_CALL": {"text": "Слишком долгий звонок. До свидания"},
  "VOX_CONNECTION": {"text": "Соединяю с $USER_ABLT"},
  "VOX_CONNECTION_FAILED": {"text": "Не получилось дозвониться. До свидания"}
}


def generate_replicas_customization(name: str):
    try:
        nomn, datv, ablt = pymorphy2.inflect_phrase(name)
    except Exception as e:
        nomn, datv, ablt = name
    
    def customize(orig):
        customization = orig.replace("$USER_NOMN", nomn.capitalize())
        customization = customization.replace("$USER_DATV", datv.capitalize())
        customization = customization.replace("$USER_ABLT", ablt.capitalize())
        return customization

    replicas_override = copy.deepcopy(ASSISTANT_REPLICAS)
    for replica in replicas_override.values():
        if isinstance(replica, list):
            for replica_variant in replica:
                replica_variant["text"] = customize(replica_variant["text"])
        else:
            replica["text"] = customize(replica["text"])

    return replicas_override
