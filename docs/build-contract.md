# Skylink Orbit first implementation — implementation contract

This is the approved implementation contract, not a claim of live-device validation.

## Deliverable

A Home Assistant custom integration in `custom_components/skylink_orbit`, installable by copying that directory or by adding the public GitHub repository to HACS. Bundle the asynchronous Orbit client inside the integration so no separate package release is needed.

The first release provides:

- Email/password setup in Home Assistant, duplicate-account prevention, and same-account reauthentication.
- Signed HTTPS login and a verified TLS MQTT connection to the vendor service.
- Discovery and live status for GDO, NOVA A/B and NVMini door channels supported by the recovered protocol. Unknown device types are reported diagnostically without guessed controls.
- One physical Home Assistant device per hub and one status cover per door channel.
- An explicitly named Activate door button, disabled by default pending an attended check on the owner's model. It sends the recovered native activation command once. No open/close/stop or percentage-position promise until validated.
- Snapshot reconciliation after reconnect; presence-aware updates; clear offline/unknown state; cancellation and socket cleanup on unload.
- A local read-only account-check command with hidden password input. It authenticates and observes device snapshots; it cannot send motion commands.
- Sanitized diagnostics, installation instructions, HACS metadata and automated checks.

Login with an empty push token follows the app's default but has not been validated against an account. Live-device validation remains a release acceptance step. Account credentials and raw account captures stay local and outside source control.

## Approved public testing boundaries

1. **Orbit client API against controlled HTTP/MQTT servers.** Check request signing against fixed independently established vectors, login failures, account-scoped discovery, incremental updates, reconnects, timeouts and one-shot command delivery. This catches protocol and lifecycle regressions; it cannot establish the vendor's current acceptance rules or physical door behavior.
2. **Home Assistant's config-flow and entity interfaces.** Check setup, duplicates, reauthentication, status mapping, availability, activation rejection while disconnected, unload and diagnostic redaction. Substitute the public Orbit client boundary. This catches integration behavior; it does not retest the actual broker or hardware.

Use one failing behavior test and its implementation at a time. Run network protocol tests locally without real credentials. Review the implementation against an initial commit containing only the existing research and agreed project setup.


Compatibility target: Home Assistant 2026.10.0 and Python 3.14.2 or newer. The user approved this scope and both test boundaries on 2026-10-27.

## Authorized extension: light and passive Wi-Fi telemetry (2026-10-28)

The owner requested light state/on-off control and Wi-Fi strength, with an explicit constraint against periodic signal polling. Add optional hub-level entities based on reported fields, using the existing push session and startup/reconnect snapshot. Light commands use recovered opcodes 3/4 once and await reported confirmation, without optimistic desired echoes or automatic retries. Continue testing at the two approved public boundaries above. Hardware validation of these additions remains separate from simulated test results. Catalog submission and announcements are deferred at the owner's request.
