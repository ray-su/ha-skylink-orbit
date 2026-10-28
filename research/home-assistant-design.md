# Home Assistant design for Skylink Orbit

Researched 2026-10-27 against Home Assistant/HACS documentation and the locally decompiled Orbit 3.8.9 APK. Authentication and discovery evidence is recorded in [orbit-auth-discovery.md](orbit-auth-discovery.md). Static protocol facts are sufficient to design the client; account login, actual device state transitions, and movement semantics still need owner-device validation.

## Recommended structure

Build an independent async Python client and a small custom integration, using a distinct domain such as `skylink_orbit` (check availability before release). Home Assistant's documented architecture keeps protocol work in an external library and translates that library into entities. For our initial GitHub/HACS distribution, keep the HA-independent client as an `orbitclient/` subpackage inside `custom_components/skylink_orbit/`, so HACS installs it with the integration. A separate PyPI release is not needed for this approach. Moving the client to a separately published package can follow later; HA core contribution has its own external-library requirements. Inject HA's `aiohttp` session into the client. Keep authentication, transport/parsing, device models, and errors separable. [HA: API library](https://contributors.home-assistant.io/core/integrations/api-library/)

Proposed modules:

```text
custom_components/skylink_orbit/
  orbitclient/     # HA-independent auth, HTTP, MQTT, models, exceptions
  __init__.py       # setup/unload and typed runtime data
  config_flow.py    # login validation, deduplication, reauth
  coordinator.py   # account state, event dispatch, availability
  cover.py         # garage door entities
  button.py        # explicit door activation while pulse semantics remain uncertain
  diagnostics.py   # explicit sanitized diagnostic schema
  manifest.json
  strings.json
  translations/en.json
tests/              # sanitized fixtures and behavioral tests
hacs.json
```

The split is a design recommendation. No endpoint names or MQTT payload formats are assumed by these filenames.

## Account setup and lifecycle

Use a UI config flow with one entry per `acc_no` returned by authentication, and reject duplicate entries. Validate login and the MQTT device snapshot before creating the entry. Credentials must be entered locally through the config flow, not placed in code, issue reports, or example fixtures. The APK logs in with email/password and then authenticates MQTT with `acc_no` and that same password; retain the password locally because no renewable-token alternative has been demonstrated. Do not promise token refresh. Test the app's empty default `pn_token` with the owner's account before adopting it. [HA: config flow](https://github.com/home-assistant/developers.home-assistant/blob/master/docs/core/integration/config_flow.md); [recovered authentication](orbit-auth-discovery.md)

During entry setup, instantiate the client, authenticate HTTP, connect MQTT, register handlers, subscribe, request and await the initial snapshot, and forward setup to the entity platforms. Keep the client/coordinator in a typed `ConfigEntry.runtime_data`. On unload, unload platforms, cancel connection/retry tasks, remove callbacks, and close client-owned sockets. Do not close HA's injected HTTP session. Use config-entry-managed tasks and cleanup callbacks where appropriate. [HA: config entries](https://contributors.home-assistant.io/core/integrations/config-entries/)

**Version note:** current development documentation shows setup/unload returning `None`, while the tagged [HA 2026.10.0 Abode integration](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/components/abode/__init__.py) still returns booleans. Select the user's actual installed HA version and check its APIs before choosing lifecycle signatures or form-validation dependencies. Test that version and record the minimum supported release in HACS metadata.

## HTTP and MQTT ownership

The library should own a direct authenticated connection to the vendor broker; no user-installed MQTT broker is required. HTTP is used for login, and MQTT supplies device discovery as well as updates and commands. Subscribe only to the logged-in account's `get/result` and `update/result` topics. Keep broker details and credential construction in the library, away from HA entities. Snapshot requests publish `{}` to `skylink/things/client/<acc_no>/get`. There is no demonstrated REST state endpoint and no REST polling fallback in this design. [Recovered discovery](orbit-auth-discovery.md)

One account-level coordinator shares device snapshots between entities. Use push updates through `async_set_updated_data`, with no periodic poll. Use the MQTT snapshot exchange for startup, reconnection, and explicit reconciliation; serialize concurrent snapshot requests because no correlation ID is evident in that exchange. Bound the first-snapshot wait so setup can fail and retry cleanly. `CoordinatorEntity` handles listeners and the coordinator's availability, while individual devices also consult their own `online` flag. [HA: fetching data](https://contributors.home-assistant.io/core/integrations/fetching-data/)

**Proposed transport behavior:** reconnect with bounded exponential backoff and jitter, rebuild subscriptions after reconnect, and reconcile state. Register the message handler before subscribing to avoid losing retained or immediate messages. A broker connection alone must not prove every opener is online. Track per-device availability from `online` and connection/snapshot validity. A quiet push device is not necessarily offline; derive any additional freshness rule from observed behavior. Serialize connection/login recovery per account.

**TLS requirement:** preserve certificate-chain and hostname validation. The APK points MQTT at an IP address, but its server certificate identity is `iot.skyhm.net`. The main investigation's unauthenticated handshake succeeded with that TLS server name and the app's embedded public CA certificates supplementing the system trust store. Use a transport that permits a separate connection address and TLS server name, or first verify a suitable DNS endpoint. Do not copy the app's permissive hostname verifier. Full authenticated MQTT operation remains untested.

Use `iot_class: cloud_push` for the recovered MQTT architecture. The manifest declares the version, domain, config flow, documentation, issue tracker, owners, and pinned client requirement. [HA: manifest](https://contributors.home-assistant.io/core/integrations/manifest/)

## Garage entity behavior

Use `CoverEntity` with `CoverDeviceClass.GARAGE`. Explicitly declare supported features; initially expose status and no motion features until state semantics are validated. `is_closed` can be `None` when unknown; movement direction is optional. Do not derive a percentage from binary open/closed sensing. Home Assistant's generic toggle itself uses reported state and supported features, so accurate flags matter. [HA: cover entity](https://contributors.home-assistant.io/core/entities/cover/)

Use physical hub identity `(DOMAIN, hub_id)` in the device registry and channel-specific entity unique IDs such as `<acc_no>:<hub_id>:A:cover`. NOVA can have two doors under one hub; splitting by `hub_id` alone merges those doors incorrectly. The APK creates logical `NOVA_A` and `NOVA_B` entries from named door channels. `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/manager/DeviceManager.java:36` (DeviceManager.java:36; local analysis artifact, excluded from Git)

**Recovered control constraint:** the main app investigation found `cmd=0` on the primary door button for GDO/NOVA/NVMini; no usage demonstrating distinct open/close commands `1` or `2` was found. Treat it as an activation/pulse-style operation until observed otherwise. After supervised validation, an explicitly named "Activate door" button can expose that native operation; add guarded `OPEN | CLOSE` cover methods only when fresh terminal-state sensing reliably determines direction. Do not expose `STOP` or position setting without supporting evidence.

**Proposed command contract:**

- Distinguish explicit “open”/“close” commands from a wall-button-style pulse. A pulse is not intrinsically an idempotent open or close operation.
- If only pulse control exists, use a fresh confirmed state, serialize commands per door, and reject requests when state or direction is unknown. Do not blindly expose a pulse as both open and close. If reliable directional behavior cannot be established, begin with status reporting and a separately named button instead.
- An already-open open request or already-closed close request should do nothing. Suppress duplicate requests while a command is pending; do not send a second pulse simply because an acknowledgement timed out.
- A publish callback confirms transport progress only; it does not prove application acceptance or final position. Await device state and report timeout/failure explicitly. Never replay a queued motion command after reconnect or HA restart.
- Do not bypass firmware obstruction detection, close warnings, or safety delays. Retain observed intermediate/error states in the client model.

These are engineering requirements for this project, not claims that HA mandates a specific garage protocol. A confirmed terminal state must remain distinguishable from a stopped/partly-open state; the latter is insufficient to safely translate a target-state action into a pulse.

## Failures, reauthentication, and diagnostics

During setup, map temporary network/service failures to `ConfigEntryNotReady` and rejected credentials to `ConfigEntryAuthFailed`. In polling/coordinator refreshes use the corresponding coordinator exceptions. Service/entity commands should raise an appropriate user-visible `HomeAssistantError` or `ServiceValidationError` rather than silently succeeding. [HA: failures](https://contributors.home-assistant.io/core/integrations/setup-failures/)

Implement reauth that verifies the same account ID and updates/reloads the existing entry. A fatal authentication failure in a push task must explicitly start reauth via `entry.async_start_reauth(hass)`; merely raising `ConfigEntryAuthFailed` inside an arbitrary background task does not initiate the flow. Do not translate an internet outage into a password prompt. [HA: reauthentication](https://contributors.home-assistant.io/core/integrations/reauth-reconfigure/)

Use a Repair issue only when the user can take a concrete corrective action, such as an unsupported device configuration; ordinary transient disconnects belong in availability/retry handling. [HA: Repairs](https://contributors.home-assistant.io/core/integrations/repairs/)

Expose diagnostic counts, firmware/model identifiers where safe, transport status, last successful update, and sanitized error categories. Exclude passwords, tokens, emails, locations, device secrets, signed URLs, MQTT usernames/client IDs if identifying, and topics that embed account/device secrets. `async_redact_data` can redact nested named keys, but an explicit allowlist is safer than returning raw packets. Snapshot-test the resulting output. [HA: diagnostics](https://contributors.home-assistant.io/core/integrations/diagnostics/)

## Packaging and verification

For HACS, keep one integration under `custom_components/<domain>/`, put all required integration files there, and provide the required manifest fields. Current HACS documentation also requires brand assets; provide the appropriate icon and document installation. [HACS: integrations](https://www.hacs.xyz/docs/publish/integration/)

Use a public GitHub repository, README, and root `hacs.json`; set the minimum HA version after compatibility testing. Installing as a HACS custom repository is distinct from inclusion in the default catalog. [HACS: publishing](https://www.hacs.xyz/docs/publish/start/)

Proposed meaningful test cases:

1. Login success, invalid login, timeout, duplicate account, revoked/changed password, same-account reauth, malformed `result:00` response handling, fixed timestamp/signature vectors.
2. Initial snapshot followed by push changes; reconnect and reconciliation; broker connected/device offline; malformed and unrelated messages ignored.
3. Observed status codes map correctly, including unknown, obstruction, movement, and stale snapshots.
4. Commands are serialized; duplicate open/close requests do not issue duplicate motion; ambiguous timeout never retries a pulse.
5. Reload/unload removes tasks and listeners; restart never sends motion; diagnostics contain no sensitive fixture values.

Use sanitized captures to test protocol parsing before live control. Validate the library independently first, then run HA integration tests and HACS/hassfest validation. Live movement tests require an explicit user-directed test session with the actual door and observed outcomes; this design research does not perform them.
