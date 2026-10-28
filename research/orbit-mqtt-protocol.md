# Orbit Home MQTT protocol recovered from Android

Recovered 2026-10-27 from `com.mtqqdemo.skylink` 3.8.9 (versionCode 79), with control-path comparison to 3.5 (55). These are observations from the app's code, not proof of successful operation against the owner's account or garage. No account login, MQTT CONNECT/SUBSCRIBE/PUBLISH, or garage action was performed. Network checks were DNS resolution and TLS handshakes only.

## Artifact provenance and confidence

Downloaded XAPK bundles through APKPure's direct file links. The `version=latest` link returned **3.8.9**, despite the web search/cache listing 3.8.1. Both base APK signing blocks contain the same certificate fingerprint; version 3.5's archive hash also matches the listing. This is consistency evidence, not independent Google Play provenance or cryptographic APK signature verification. See [machine-readable hashes and signer fingerprints](apk-provenance.json).

JADX 1.5.6 produced usable source with 38 decompilation errors on the latest app. Rechecked the critical command builder in simple decompilation mode; the older 3.5 build retains descriptive method names and corroborates the same topic structure, credentials and commands. Do not copy malformed decompiled Java control flow literally.

Local evidence roots:

- Latest source: `artifacts/decompiled-3.8.9/sources/`
- Critical lower-level check: `artifacts/MainActivity-simple.java`
- Older comparison: `artifacts/MainActivity-3.5.java`

All these proprietary artifacts are excluded from Git. Class/method line references below refer to these exact local outputs.

## Connection and credentials

| Setting | Recovered value | Evidence |
| --- | --- | --- |
| Broker in app | `ssl://34.214.223.70:1899` | Latest `com/mtqqdemo/skylink/MainActivity.java:725` |
| Username | `acc_no` returned by HTTPS login | `MainActivity.java:736` |
| Password | Original stored Orbit account password | `MainActivity.java:737`; login stores entered password |
| Client ID | Account number concatenated with random integer 0–998 | `MainActivity.java:723–725` |
| Clean session | true | `MainActivity.java:732` |
| Keepalive | 30 seconds | `MainActivity.java:739` |
| Automatic reconnect | enabled in app | `MainActivity.java:726` |
| Subscribe QoS | 1 | `MainActivity.java:431–433` |
| Publish QoS / retain | 0 / false | `MainActivity.java:1269` |

Use a persistent, distinct HA client ID after testing allowed length/format and multiple sessions; copying the app's small random namespace invites collisions. Do not assume an account number alone is sufficient authorization: the original password is used for MQTT. See [HTTP authentication details](orbit-auth-discovery.md).

### TLS checks performed

- `iot.skyhm.net` resolved to `34.214.223.70` during this investigation.
- HTTPS `iot.skyhm.net:8444` passed Python's default CA and hostname checks.
- MQTT `34.214.223.70:1899`, with SNI/expected hostname `iot.skyhm.net`, initially failed the default trust context with “unable to get local issuer certificate.”
- Adding the two public CA certificates bundled in `MainActivity.s()` to a default trust context made the same MQTT TLS handshake pass **with hostname checking still enabled**.
- The presented certificate SANs are `iot.skyhm.net` and `www.iot.skyhm.net`; its expiry was March 2, 2027. This is a point-in-time observation.

The app's custom `p016c4/e.java` hostname verifier unconditionally accepts the IP/domain; do not copy that behavior. Prefer the verified DNS name and normal hostname checks, supplementing the missing chain with appropriate public CA material if still necessary. Local extracted public certificates: `artifacts/orbit-mqtt-ca.pem`. The CA fallback needs a maintainable packaging/update strategy, not `CERT_NONE` or `tls_insecure_set(True)`.

## Topics and discovery sequence

All observed topics are scoped to the logged-in **account**, not individual doors.

| Direction | Topic | Payload/meaning |
| --- | --- | --- |
| Subscribe | `skylink/things/client/{acc_no}/get/result` | Full account device snapshot; `data` is a list |
| Subscribe | `skylink/things/client/{acc_no}/update/result` | Update envelope; `data` is one device object |
| Publish | `skylink/things/client/{acc_no}/get` | `{}` requests device snapshot |
| Publish | `skylink/things/client/{acc_no}/desire` | Desired-state command envelope |

