# Orbit 3.8.9: authentication and discovery from the Android app

Analyzed 2026-10-27. Scope: local static analysis of the supplied decompilation of package `com.mtqqdemo.skylink`, version 3.8.9 (versionCode 79, APK acquisition/version verification handled in the main research). No credentials were supplied, no cloud login was performed, and no device commands were sent. Separate DNS/TLS handshake checks are documented in [the MQTT protocol note](orbit-mqtt-protocol.md). These are facts about this APK's code paths; server acceptance and behavior remain unverified.

## Confirmed HTTP request construction

The configured base URL is `https://iot.skyhm.net:8444/skylinkhub_crm/`. Retrofit appends `skyhm_api_s.jsp?cmd=<command>` and sends POST requests. The request body is a JSON object serialized as `application/json; charset=utf-8`. See `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/net/RetrofitUtil.java:123` (RetrofitUtil.java:123; local analysis artifact, excluded from Git), `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/net/ApiService.java:83` (ApiService.java:83; local analysis artifact, excluded from Git), and `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/net/RequestUtil.java:10` (RequestUtil.java:10; local analysis artifact, excluded from Git).

The interceptor adds four headers:

| Header | Construction |
| --- | --- |
| `REQ-TIMESTAMP` | Decimal Unix epoch milliseconds, truncated to whole seconds |
| `REQ-CMD` | Command passed to `RetrofitUtil.getService` |
| `REQ-DATA` | A caller-selected identifier, not the serialized JSON body |
| `REQ-SIGNATURE` | Lowercase hexadecimal MD5 described below |

The signature input is `timestamp + "+" + command + "+" + req_data + "+" + embedded_signing_suffix`. The constant is present in the APK but deliberately omitted here. It is an ordinary MD5 digest of concatenated bytes, not HMAC, and does not include the body. The method formats current time as `yyyy-MM-dd HH:mm:ss` in GMT+08 then parses using that same formatter; those timezone conversions cancel, while subsecond precision is discarded. Do not add eight hours in a Python port. See `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/net/HeadInterceptor.java:32` (HeadInterceptor.java:32; local analysis artifact, excluded from Git) and the concatenation helper `artifacts/decompiled-3.8.9/sources/A1/C0171b.java:59` (C0171b.java:59; local analysis artifact, excluded from Git).

The traced login request has no additional application-level encryption, password hash, Bearer token, or OAuth exchange. The password is a JSON string transported over HTTPS. This finding applies to the traced login path; it is not a claim about every feature in the app. `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/account/LoginActivity.java:156` (LoginActivity.java:156; local analysis artifact, excluded from Git)

## Login exchange

```http
POST /skylinkhub_crm/skyhm_api_s.jsp?cmd=act_login
Content-Type: application/json; charset=utf-8
REQ-CMD: act_login
REQ-DATA: <entered email>
REQ-TIMESTAMP: <epoch milliseconds truncated to seconds>
REQ-SIGNATURE: <computed digest>
```

```json
{
  "username": "<entered email>",
  "password": "<entered account password>",
  "app_sys": "gcm",
  "app_brand": "00",
  "pn_token": "<stored Firebase push token, default empty string>"
}
```

Both manual and automatic login construct this shape. The manual path passes the entered email verbatim, rejects spaces, and checks for empty credentials; there is no case normalization in the traced method. The app can construct a request with an empty `pn_token`, but that does not prove the server accepts an empty token or that login cannot alter push registration. See `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/account/LoginActivity.java:142` (LoginActivity.java:142; local analysis artifact, excluded from Git) and `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/SplashActivity.java:38` (SplashActivity.java:38; local analysis artifact, excluded from Git).

The response model is:

```json
{
  "result": 0,
  "message": "<server text>",
  "acc_no": "<account identifier>",
  "alias_name": "<display name>",
  "head_portrait": "<profile image value>"
}
```

This is a schema reconstructed from fields, not a captured server response. The generic callback treats `result == 0` as success. `result == 23` invokes a special `isAccount` callback; its base implementation is empty, so its exact meaning is not established. Other results pass the server's message to failure handling. Do not invent an invalid-password code. The JSON converter specifically repairs the malformed token `"result":00` to `"result":0`, suggesting compatibility with that server output. See `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/bean/LoginBean.java:4` (LoginBean.java:4; local analysis artifact, excluded from Git), `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/bean/BaseBean.java:4` (BaseBean.java:4; local analysis artifact, excluded from Git), `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/net/NetCallback.java:67` (NetCallback.java:67; local analysis artifact, excluded from Git), and `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/net/RetrofitUtil.java:54` (RetrofitUtil.java:54; local analysis artifact, excluded from Git).

Success persists `acc_no`, alias, and portrait, then starts `MainActivity`. Automatic login reloads the stored email and password and calls `act_login` again. `UserManager` contains access/refresh-token fields, but searches of the decompiled Java sources found no call sites for their getters/setters outside that class. These names are not evidence of a working refresh-token API. See `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/account/a.java:42` (account/a.java:42; local analysis artifact, excluded from Git), `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/SplashActivity.java:81` (SplashActivity.java:81; local analysis artifact, excluded from Git), and `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/manager/UserManager.java:39` (UserManager.java:39; local analysis artifact, excluded from Git).

