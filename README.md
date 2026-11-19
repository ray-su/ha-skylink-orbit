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

The cover remains **status-only by default**, including after an update. An explicit **Configure → Enable guarded Open/Close for validated GDO doors** option enables directional capabilities for GDO devices in that account; NOVA/NVMini remain status-only. Enable only after attended validation on every affected GDO door. Changing the option reloads the integration, briefly interrupting status and resetting in-memory interlocks: inspect the doors and ensure they are stationary first. Existing activation buttons and entity unique IDs are preserved.

When enabled, each eligible directional request asks for one account snapshot over the existing MQTT connection and reconciles intervening pushed updates. No periodic polling or new subscription is added. Open sends one native activation only from reported Closed (4); Close sends one only from fully Open (1). A consistent refreshed report already at the target causes no pulse. Moving, partial, close-delay, unknown, removed or offline doors, failed refreshes, contradictory snapshots, and changed connections are rejected without a motion command.

Every accepted snapshot is checked against prior motion-relevant evidence for **all known hubs**, including during reconnect and another door's request. A contradiction records a hub-level block separately from the replaceable status cache. This also covers no-op requests, removed/offline/unknown state, and model/channel changes; it blocks **both directional requests and native activation** for that hub. Later snapshots, telemetry, matching pushed reports or automatic reconnect cannot clear an existing block. A legitimate push before/during reconciliation can establish a changed state when no conflict was already recorded; metadata-only name, light and RSSI changes do not create one. Position reports received before a hub has supplied its model count as evidence too: discovery can fill in missing fields, but cannot contradict an observed position. If updates are dropped from the MQTT receive queue or a startup, reconnect or refresh buffer, the integration blocks motion for the entire account for the lifetime of that client. When reports conflict, history is lost, or an outcome is uncertain, the affected HA cover and activation button become **unavailable**; the HomeKit bridge marks the garage accessory unavailable rather than serving the disputed position as current. Diagnostics retain the raw last report and a reason. Light and Wi-Fi telemetry remain separate.

**These are state-guarded pulses, not native directional hardware commands.** A newly received vendor snapshot can still contain stale device data, and a physical remote can race the check. Both directional and native activation controls share pending/cooldown checks and a persistent in-memory motion interlock. First movement does not release it: an explicit pushed target-position report is required. Snapshots, telemetry, elapsed debounce time and automatic reconnection cannot clear uncertain motion. Directional success requires the expected progress or target report, not merely any state change; it does not necessarily mean the final position has been reached. Motion is never automatically retried or replayed.

If a snapshot request times out/is cancelled, its response ordering becomes uncertain and further directional refreshes are blocked for that client instance. Uncertain activation outcomes and contradictory-snapshot hub blocks remain latched too. **Inspect the physical door and confirm it is stationary before manually reloading the integration to recover.** A reload/restart resets in-memory protection; do not use it as an automatic retry mechanism. A missed legitimate state change can therefore require attended recovery rather than an automatic guess. Stop and percentage-position actions remain unsupported. No optimistic Orbit cover state or obstruction sensor is invented.

**0.3.0 position handling:** Login in the configuration form verifies the account identity; entry setup performs MQTT discovery before exposing entities. Automatic setup retries share the failed attempt's observations, conflicts and history-loss flag without reusing its network client. Retained broker messages cannot complete a new discovery or acknowledge movement. Conflicting rows for a hub, or a pushed change of a known hub model, require attended recovery. An unknown, missing or close-delay reading makes the cover unavailable until a representable report arrives. On a directional cover, partial states (2/6) are also unavailable rather than advertising fully Open to Apple Home. A status-only cover retains the partial-as-open mapping. A valid push restores a merely unrepresentable position, not a latched conflict.

### Apple Home / CarPlay (pending live validation)

With the GDO option enabled, the existing garage-class cover advertises Open and Close, matching Home Assistant's HomeKit garage accessory interface. Once installation and control opt-in are separately approved, expose **only the garage cover** with HomeKit Bridge and pair that bridge in Apple Home. HomeKit commands use the integration guards, **not** the old arrival dashboard's GPS, three-minute authorization window or once-per-arrival conditions. Existing arrival automations remain unchanged. Siri Suggestions may show a garage tile near home, but Apple controls its appearance. Local tests exercise HA's real HomeKit garage adapter and cover services with a mocked driver/client, including resynchronization after a rejected command. Pairing, remote operation through a home hub, CarPlay presentation and physical actuation remain untested.

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
uv run --with-requirements tests/homekit-requirements.txt python -m pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Tests exercise the public client against local HTTP and MQTT peers, Home Assistant's config-flow/entity interfaces against a simulated client, and the real HA HomeKit garage adapter against a mocked HAP driver and Orbit client. The optional test requirements enable that adapter test; plain `uv run pytest` skips it if HAP-python is absent. CI uses the complete test command above and also has hassfest and HACS validation jobs. No real credentials or physical garage commands are used by tests.

Protocol details and limitations:

- [Recovered MQTT protocol](research/orbit-mqtt-protocol.md)
- [Recovered HTTP authentication](research/orbit-auth-discovery.md)
- [HA architecture](research/home-assistant-design.md)
- [Approved first-release scope](docs/build-contract.md)
- [APK provenance](research/apk-provenance.json)

App binaries, decompiled source, and account captures are excluded from this repository. The client is an independent Python implementation based on the documented wire behavior. `mqtt-ca.pem` contains public RapidSSL/DigiCert CA certificates, not private keys. The garage icon is original project artwork, not Skylink branding.
