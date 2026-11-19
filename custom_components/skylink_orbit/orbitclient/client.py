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
from dataclasses import dataclass, field, replace
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
from .models import Door, Hub, device_family, doors_from_devices, hubs_from_devices

LOGIN_URL = "https://iot.skyhm.net:8444/skylinkhub_crm/skyhm_api_s.jsp"
# Public protocol constant embedded in Orbit Home, not an account credential.
SIGNING_SUFFIX = "8uHDSF77ueRmLlKkl67"
_DEFAULT_TLS = object()
_UPDATE_LIMIT = 1000


@dataclass
class DiscoveryHistory:
    """In-memory evidence only. No client, credentials, tasks or listeners."""

    devices: dict[str, dict[str, Any]] = field(default_factory=dict)
    conflicts: set[str] = field(default_factory=set)
    lost: bool = False


@dataclass
class _Motion:
    """Protect a sent pulse independently of its awaiting caller."""

    target: int | None
    progress: bool = False
    completed: bool = False
    uncertain: bool = False


def _merge(target: dict[str, Any], update: dict[str, Any]) -> None:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


def _motion_report(device: dict[str, Any]) -> tuple[Any, ...]:
    """Compare motion evidence even when a model temporarily stops yielding doors."""
    reported = device.get("reported")
    mdev = reported.get("mdev") if isinstance(reported, dict) else None
    mdev = mdev if isinstance(mdev, dict) else {}

    def state(data: Any) -> int | None:
        raw = data.get("door") if isinstance(data, dict) else None
        return raw if type(raw) is int and raw in range(8) else None

    model = device.get("type")
    model = model if isinstance(model, str) else None
    online = device.get("online")
    channels = (
        (bool(device.get("firstdoor_name")), bool(device.get("seconddoor_name")))
        if device_family(model) == "NOVA"
        else ()
    )
    return (
        ("type" in device, model),
        online is True or type(online) is int and online == 1,
        state(mdev),
        state(mdev.get("firstdoor")),
        state(mdev.get("seconddoor")),
        channels,
    )


def mqtt_tls_context() -> ssl.SSLContext:
    """Supplement normal trust with the public CA chain shipped by Orbit."""
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=str(Path(__file__).with_name("mqtt-ca.pem")))
    return context


def _partial_evidence(device: dict[str, Any]) -> dict[str, int | bool | None]:
    """Before model discovery, compare only fields actually observed, never defaults."""
    evidence: dict[str, int | bool | None] = {}
    if "online" in device:
        value = device["online"]
        evidence["online"] = value is True or type(value) is int and value == 1
    reported = device.get("reported", {})
    mdev = reported.get("mdev", {}) if isinstance(reported, dict) else None
    for channel in ("single", "firstdoor", "seconddoor"):
        node = (
            mdev
            if channel == "single" or not isinstance(mdev, dict)
            else mdev.get(channel, {})
        )
        if not isinstance(node, dict) or "door" in node:
            raw = node.get("door") if isinstance(node, dict) else None
            evidence[channel] = raw if type(raw) is int and raw in range(8) else None
    return evidence


