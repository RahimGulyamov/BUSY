from aiohttp import web
import json


def success(**kwargs) -> web.Response:
    return web.Response(status=200, text=json.dumps(kwargs, default=str))


def unauthorized(**kwargs) -> web.Response:
    return web.Response(status=400, text=json.dumps({'result': 'unauthorized'} | kwargs, default=str))


def bad_request(**kwargs):
    return web.Response(status=400, text=json.dumps({'result': 'badRequest'} | kwargs, default=str))


def too_many_requests(**kwargs):
    return web.Response(status=239, text=json.dumps({'result': 'tooManyRequests'} | kwargs, default=str))


def not_found(**kwargs):
    return web.Response(status=404, text=json.dumps({'result': 'notFound'} | kwargs, default=str))


def has_current_call(**kwargs):
    return web.Response(status=400, text=json.dumps({'result': 'busy'} | kwargs, default=str))


def is_ignored(**kwargs):
    return web.Response(status=400, text=json.dumps({'result': 'isIgnored'} | kwargs, default=str))