Evidence: latest `MainActivity.java:208–217`, callback `com/mtqqdemo/skylink/a.java:50–89`, `bean/TestData.java`. Older `MainActivity-3.5.java:209–212,1135–1137` names the method `getAllDevice()`.

The latest app waits for subscription success on `get/result`, then schedules `{}` publication 800 ms later (`MainActivity.java:367–390`). It subscribes separately to updates. HA should await both subscription acknowledgements, then obtain its initial snapshot; determine in account testing whether the app's extra 800 ms is necessary. Buffer/reconcile updates arriving during the snapshot so an older snapshot does not erase newer state.

There is no evidence here for the scaffold's HTTP `GET /v1/devices` fallback. Requesting a fresh snapshot over MQTT is the recovered reconciliation mechanism. If the broker is unreachable, mark state unavailable and reconnect; a healthy HTTP login does not restore device status.

## Device and state models

Relevant fields in `manager/SkyLinkDevice.java` include:

```text
hub_id, hub_name, type, online
firstdoor_name, seconddoor_name
reported.mdev.door, light, rssi, fw
reported.mdev.firstdoor.door, GS, position
reported.mdev.seconddoor.door, GS, position
reported.link.online
```

Initial online state is the top-level `online`. The app applies update connectivity from `reported.link.online` and replaces provided `mdev`/`sdev` subobjects (`SkyLinkDevice.java:589–590,677–694`). A Python implementation must preserve field presence instead of Java's absent-boolean false and absent-integer zero defaults. Whether updates always contain complete `mdev` objects needs live verification.

NOVA can represent **two doors under one hub ID**. The app creates virtual A/B entries based on nonempty `firstdoor_name` and `seconddoor_name` (`DeviceManager.java:36–58`). `NOVA_A` and `NOVA_B` are app-derived types; do not require those exact types in raw server data. Use one HA device per hub and cover unique IDs including the channel. NVMini uses `firstdoor`; GDO uses `reported.mdev.door`. SMO appears in UI firmware/state branches but the inspected main door-command dispatch does not explicitly handle it; do not claim it tested/supported.

### Door status codes

The UI maps these values to named door graphics in `p098r4/c.java:313–357`, corroborated by `p098r4/g.java`. These are code-derived meanings, not physical tests.

| Code | App interpretation | Proposed HA handling |
| --- | --- | --- |
| 0 | Opening animation | `is_opening=True` |
| 1 | Opened | Open |
| 2 | Half/partial | Partially open; direction unknown |
| 3 | Closing animation | `is_closing=True` |
| 4 | Closed | `is_closed=True` |
| 5 | Close-delay graphic | Pending close; expose raw state, verify motion timing |
| 6 | Half/partial | Partially open; distinction from 2 unresolved |
| 7 | Unknown | `is_closed=None` |
| Missing/other | Not established | Unknown, never coerce to 0 |

The `autoclose_en` setting can also change the displayed graphic. Do not collapse configured auto-close and current door motion into the same state. `GS` is a sensor-related field but its exact meaning is not established; do not label it obstruction or battery without evidence. No reliable percentage position is recovered: command `position` is a channel selector A/B.

## Command contract

Publish UTF-8 JSON, QoS 0, not retained, to the account's `/desire` topic:

```json
{
  "data": {
    "hub_id": "<owned hub ID>",
    "desired": {
      "mdev": {
        "ctrlgdo": {
          "cmd": 0,
          "ts": "<Unix milliseconds as a string>"
        }
      }
    }
  }
}
```

For NOVA A/B or NVMini A, include `"position":"A"` or `"B"` inside `ctrlgdo`. GDO omits it. Evidence: latest `MainActivity.n()` at 621–639 and `p()` at 1228–1254; simple output at 1376–1404 confirms one publish per call despite malformed high-level decompilation. Older `contralDoor` and `deviceContral` at 1149–1193 provide clearer names and equivalent construction.

