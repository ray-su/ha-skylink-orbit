"""Presence-aware device models for the recovered door families."""

from dataclasses import dataclass
from typing import Any


def device_family(model: object) -> str:
    """Classify only the families established by the recovered protocol."""
    return next(
        (
            name
            for name in ("GDO", "NOVA", "NVMini")
            if isinstance(model, str) and model.startswith(name)
        ),
        "unsupported",
    )


@dataclass(frozen=True)
class Door:
    hub_id: str
    channel: str | None
    name: str
    hub_name: str
    model: str
    state: int | None
    online: bool

    @property
    def key(self) -> tuple[str, str | None]:
        return self.hub_id, self.channel


@dataclass(frozen=True)
class Hub:
    """Reported hub-level telemetry, shared by all of its door channels."""

    hub_id: str
    name: str
    model: str
    online: bool
    light: bool | None
    rssi: int | None


def hubs_from_devices(devices: dict[str, dict[str, Any]]) -> list[Hub]:
    hubs = []
    for hub_id, device in devices.items():
        model = device.get("type")
        if not isinstance(model, str) or device_family(model) == "unsupported":
            continue
        reported = device.get("reported")
        mdev = reported.get("mdev") if isinstance(reported, dict) else None
        mdev = mdev if isinstance(mdev, dict) else {}
        light = mdev.get("light")
        rssi = mdev.get("rssi")
        online = device.get("online")
        hubs.append(
            Hub(
                hub_id,
                str(device.get("hub_name") or "Garage"),
                model,
                online is True or type(online) is int and online == 1,
                bool(light) if type(light) is int and light in (0, 1) else None,
                rssi if type(rssi) is int and -127 <= rssi < 0 else None,
            )
        )
    return hubs


def doors_from_devices(devices: dict[str, dict[str, Any]]) -> list[Door]:
    doors = []
    for hub_id, device in devices.items():
        model = device.get("type", "")
        if not isinstance(model, str):
            continue
        reported = device.get("reported")
        reported = reported if isinstance(reported, dict) else {}
        mdev = reported.get("mdev")
        mdev = mdev if isinstance(mdev, dict) else {}
        online = device.get("online")
        hub_name = device.get("hub_name") or "Garage"
        channels: list[tuple[str | None, str | None]]
        family = device_family(model)
        if family == "NOVA":
            channels = [
                (channel, field)
                for channel, field in (("A", "firstdoor"), ("B", "seconddoor"))
                if device.get(f"{field}_name")
            ]
        elif family == "NVMini":
            channels = [("A", "firstdoor")]
        elif family == "GDO":
            channels = [(None, None)]
        else:
            continue
        for channel, field in channels:
            state_data = mdev.get(field, {}) if field else mdev
            state = state_data.get("door") if isinstance(state_data, dict) else None
            if type(state) is not int or state not in range(8):
                state = None
            name = (
                device.get(f"{field}_name") if field else hub_name
            ) or f"Door {channel}"
            doors.append(
                Door(
                    hub_id,
                    channel,
                    str(name),
                    str(hub_name),
                    model,
                    state,
                    online is True or type(online) is int and online == 1,
                )
            )
    return doors