def _loss_aware_queue(
    on_loss: Callable[[], None],
) -> type[asyncio.Queue[aiomqtt.Message]]:
    class IncomingQueue(asyncio.Queue[aiomqtt.Message]):
        def put_nowait(self, item: aiomqtt.Message) -> None:
            try:
                super().put_nowait(item)
            except asyncio.QueueFull:
                on_loss()
                raise

    return IncomingQueue


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
        history: DiscoveryHistory | None = None,
        expected_account_id: str | None = None,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._login_url = login_url
        self._timeout = timeout
        self.account_id: str | None = None
        self._expected_account_id = expected_account_id
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
        self._history = history if history is not None else DiscoveryHistory()
        self._devices = self._history.devices
        self._listeners: set[Callable[[], None]] = set()
        self._pending: set[tuple[str, str | None]] = set()
        self._last_command: dict[tuple[str, str | None], float] = {}
        self._pending_lights: set[str] = set()
        self._snapshot_lock = asyncio.Lock()
        self._snapshot_ready: asyncio.Event | None = None
        self._snapshot_updates: list[dict[str, Any]] = []
        self._snapshot_overflow = False
        self._snapshot_uncertain = False

        # Separate from the replaceable cache: snapshots cannot erase conflicts.
        # Hub-scoped so a changed model/channel mapping cannot bypass protection.
        self._snapshot_conflicts = self._history.conflicts
        self._motions: dict[tuple[str, str | None], _Motion] = {}

    @property
    def doors(self) -> list[Door]:
        """Current door records, including unknown state when fields are absent."""
        return [
            replace(door, position_issue=self._position_issue(door))
            for door in doors_from_devices(self._devices)
        ]

    def _position_issue(self, door: Door) -> str | None:
        if self._history.lost:
            return "history_lost"
        if door.hub_id in self._snapshot_conflicts:
            return "conflicting_reports"
        if self._snapshot_uncertain:
            return "ordering_uncertain"
        motion = self._motions.get(door.key)
        return "motion_uncertain" if motion and motion.uncertain else None

    def _mark_history_lost(self) -> None:
        if not self._history.lost:
            self._history.lost = True
            self._notify()

    @property
    def hubs(self) -> list[Hub]:
        """Hub telemetry from existing snapshots/pushes; never fetch on access."""
        return hubs_from_devices(self._devices)

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
                {
                    "channel": door.channel,
                    "state": door.state,
                    "online": door.online,
                    "position_issue": door.position_issue,
                }
                for door in self.doors
            ],
            "hubs": [
                {"light": hub.light, "rssi": hub.rssi, "online": hub.online}
                for hub in self.hubs
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
        if (
            self._expected_account_id is not None
            and self.account_id != self._expected_account_id
        ):
            raise AuthenticationError("Account changed")
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
                for motion in self._motions.values():
                    motion.uncertain = True
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
            queue_type=_loss_aware_queue(self._mark_history_lost),
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
            if getattr(message, "retain", False):
                # Broker replay at subscribe is neither a solicited snapshot
                # nor a new device report. Do not use it to authorize motion.
                continue
            try:
                data = json.loads(bytes(message.payload))["data"]
            except ValueError, TypeError, KeyError:
                if topic.endswith("/update/result"):
                    self._mark_history_lost()
                continue
            if topic.endswith("/get/result"):
                if not isinstance(data, list) or (
                    self.connected
                    and (self._snapshot_ready is None or self._snapshot_ready.is_set())
                ):
                    continue
                refreshing = self.connected
                updates = self._snapshot_updates if refreshing else buffered

                # Push evidence is retained as received, even if a discovery
                # session ends before its first snapshot. The bounded replay log
                # overlays pushes onto the candidate, not onto prior evidence.
                previous = {
                    hub_id: _motion_report(device)
                    for hub_id, device in self._devices.items()
                    if "type" in device
                }
                partial = {
                    hub_id: _partial_evidence(device)
                    for hub_id, device in self._devices.items()
                    if "type" not in device
                }
                self._devices.clear()
                for item in data:
                    if not isinstance(item, dict) or not isinstance(
                        item.get("hub_id"), str
                    ):
                        self._mark_history_lost()
                        continue
                    hub_id = item["hub_id"]
                    if hub_id in self._devices and _motion_report(
                        self._devices[hub_id]
                    ) != _motion_report(item):
                        self._snapshot_conflicts.add(hub_id)
                    self._devices[hub_id] = item
                for hub_id, observed in partial.items():
                    candidate = _partial_evidence(self._devices.get(hub_id, {}))
                    if not observed.items() <= candidate.items():
                        self._snapshot_conflicts.add(hub_id)
                for update in updates:
                    self._apply_update(update)
                for hub_id, before in previous.items():
                    after = self._devices.get(hub_id)
                    if after is None or before != _motion_report(after):
                        self._snapshot_conflicts.add(hub_id)
                buffered.clear()
                self._snapshot_updates.clear()
                if self._snapshot_ready is not None:
                    self._snapshot_ready.set()
                self.connected = True
                deadline.reschedule(None)
                self.error = None
                self._ready.set()
            elif isinstance(data, dict) and isinstance(data.get("hub_id"), str):
                if not self.connected:
                    if len(buffered) < _UPDATE_LIMIT:
                        buffered.append(data)
                        self._apply_update(data)
                    else:
                        self._mark_history_lost()
                    continue
                if self._snapshot_ready is not None:
                    if len(self._snapshot_updates) < _UPDATE_LIMIT:
                        self._snapshot_updates.append(data)
                    else:
                        self._snapshot_overflow = True
                        self._mark_history_lost()
                self._apply_update(data)
                self._observe_motion(data)
            else:
                # A delivered update that cannot be attributed to a hub is
                # missing position history, not evidence that nothing changed.
                self._mark_history_lost()
                continue
            self._notify()

    def _apply_update(self, update: dict[str, Any]) -> None:
        hub_id = update["hub_id"]
        device = self._devices.setdefault(hub_id, {})
        old_model = device.get("type")
        _merge(device, update)
        if old_model is not None and device.get("type") != old_model:
            # Channel selectors depend on the model. A push must not remap a
            # guarded single door to a new actionable A/B key on the same hub.
            self._snapshot_conflicts.add(hub_id)
        reported = update.get("reported")
        link = reported.get("link") if isinstance(reported, dict) else None
        if isinstance(link, dict) and "online" in link:
            # Snapshots use top-level online; only push updates use link.online.
            device["online"] = link["online"]

    def _observe_motion(self, update: dict[str, Any]) -> None:
        """Only explicit pushed door reports, never snapshots, release interlocks."""
        if not self._motions:
            return
        reported = update.get("reported")
        mdev = reported.get("mdev") if isinstance(reported, dict) else None
        doors = {door.key: door for door in self.doors}
        for key, motion in list(self._motions.items()):
            if key[0] != update["hub_id"] or motion.uncertain:
                continue
            door = doors.get(key)
            if door is None or not door.online:
                motion.uncertain = True
                continue
            if not isinstance(mdev, dict):
                continue
            field = {"A": "firstdoor", "B": "seconddoor"}.get(key[1] or "")
            state_data = mdev.get(field) if field else mdev
            if not isinstance(state_data, dict) or "door" not in state_data:
                continue
            state = door.state
            if motion.target is None:
                if state in (0, 3):
                    motion.target = 1 if state == 0 else 4
                    motion.progress = True
                else:
                    motion.uncertain = True
            elif state == motion.target:
                motion.completed = True
                del self._motions[key]
            elif state in ((3, 5) if motion.target == 4 else (0,)):
                motion.progress = True
            elif state != (1 if motion.target == 4 else 4) or motion.progress:
                motion.uncertain = True

    async def _refresh_for_motion(self, mqtt: aiomqtt.Client) -> None:
        """Refresh once; ambiguous replies require manual inspection and reload."""
        if self._snapshot_lock.locked():
            raise CommandRejected("Status refresh busy; no pulse sent")
        async with self._snapshot_lock:
            if self._snapshot_uncertain:
                raise CommandRejected(
                    "Status reply ordering uncertain; inspect door and reload integration"
                )
            if not self.connected or self._mqtt is not mqtt:
                raise CommandRejected("Connection changed; request rejected")
            ready = self._snapshot_ready = asyncio.Event()
            self._snapshot_updates.clear()
            self._snapshot_overflow = False
            verified = False
            try:
                async with asyncio.timeout(self._timeout):
                    await mqtt.publish(
                        f"skylink/things/client/{self.account_id}/get",
                        "{}",
                        qos=0,
                        retain=False,
                    )
                    await ready.wait()
                if not self.connected or self._mqtt is not mqtt:
                    raise CommandRejected("Connection changed; request rejected")
                if self._snapshot_overflow:
                    raise CommandRejected(
                        "Too many updates to verify state; no pulse sent"
                    )
                verified = True
            except (aiomqtt.MqttError, TimeoutError) as err:
                raise CommandRejected(
                    "Cannot verify current door state; no pulse sent"
                ) from err
            finally:
                if not verified:
                    # Without request IDs, late replies cannot be safely attributed.
                    # Automatic reconnect deliberately does not reset this interlock.
                    self._snapshot_uncertain = True
                    self._notify()
                self._snapshot_ready = None
                self._snapshot_updates.clear()

    async def activate(self, hub_id: str, channel: str | None) -> None:
        """Keep the existing explicit toggle button behavior."""
        await self._activate(hub_id, channel, closed=None)

    async def set_closed(self, hub_id: str, channel: str | None, closed: bool) -> None:
        """Guard a directional request using refreshed reported terminal state."""
        await self._activate(hub_id, channel, closed=closed)

    async def _activate(
        self, hub_id: str, channel: str | None, *, closed: bool | None
    ) -> None:
        """Reserve the channel before any await; send at most one native pulse."""
        key = (hub_id, channel)
        door = next((item for item in self.doors if item.key == key), None)
        mqtt = self._mqtt
        if not self.connected or mqtt is None or door is None or not door.online:
            raise CommandRejected("Door is unavailable")
        if closed is not None and device_family(door.model) != "GDO":
            raise CommandRejected(
                "Directional controls are only enabled for validated GDO devices"
            )
        if (
            key in self._pending
            or key in self._motions
            or time.monotonic() - self._last_command.get(key, float("-inf")) < 2
        ):
            raise CommandRejected(
                "An activation is pending or uncertain; wait for completion, or inspect door before reloading integration"
            )
        self._check_snapshot_conflict(hub_id)
        if closed is not None and door.state not in (1, 4):
            raise CommandRejected("Door is moving or ambiguous; no pulse sent")
        changed = asyncio.Event()
        unsubscribe = self.subscribe(changed.set)
        self._pending.add(key)
        control: dict[str, Any] = {"cmd": 0, "ts": str(int(time.time() * 1000))}
        if channel is not None:
            control["position"] = channel
        payload = {
            "data": {"hub_id": hub_id, "desired": {"mdev": {"ctrlgdo": control}}}
        }
        motion: _Motion | None = None
        try:
            if closed is not None:
                await self._refresh_for_motion(mqtt)
                door = next((item for item in self.doors if item.key == key), None)
                if door is None or not door.online:
                    raise CommandRejected("Door is unavailable; no pulse sent")
                if door.state not in (1, 4):
                    raise CommandRejected("Door is moving or ambiguous; no pulse sent")
                self._check_snapshot_conflict(hub_id)
                if door.state == (4 if closed else 1):
                    return
                changed.clear()
            self._last_command[key] = time.monotonic()
            control["ts"] = str(int(time.time() * 1000))
            target = (
                (4 if closed else 1)
                if closed is not None
                else (1 if door.state == 4 else 4 if door.state == 1 else None)
            )
            motion = self._motions[key] = _Motion(target)
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
                    self._check_snapshot_conflict(hub_id)
                    if closed is not None and motion.uncertain:
                        raise CommandRejected(
                            "Unexpected direction or ambiguous motion; inspect the door"
                        )
                    if closed is not None and (motion.progress or motion.completed):
                        return
                    if (
                        closed is None
                        and current.state not in (None, 7)
                        and current.state != door.state
                    ):
                        return
        except (aiomqtt.MqttError, TimeoutError) as err:
            if motion is not None and not motion.completed:
                motion.uncertain = True
            raise CommandRejected(
                "Activation outcome unknown. Check the door before trying again"
            ) from err
        except BaseException:
            if motion is not None and not motion.completed:
                motion.uncertain = True
            raise
        finally:
            self._pending.discard(key)
            unsubscribe()
            if motion is not None and motion.uncertain:
                self._notify()

    def _check_snapshot_conflict(self, hub_id: str) -> None:
        if self._history.lost:
            raise CommandRejected(
                "Door history incomplete; inspect doors and reload integration"
            )
        if self._snapshot_uncertain:
            raise CommandRejected(
                "Status reply ordering uncertain; inspect doors and reload integration"
            )
        if hub_id in self._snapshot_conflicts:
            raise CommandRejected(
                "Conflicting door reports; inspect door and reload integration"
            )

    async def set_light(self, hub_id: str, on: bool) -> None:
        """Send a GDO light command once; return means sent, not observed state."""
        hub = next((item for item in self.hubs if item.hub_id == hub_id), None)
        mqtt = self._mqtt
        if not self.connected or mqtt is None or hub is None or not hub.online:
            raise CommandRejected("Light is unavailable")
        if not hub.supports_light_commands:
            raise CommandRejected("Light commands are not established for this model")
        if hub_id in self._pending_lights:
            raise CommandRejected("A light command is already pending")
        self._pending_lights.add(hub_id)
        payload = {
            "data": {
                "hub_id": hub_id,
                "desired": {
                    "mdev": {
                        "ctrlgdo": {
                            "cmd": 3 if on else 4,
                            "ts": str(int(time.time() * 1000)),
                        }
                    }
                },
            }
        }
        try:
            async with asyncio.timeout(self._timeout):
                await mqtt.publish(
                    f"skylink/things/client/{self.account_id}/desire",
                    json.dumps(payload),
                    qos=0,
                    retain=False,
                )
                if not self.connected or self._mqtt is not mqtt:
                    raise CommandRejected("Connection lost; light outcome unknown")
        except (aiomqtt.MqttError, TimeoutError) as err:
            raise CommandRejected("Light command send failed; outcome unknown") from err
        finally:
            self._pending_lights.discard(hub_id)

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