| Command | Evidence-backed interpretation |
| --- | --- |
| 0 | Main door-button action for GDO, NOVA A/B, and NVMini; same value is used regardless of door state |
| 3 | Light on: older `contralLight(device,true)` and latest light-result mapping |
| 4 | Light off: older `contralLight(device,false)` and latest light-result mapping |

Older `MainActivity-3.5.java:1139–1142` directly establishes 3/4 as light on/off. Latest `home/fragment/DeviceDetailFragment.java:844–855` maps desired command 3 to light=1 and 4 to light=0. This confirms app intent, not that a command response proves the physical light state.

**No dedicated OPEN, CLOSE or STOP opcode has been established.** The state-independent `cmd=0` is consistent with toggle/wall-button behavior. Actual progression, stopping, reversing, warning delay, and behavior from partial states must be validated on the user's model. Do not invent meanings for unused numbers such as 1 or 2, and do not brute-force command numbers: other opcodes modify sensors, settings or firmware.

A successful QoS-0 publish callback is not device acknowledgement or final state. The app's incoming callback has a branch for `/desire`, but the observed startup only subscribes to `/get/result` and `/update/result`; do not assume that branch establishes an acknowledgement channel. Match results through subsequent reported state, with a bounded timeout and explicit unknown outcome.

## Implications for the integration

1. Build a signed HTTP login adapter plus a direct MQTT session. Retain the user-provided account password for reconnect unless a separately verified alternative is found; there is no recovered access/refresh-token lifecycle.
2. Use a push coordinator with an MQTT snapshot request on startup/reconnect and carefully bounded reconciliation when required. Keep a single account connection for all hubs/channels.
3. Implement a low-level `press_door_button(hub_id, channel)` operation, rather than claiming that the wire protocol supports `open`, `close` and `stop`.
4. Initially expose status plus an explicitly named control button. After attended validation, a cover may implement open-from-confirmed-closed and close-from-confirmed-open with one pulse. Refuse ambiguous, partial or stale-state motion. A state check cannot eliminate races with a physical remote; document that limitation. STOP remains disabled until behavior is confirmed.
5. Never automatically retry, retain, queue or replay a motion pulse after timeout/reconnect. Use a per-channel command lock and pending-command state. The app itself queues a disconnected publish; that is not a behavior HA must reproduce.
6. Add the light only for devices with established capability; treat reported state separately from an optimistic desired-state echo. Expose RSSI if actually present. Avoid guessing unsupported sensors.
7. Continue with read-only account validation first: authenticate locally, enumerate devices and observe status alongside the iPhone app. No Android phone is needed.

See [updated HA design](home-assistant-design.md) and [HTTP/auth details](orbit-auth-discovery.md).

## Light and Wi-Fi extension (2026-10-28)

Rechecked the local app sources before implementing these optional entities. `p098r4/c.java:210–217` reads light and RSSI from the same reported `mdev` object populated by snapshots and MQTT updates. Its Wi-Fi graphic thresholds at lines 135–148 are -65, -75 and -85, consistent with received signal strength in dBm. We expose the reported negative integer, not the graphic's coarse percentage. Missing, zero, out-of-range or malformed values remain unknown.

The older `contralLight` method passes commands 3/4 with no channel selector. The latest app independently sends 3 in `p098r4/c.java:195` and 4 in `p098r4/f.java:27`, both via `MainActivity.p(hub_id, cmd, 1, null)`. The latest UI also applies optimistic desired-command echoes, as described above; the integration deliberately requires reported light state instead.

Implementation uses the existing account subscription and startup/reconnect snapshot only. No RSSI polling, extra subscription, post-command snapshot request, or automatic command retry is added. Entity creation requires a valid reported field on a recognized family. Light and RSSI belong to the physical hub, not its door channels.

The owner has now confirmed GDO login, discovery, activation, and opening/open/closing/closed reports in HA. Light command responses and RSSI publication frequency remain unverified on hardware. If the vendor only provides a value in snapshots, HA retains that last reported value until another push or reconnect; it does not request more frequent reports. This design does not establish vendor approval of third-party API usage.
