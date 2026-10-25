"""Network adapters. No Qt dependencies; also usable by a future server runner."""
import json
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .rewards import twitch_limits


class ApiError(Exception):
    def __init__(self, service, status=0, code=""):
        self.service, self.status, self.code = service, status, code
        # Never display response bodies, URLs or authorization headers.
        detail = {401: "авторизация истекла — подключитесь заново",
                  403: "недостаточно прав или функция недоступна аккаунту",
                  404: "объект не найден", 429: "слишком много запросов"}.get(status)
        super().__init__(f"{service}: {detail or ('ошибка HTTP ' + str(status) if status else 'нет ответа от сервиса')}")


def request(service, url, method="GET", body=None, headers=None, form=False):
    headers = dict(headers or {})
    headers["Accept"] = "application/json"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        data = (urlencode(body) if form else json.dumps(body)).encode()
    try:
        with urlopen(Request(url, data=data, method=method, headers=headers), timeout=15) as response:
            payload = response.read()
            return json.loads(payload) if payload else None
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read())
            code = payload.get("message", payload.get("error", ""))
        except (ValueError, AttributeError):
            code = ""
        raise ApiError(service, exc.code, code) from None
    except (URLError, TimeoutError, socket.timeout, OSError, ValueError):
        raise ApiError(service) from None


