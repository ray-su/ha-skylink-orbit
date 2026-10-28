# Skylink Orbit Home Assistant investigation

- [Recovered MQTT protocol, command payloads and door-state codes](research/orbit-mqtt-protocol.md)
- [Recovered HTTP login and request signing](research/orbit-auth-discovery.md)
- [Home Assistant architecture and implementation design](research/home-assistant-design.md)
- [GitHub hosting, HACS installation, and release workflow](research/github-installation-guide.md)
- [Initial upstream scaffold assessment](research/orbit-api-investigation.md)
- [APK versions, hashes and signer fingerprints](research/apk-provenance.json)

Status: Orbit Home Android 3.8.9 (79) and 3.5 (55) downloaded and decompiled. Signed HTTPS login and MQTT discovery/control traced in app code. DNS and TLS handshake checks performed; no account login, MQTT session, or garage movement performed. The user's iPhone does not prevent Android-based protocol research.

Local upstream reference: `upstream/ha-orbit/` at `df537b011f4673b082d1f9de87e109d270e4f23b` (ignored). App binaries, decompilation, and private captures belong in ignored `artifacts/`, never in source control.
