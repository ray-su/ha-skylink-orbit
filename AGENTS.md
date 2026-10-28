# Local developer tooling

- Use the repository's uv environment and lockfile. Prefer already installed developer tools.
- Homebrew is under `/opt/homebrew/bin`; add it to PATH before declaring tools missing.
- Use `/opt/homebrew/bin/gh` for supported GitHub operations; authentication is shared across worktrees.
- Before reporting GitHub unavailable, inspect `git remote -v`, test `git ls-remote --exit-code origin HEAD`, and run `gh auth status`. Distinguish an empty remote from authentication failure.
- Request reauthentication only after actual authentication/remote access failures; include the failing command and error.

## Agent skills

### Issue tracker

Track work in this repository's GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the five default triage labels. See `docs/agents/triage-labels.md`.

### Domain docs

Use one root glossary and `docs/adr/`. See `docs/agents/domain.md`.

## Protocol work

Read `research/orbit-mqtt-protocol.md` and `research/orbit-auth-discovery.md` before changing wire behavior. `docs/build-contract.md` defines the approved first release and testing boundaries.
Keep credentials, app binaries, decompiled sources and raw account captures in ignored local storage. Keep TLS certificate and hostname validation enabled. Motion commands are never retried or replayed automatically.