class Twitch:
    SCOPE = "channel:manage:redemptions user:write:chat"

    def __init__(self, client_id, token, refresh_token="", on_refresh=None):
        self.client_id, self.token = client_id, token
        self.refresh_token = refresh_token
        self.on_refresh = on_refresh
        self.user_id = ""

    @staticmethod
    def device_start(client_id):
        return request("Twitch", "https://id.twitch.tv/oauth2/device", "POST",
                       {"client_id": client_id, "scopes": Twitch.SCOPE}, form=True)

    @staticmethod
    def device_poll(client_id, device_code):
        return request("Twitch", "https://id.twitch.tv/oauth2/token", "POST", {
            "client_id": client_id, "device_code": device_code, "scopes": Twitch.SCOPE,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code"}, form=True)

    def authorized_request(self, url, method="GET", body=None, validation=False):
        def send():
            headers = {"Authorization": f"{'OAuth' if validation else 'Bearer'} {self.token}",
                       "Client-Id": self.client_id}
            return request("Twitch", url, method, body, headers)

        try:
            return send()
        except ApiError as exc:
            if exc.status != 401 or not self.refresh_token:
                raise
        # Retry only a rejected authorization, never a timeout or an ambiguous write.
        result = request("Twitch", "https://id.twitch.tv/oauth2/token", "POST", {
            "client_id": self.client_id, "grant_type": "refresh_token",
            "refresh_token": self.refresh_token}, form=True)
        self.token, self.refresh_token = result["access_token"], result["refresh_token"]
        if self.on_refresh:
            self.on_refresh(self.token, self.refresh_token)
        return send()

    def validate(self):
        data = self.authorized_request("https://id.twitch.tv/oauth2/validate", validation=True)
        if data.get("client_id") != self.client_id or not set(self.SCOPE.split()).issubset(data.get("scopes", [])):
            raise ValueError("Войдите в Twitch через браузер заново: нужны права управления наградами и отправки сообщений (channel:manage:redemptions, user:write:chat).")
        self.user_id = data["user_id"]
        return data["login"]

    def call(self, path, method="GET", body=None, **query):
        query["broadcaster_id"] = self.user_id
        return self.authorized_request("https://api.twitch.tv/helix/channel_points/" + path + "?" + urlencode(query),
                                       method, body)

    def create_reward(self, title, cost, limits=None):
        result = self.call("custom_rewards", "POST", {
            "title": title, "cost": cost, "prompt": "Введите точный ник на MemeAlerts",
            "is_user_input_required": True, "is_enabled": False,
            "should_redemptions_skip_request_queue": False,
            **(twitch_limits(limits) if limits is not None else {})})
        return result["data"][0]["id"]

    def enable(self, reward_id, enabled):
        self.call("custom_rewards", "PATCH", {"is_enabled": enabled}, id=reward_id)

    def update_reward(self, reward_id, title, cost, limits=None):
        self.call("custom_rewards", "PATCH", {
            "title": title, "cost": cost, "is_user_input_required": True,
            "should_redemptions_skip_request_queue": False,
            **(twitch_limits(limits) if limits is not None else {})}, id=reward_id)

    def notify_not_supporter(self, login):
        # Only a Twitch login from redemption metadata is mentioned; viewer input is never echoed.
        import re
        if not re.fullmatch(r"[a-zA-Z0-9_]{1,25}", login):
            raise ValueError("В заявке отсутствует корректный логин Twitch для сообщения.")
        message = (f"@{login}, указанный ник не найден среди саппортеров этого канала на MemeAlerts. "
                   "Перейдите на https://memealerts.com/ и станьте саппортером канала. "
                   "Если вы уже саппортер, проверьте ник MemeAlerts и повторите получение награды. "
                   "Баллы канала возвращены.")
        result = self.authorized_request("https://api.twitch.tv/helix/chat/messages", "POST", {
            "broadcaster_id": self.user_id, "sender_id": self.user_id, "message": message})
        if not isinstance(result, dict) or not result.get("data") or result["data"][0].get("is_sent") is not True:
            raise ValueError("Twitch не подтвердил отправку сообщения в чат.")

    def redemptions(self, reward_id):
        cursor = None
        while True:
            query = {"reward_id": reward_id, "status": "UNFULFILLED", "first": 50, "sort": "OLDEST"}
            if cursor:
                query["after"] = cursor
            result = self.call("custom_rewards/redemptions", **query)
            yield from result["data"]
            cursor = result.get("pagination", {}).get("cursor")
            if not cursor:
                break

    def finish(self, reward_id, redemption_id, status):
        self.call("custom_rewards/redemptions", "PATCH", {"status": status},
                  reward_id=reward_id, id=redemption_id)


class NotSupporter(Exception):
    pass


class MemeAlerts:
    """Private website endpoints observed on 2026-10-24; may change."""

    def __init__(self, token):
        self.token, self.user_id = token, ""

    def call(self, path, method="GET", body=None):
        return request("MemeAlerts", "https://memealerts.com/api/" + path, method, body,
                       {"Authorization": f"Bearer {self.token}"})

    def validate(self):
        data = self.call("user/current")
        if not isinstance(data, dict) or not data.get("_id"):
            raise ValueError("MemeAlerts изменил формат профиля; подключение не подтверждено.")
        if not data.get("isStreamer"):
            raise ValueError("В MemeAlerts требуется аккаунт стримера.")
        self.user_id = data["_id"]
        return data.get("username") or data.get("name") or self.user_id

    def find_supporter(self, nickname):
        nickname = nickname.strip()
        if not nickname or len(nickname) > 100 or any(ord(c) < 32 for c in nickname):
            raise NotSupporter("Укажите точный ник MemeAlerts, от 1 до 100 символов.")
        skip, found = 0, {}
        while True:
            result = self.call("supporters", "POST", {
                "query": nickname, "streamerId": self.user_id, "limit": 100, "skip": skip})
            if not isinstance(result, dict) or not isinstance(result.get("data"), list):
                raise ValueError("MemeAlerts изменил формат списка саппортеров.")
            rows = result["data"]
            for row in rows:
                if str(row.get("supporterName", "")).casefold() == nickname.casefold() and row.get("supporterId"):
                    found[row["supporterId"]] = row
            skip += len(rows)
            if len(rows) < 100 or skip >= result.get("total", float("inf")):
                break
            if skip >= 10000:
                raise ValueError("Слишком много совпадений; уточните ник на MemeAlerts.")
        if len(found) != 1:
            raise NotSupporter("Саппортер с таким точным ником не найден или ник неоднозначен. Баллы возвращены.")
        return next(iter(found))

    def credit(self, user_id, amount):
        result = self.call("user/give-bonus", "POST", {
            "userId": user_id, "streamerId": self.user_id, "value": amount})
        if isinstance(result, dict) and (result.get("success") is False or result.get("error")):
            raise ValueError("MemeAlerts не подтвердил начисление.")
