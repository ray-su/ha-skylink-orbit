"""Final-review probes only; no production edits or external device access."""

from contextlib import suppress

import pytest

from custom_components.skylink_orbit.orbitclient.errors import CommandRejected

from .test_motion_safety import reconnect
from .test_review_regressions import update

pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.mark.parametrize(
    "scenario", ["same-door-noop", "other-door-refresh", "idle-reconnect"]
)
async def test_snapshot_cannot_launder_known_open_into_open_authorization(
    orbit_client, broker, scenario
):
    refresh_other_door = scenario == "other-door-refresh"
    if refresh_other_door:
        broker.snapshot.append(
            {
                "hub_id": "other",
                "type": "GDO",
                "online": True,
                "reported": {"mdev": {"door": 4}},
            }
        )
    await orbit_client.start()
    await broker.published.get()
    # Real pushed evidence is Open; the simulated cached cloud snapshot stays Closed.
    await update(orbit_client, broker, 1)
    assert next(d.state for d in orbit_client.doors if d.hub_id == "hub-test") == 1
    # A no-op Close (same door or another door) must not erase the contradiction
    # and thereby authorize a later Open pulse from the stale Closed snapshot.
    if scenario == "idle-reconnect":
        await reconnect(orbit_client, broker)
    else:
        with suppress(CommandRejected):
            await orbit_client.set_closed(
                "other" if refresh_other_door else "hub-test", None, True
            )
        assert (await broker.published.get())[0].endswith("/get")
    assert broker.published.empty()
    with suppress(CommandRejected):
        await orbit_client.set_closed("hub-test", None, False)
    emitted = []
    while not broker.published.empty():
        emitted.append((await broker.published.get())[0])
    assert not any(topic.endswith("/desire") for topic in emitted), (
        "OPEN emitted a pulse after an earlier no-op/account snapshot erased the newer pushed OPEN report"
    )
