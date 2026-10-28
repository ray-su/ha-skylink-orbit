"""Signed HTTP authentication and account-scoped MQTT lifecycle."""

import asyncio
import hashlib
import json
import random
import re
import ssl
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiohttp
import aiomqtt
from aiomqtt.exceptions import MqttConnectError

from .errors import (
    AuthenticationError,
    CannotConnect,
    CommandRejected,
    LoginRejected,
    ProtocolError,
)
from .models import Door, device_family, doors_from_devices

LOGIN_URL = "https://iot.skyhm.net:8444/skylinkhub_crm/skyhm_api_s.jsp"
# Public protocol constant embedded in Orbit Home, not an account credential.
SIGNING_SUFFIX = "8uHDSF77ueRmLlKkl67"
_DEFAULT_TLS = object()


def _merge(target: dict[str, Any], update: dict[str, Any]) -> None:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


def mqtt_tls_context() -> ssl.SSLContext:
    """Supplement normal trust with the public CA chain shipped by Orbit."""
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=str(Path(__file__).with_name("mqtt-ca.pem")))
    return context


class OrbitClient:
    """One Orbit account. The caller owns the injected HTTP session."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        *,
        login_url: str = LOGIN_URL,
        timeout: float = 20,
        broker_host: str = "iot.skyhm.net",
        broker_port: int = 1899,
        tls_context: Any = _DEFAULT_TLS,
        reconnect_delay: float = 1,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._login_url = login_url
        self._timeout = timeout
        self.account_id: str | None = None
        self.connected = False
        self.error: Exception | None = None
        self._host = broker_host
        self._port = broker_port
        self._tls = tls_context
        self._reconnect_delay = reconnect_delay
        self._identifier = "ha-orbit-" + uuid4().hex[:12]
        self._mqtt: aiomqtt.Client | None = None
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._devices: dict[str, dict[str, Any]] = {}
        self._listeners: set[Callable[[], None]] = set()
        self._pending: set[tuple[str, str | None]] = set()
        self._last_command: dict[tuple[str, str | None], float] = {}

    @property
    def doors(self) -> list[Door]:
        """Current door records, including unknown state when fields are absent."""
        return doors_from_devices(self._devices)

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Notify on state or connectivity changes; return an unsubscribe function."""
        self._listeners.add(callback)
        return lambda: self._listeners.discard(callback)

    def diagnostics(self) -> dict[str, Any]:
        """Allowlisted state only, suitable for sharing in an issue report."""
        families = {"GDO": 0, "NOVA": 0, "NVMini": 0, "unsupported": 0}
        for device in self._devices.values():
            families[device_family(device.get("type"))] += 1
        return {
            "connected": self.connected,
            "device_count": len(self._devices),
            "door_count": len(self.doors),
            "device_families": families,
            "doors": [
                {"channel": door.channel, "state": door.state, "online": door.online}
                for door in self.doors
            ],
            "error": type(self.error).__name__ if self.error else None,
        }

    def _notify(self) -> None:
        for callback in tuple(self._listeners):
            callback()

    async def start(self) -> None:
        """Log in and wait for the first snapshot, cleaning up on failure."""
        if self._task is not None:
            raise RuntimeError("Client already started")
        await self.login()
        if self._tls is _DEFAULT_TLS:
            self._tls = await asyncio.to_thread(mqtt_tls_context)
        self._ready.clear()
        self.error = None
        self._task = asyncio.create_task(self._run(), name="orbit-mqtt")
        try:
            async with asyncio.timeout(self._timeout):
                await self._ready.wait()
            if self.error is not None:
                raise self.error
        except TimeoutError as err:
            await self.close()
            raise CannotConnect("Initial snapshot timed out") from err
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        """Close owned MQTT resources; leave the injected HTTP session open."""
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self.connected = False
        self._mqtt = None

    async def _run(self) -> None:
        delay = self._reconnect_delay
        while True:
            try:
                await self._connection()
            except (aiomqtt.MqttError, OSError, TimeoutError) as err:
                rc = getattr(err, "rc", None)
                code = getattr(rc, "value", rc)
                if isinstance(err, MqttConnectError) and code in (4, 5, 134, 135):
                    self.error = AuthenticationError("MQTT authentication rejected")
                else:
                    self.error = CannotConnect("MQTT connection failed")
            finally:
                was_connected = self.connected
                self.connected = False
                self._mqtt = None
                self._notify()
            if not self._ready.is_set() or isinstance(self.error, AuthenticationError):
                self._ready.set()
                return
            if was_connected:
                delay = self._reconnect_delay
            await asyncio.sleep(delay * random.uniform(0.8, 1.2))
            delay = min(60, delay * 2)

    async def _connection(self) -> None:
        prefix = f"skylink/things/client/{self.account_id}"
        async with aiomqtt.Client(
            self._host,
            self._port,
            username=self.account_id,
            password=self._password,
            identifier=self._identifier,
            clean_session=True,
            keepalive=30,
            timeout=self._timeout,
            tls_context=self._tls,
            max_queued_incoming_messages=1024,
        ) as mqtt:
            async with asyncio.timeout(self._timeout) as deadline:
                await mqtt.subscribe(
                    [(prefix + "/get/result", 1), (prefix + "/update/result", 1)]
                )
                await mqtt.publish(prefix + "/get", "{}", qos=0, retain=False)
                self._mqtt = mqtt
                await self._consume(mqtt, prefix, deadline)

    async def _consume(
        self, mqtt: aiomqtt.Client, prefix: str, deadline: asyncio.Timeout
    ) -> None:
        buffered: list[dict[str, Any]] = []
        async for message in mqtt.messages:
            topic = str(message.topic)
            if topic not in (prefix + "/get/result", prefix + "/update/result"):
                continue
            try:
                data = json.loads(bytes(message.payload))["data"]
            except ValueError, TypeError, KeyError:
                continue
            if topic.endswith("/get/result"):
                if not isinstance(data, list) or self.connected:
                    continue
                self._devices = {
                    item["hub_id"]: item
                    for item in data
                    if isinstance(item, dict) and isinstance(item.get("hub_id"), str)
                }
                for update in buffered:
                    self._apply_update(update)
                buffered.clear()
                self.connected = True
                deadline.reschedule(None)
                self.error = None
                self._ready.set()
            elif isinstance(data, dict) and isinstance(data.get("hub_id"), str):
                if not self.connected:
                    if len(buffered) < 1000:
                        buffered.append(data)
                    continue
                self._apply_update(data)
            else:
                continue
            self._notify()

    def _apply_update(self, update: dict[str, Any]) -> None:
        hub_id = update["hub_id"]
        device = self._devices.setdefault(hub_id, {})
        _merge(device, update)
        reported = update.get("reported")
        link = reported.get("link") if isinstance(reported, dict) else None
        if isinstance(link, dict) and "online" in link:
            # Snapshots use top-level online; only push updates use link.online.
            device["online"] = link["online"]

    async def activate(self, hub_id: str, channel: str | None) -> None:
        """Send one native pulse and await a reported state change, never retry."""
        key = (hub_id, channel)
        door = next((item for item in self.doors if item.key == key), None)
        mqtt = self._mqtt
        if not self.connected or mqtt is None or door is None or not door.online:
            raise CommandRejected("Door is unavailable")
        if (
            key in self._pending
            or time.monotonic() - self._last_command.get(key, float("-inf")) < 2
        ):
            raise CommandRejected("An activation is already pending; check the door")
        changed = asyncio.Event()
        unsubscribe = self.subscribe(changed.set)
        self._pending.add(key)
        self._last_command[key] = time.monotonic()
        control: dict[str, Any] = {"cmd": 0, "ts": str(int(time.time() * 1000))}
        if channel is not None:
            control["position"] = channel
        payload = {
            "data": {"hub_id": hub_id, "desired": {"mdev": {"ctrlgdo": control}}}
        }
        try:
            async with asyncio.timeout(self._timeout):
                await mqtt.publish(
                    f"skylink/things/client/{self.account_id}/desire",
                    json.dumps(payload),
                    qos=0,
                    retain=False,
                )
                while True:
                    await changed.wait()
                    changed.clear()
                    if not self.connected or self._mqtt is not mqtt:
                        raise CommandRejected(
                            "Connection lost; activation outcome unknown. Check the door"
                        )
                    current = next(
                        (item for item in self.doors if item.key == key), None
                    )
                    if current is None or not current.online:
                        raise CommandRejected(
                            "Door unavailable; activation outcome unknown. Check the door"
                        )
                    if current.state not in (None, 7) and current.state != door.state:
                        return
        except (aiomqtt.MqttError, TimeoutError) as err:
            raise CommandRejected(
                "Activation outcome unknown. Check the door before trying again"
            ) from err
        finally:
            self._pending.discard(key)
            unsubscribe()

    async def login(self) -> str:
        """Authenticate without logging vendor responses or account data."""
        timestamp = str(int(time.time()) * 1000)
        signature = hashlib.md5(
            f"{timestamp}+act_login+{self._username}+{SIGNING_SUFFIX}".encode(),
            usedforsecurity=False,
        ).hexdigest()
        headers = {
            "REQ-TIMESTAMP": timestamp,
            "REQ-CMD": "act_login",
            "REQ-DATA": self._username,
            "REQ-SIGNATURE": signature,
            "Content-Type": "application/json; charset=utf-8",
        }
        body = {
            "username": self._username,
            "password": self._password,
            "app_sys": "gcm",
            "app_brand": "00",
            "pn_token": "",
        }
        try:
            async with self._session.post(
                self._login_url,
                params={"cmd": "act_login"},
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=self._timeout),
                allow_redirects=False,
            ) as response:
                if response.status in (401, 403):
                    raise AuthenticationError("Authentication rejected")
                if response.status != 200:
                    raise CannotConnect("Login service unavailable")
                raw = await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect("Login connection failed") from err
        try:
            data = json.loads(re.sub(r'("result"\s*:\s*)00(?=\s*[,}])', r"\g<1>0", raw))
        except (ValueError, TypeError) as err:
            raise ProtocolError("Invalid login response") from err
        if not isinstance(data, dict) or type(data.get("result")) is not int:
            raise ProtocolError("Missing login result")
        if data["result"] != 0:
            # The app does not establish which result codes mean bad credentials.
            raise LoginRejected("Login rejected by Orbit")
        account_id = data.get("acc_no")
        if not isinstance(account_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]+", account_id
        ):
            raise ProtocolError("Invalid account identifier")
        if self.account_id is not None and self.account_id != account_id:
            raise AuthenticationError("Account changed")
        self.account_id = account_id
        return account_id
