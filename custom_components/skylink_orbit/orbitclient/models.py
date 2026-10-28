"""Presence-aware device models for the recovered door families."""

from dataclasses import dataclass
from typing import Any


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
        link = reported.get("link")
        online = (
            link.get("online")
            if isinstance(link, dict) and "online" in link
            else device.get("online")
        )
        hub_name = device.get("hub_name") or "Garage"
        channels: list[tuple[str | None, str | None]]
        if model.startswith("NOVA"):
            channels = [
                (channel, field)
                for channel, field in (("A", "firstdoor"), ("B", "seconddoor"))
                if device.get(f"{field}_name")
            ]
        elif model.startswith("NVMini"):
            channels = [("A", "firstdoor")]
        elif model.startswith("GDO"):
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
