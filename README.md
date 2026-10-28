# Skylink Orbit for Home Assistant

<img src="custom_components/skylink_orbit/brand/icon.png" width="96" alt="Garage icon">

An experimental custom integration for garage openers paired with **Orbit Home by Skylink**. It connects directly to the Orbit cloud; no local MQTT broker or Android phone is needed.

**Requires Home Assistant 2026.10.0 or newer.** Tested against simulated services and Home Assistant 2026.10.0. On 2026-10-28, the owner confirmed account setup, GDO discovery, door activation, and Opening/Open/Closing/Closed updates on their opener. The exact hardware model has not been recorded; Wi-Fi RSSI was also observed in HA. Other families, light commands from HA, and the frequency of Wi-Fi updates still need hardware validation. It is independent of Skylink and is not an official integration.

## What it provides

- Account setup and reauthentication through Home Assistant's UI.
- Live garage status for the GDO, NOVA A/B, and NVMini families found in the Android app. These are protocol-supported families, not a hardware-tested compatibility list.
- Separate doors under one physical device for dual-channel hubs.
- Separate **Light on** and **Light off** buttons for GDO openers, even when no light state is reported.
- One diagnostic **Wi-Fi signal** sensor per hub when it reports a valid RSSI value (dBm).
- Reconnection, fresh discovery after reconnect, availability, and sanitized diagnostics.
- An **Activate door** button that is **disabled by default**. The recovered command behaves like the app's main button; its exact physical behavior needs verification on your model.

The cover reports status and has no open, close, stop, or percentage-position actions. Partially open states appear as open; the `orbit_door_state` attribute preserves the original code. A close-delay state is unknown until actual motion or a terminal state arrives. Dimming, cameras, settings, and firmware updates are outside this release.

## Install with HACS

1. In HACS, open **Custom repositories** from its menu.
2. Add `https://github.com/ray-su/ha-skylink-orbit` with category **Integration**.
3. Find **Skylink Orbit**, download it, and restart Home Assistant.
4. Open **Settings → Devices & services → Add integration → Skylink Orbit**.
5. Enter your existing Orbit Home email and password. Your opener must already be paired in the mobile app.

The repository can be installed as a custom repository without being in HACS's default catalog. See [HACS custom repositories](https://www.hacs.xyz/docs/faq/custom_repositories/).

For manual installation, copy the entire `custom_components/skylink_orbit` directory into Home Assistant's `config/custom_components/` directory, then restart and add the integration. Include its `orbitclient`, `translations`, and `brand` subdirectories. HACS installs dependencies declared in the manifest through Home Assistant.

To update, download the newer version in HACS and restart. To remove it, delete the entry under Devices & services, remove the download from HACS, and restart. This does not unpair the opener in Orbit Home.

## Light and Wi-Fi signal

GDO openers get **Light on** and **Light off** buttons automatically, without requiring a reported light value. These explicit commands match the controls the owner sees in Orbit Home. Light commands on other device families remain unverified and are not exposed. Signal sensors appear when a usable RSSI value arrives, including in updates after setup; a hub with two door channels still has one signal sensor. Existing door entity IDs are unchanged.

Each light button press sends the app's corresponding command once. A successful service call means the command was sent; it does not confirm physical light state. No on/off state is inferred or displayed, and desired-command echoes are not treated as reported state. Offline requests are rejected, and commands are never automatically retried or replayed. Verify the physical light response on your opener. Version 0.2.1 replaces the stateful light entity introduced in 0.2.0 with these buttons; an old light entity, if one was created, is no longer provided.

**No periodic polling is added.** Signal data uses the existing MQTT startup/reconnect snapshot and pushed updates. There are no new subscriptions, refresh timers, or RSSI requests. Normal connection keepalives remain. The Wi-Fi sensor shows the last reported value; Skylink controls how often it changes, and it may remain unchanged until a later update or reconnect. Zero, malformed, and missing RSSI are unknown, not a strong signal. If the hub never reports a usable value, no signal entity is created. Explicitly cleared values become unknown; disconnected or removed hubs become unavailable.

The protocol is unofficial. Reusing existing pushed data minimizes additional traffic; it does not establish vendor approval of third-party clients.

## First account validation

Start with status only. Confirm that the displayed door state matches the phone app and that both can remain connected. Check the phone's notifications after logging in: the app's empty push-token login default is used here, but its effect on existing notification registration is not yet verified.

A read-only command is included for testing from a development machine:

```sh
uv sync --frozen
uv run python tools/check_account.py --observe 30
```

It prompts locally for email and a hidden password, reads the initial snapshot, and prints allowlisted status updates. It has no garage activation command, writes no credentials, and does not print account IDs, hub IDs, names, or raw packets. Use `--observe 0` for a single snapshot. Do not put your password in GitHub issues or command arguments.

The integration retains the password in Home Assistant's local config entry because the recovered MQTT protocol uses it on reconnect. No token-refresh API has been established. Treat Home Assistant configuration and backups as sensitive.

## Optional activation test

Once status is working, use an attended test with a clear view of the door. Enable the door's **Activate door** entity under the device's entities, then press it once while observing the result. Do not build directional open/close automations from this pulse command until its behavior has been validated.

A successful press means a reported status change was observed, not that a requested final position was reached. An uncertain outcome is reported as an error. Check the physical door before another press. Commands are never retained, automatically retried, or replayed after reconnection; concurrent/rapid duplicate requests are rejected.

## Troubleshooting

- **Cannot connect:** the integration needs HTTPS access to `iot.skyhm.net:8444` and MQTT-over-TLS access to `iot.skyhm.net:1899`. A successful HTTP login alone is insufficient; device discovery comes through MQTT.
- **Login rejected:** verify the account in Orbit Home. Vendor result codes are undocumented, so generic failures are not mislabeled as bad passwords.
- **Unavailable:** the cloud session or the device is offline. The client retries its connection with bounded backoff and requests a new snapshot.
- **No doors:** download diagnostics. Unsupported device types are counted without exposing raw device data.
- **TLS failure:** keep certificate and hostname verification enabled. The package supplements system trust with the public CA chain found in the app; vendor certificate changes may require an update.

Diagnostics are available on the integration entry's menu. Share those instead of configuration files, packet captures, or raw debug dumps.

## Development

Python 3.14.2+ and [uv](https://docs.astral.sh/uv/) are used. The lockfile pins the tested Home Assistant environment.

```sh
uv sync --frozen
uv run python -m pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Tests exercise the public client against local HTTP and MQTT peers and Home Assistant's config-flow/entity interfaces against a simulated client. CI also runs hassfest and HACS validation. No real credentials or garage commands are used by tests.

Protocol details and limitations:

- [Recovered MQTT protocol](research/orbit-mqtt-protocol.md)
- [Recovered HTTP authentication](research/orbit-auth-discovery.md)
- [HA architecture](research/home-assistant-design.md)
- [Approved first-release scope](docs/build-contract.md)
- [APK provenance](research/apk-provenance.json)

App binaries, decompiled source, and account captures are excluded from this repository. The client is an independent Python implementation based on the documented wire behavior. `mqtt-ca.pem` contains public RapidSSL/DigiCert CA certificates, not private keys. The garage icon is original project artwork, not Skylink branding.