## Device discovery is MQTT

After HTTP login, `MainActivity` uses `acc_no` as its MQTT username and the original account password as its MQTT password. Therefore the app's observed lifecycle still needs the password after HTTP authentication; there is no demonstrated password-free session token. `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/MainActivity.java:733` (MainActivity.java:733; local analysis artifact, excluded from Git)

Discovery uses these account-scoped topics:

| Role | Topic or payload |
| --- | --- |
| Subscribe for snapshot | `skylink/things/client/<acc_no>/get/result` |
| Request snapshot | Publish `{}` to `skylink/things/client/<acc_no>/get` |
| Subscribe for updates | `skylink/things/client/<acc_no>/update/result` |

The app subscribes at QoS 1, then schedules the snapshot request 800 ms after successful snapshot subscription. That delay is app behavior, not proven protocol necessity. Topic construction is in `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/MainActivity.java:208` (MainActivity.java:208; local analysis artifact, excluded from Git); subscription and discovery sequencing are in `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/MainActivity.java:369` (MainActivity.java:369; local analysis artifact, excluded from Git) and `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/MainActivity.java:424` (MainActivity.java:424; local analysis artifact, excluded from Git). Broker/TLS, publication flags, and command behavior are covered in the separate protocol research.

Snapshot payloads are parsed as `{"data": [<SkyLinkDevice>, ...]}` and replace the in-memory device list. Update messages carry a `data` object parsed as one `SkyLinkDevice`, matched by `hub_id`. The model includes `hub_id`, `hub_name`, `hub_type`, `type`, `online`, `timestamp`, `reported`, `desired`, `firstdoor_name`, `seconddoor_name`, permissions/sharing fields, and location fields. The exact presence and types in real responses require a sanitized live capture. See `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/bean/TestData.java:7` (TestData.java:7; local analysis artifact, excluded from Git), `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/a.java:50` (a.java:50; local analysis artifact, excluded from Git), and `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/manager/SkyLinkDevice.java:9` (SkyLinkDevice.java:9; local analysis artifact, excluded from Git).

For types beginning `NOVA`, the app produces logical `NOVA_A` and `NOVA_B` entries according to which door-name fields are nonempty, cloning one physical hub record for door B. Therefore HA entity identity should include a door/channel suffix, while device identity can remain the physical `hub_id`. Do not use `hub_id` alone for every cover entity. `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/manager/DeviceManager.java:36` (DeviceManager.java:36; local analysis artifact, excluded from Git)

`hub_import` exists as a declared HTTP command with an empty response subclass, but no invocation was found in the decompiled Java sources. It is not the discovery mechanism traced from login. `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/net/ApiService.java:83` (ApiService.java:83; local analysis artifact, excluded from Git) and `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/bean/HubInServerBean.java:4` (HubInServerBean.java:4; local analysis artifact, excluded from Git)

## Other HTTP surface and scope boundary

`ApiService` declares account registration/verification/reset/change/delete, hub add/delete/import/share, door add/delete, camera operations, push notification configuration, voice-assistant PINs, app-version lookup, firmware lookup, and event-log retrieval. These declarations do not imply that HA needs to implement them. For an existing paired opener, the minimal path is HTTP login plus MQTT discovery/status/control. `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/net/ApiService.java:35` (ApiService.java:35; local analysis artifact, excluded from Git)

An optional read-only feature is `hub_event_log`: body fields `acc_no`, `hub_id`, and `num_of_event` (the app sends 200), with `REQ-DATA=acc_no`. This is traced to the event-log UI, but pagination and server limits are unverified. `artifacts/decompiled-3.8.9/sources/com/mtqqdemo/skylink/account/EventLogFragment.java:48` (EventLogFragment.java:48; local analysis artifact, excluded from Git)

## Consequences for the implementation plan

1. Replace speculative token-refresh logic with verified email/password login and secure local password retention needed for MQTT. Keep auth abstractions extensible if later traffic reveals another supported method.
2. Use `acc_no` as the account unique ID; verify it is unchanged on reauth. Separate invalid credentials from network, TLS, malformed response, and unknown result-code errors.
3. Start with a read-only local diagnostic client: login, subscribe, request snapshot, print only sanitized device type/state metadata. Test whether empty `pn_token` works without disturbing the mobile app before treating it as the final login contract.
4. Build request-signature tests with fixed timestamps and synthetic identifiers, using the public app signing constant recovered from locally ignored research material. The independent client includes this protocol constant; it is not an account credential. Do not publish credentials, raw account topics, or captured device data in fixtures.
5. Obtain live confirmation of certificate validation, login error codes, session coexistence with the phone, shared-account permissions, snapshot schema, reconnection behavior, and exact user's device type before enabling HA movement commands.
