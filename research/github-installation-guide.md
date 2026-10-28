# Hosting and installing the Skylink Orbit integration

Checked 2026-10-27 against official Home Assistant and HACS documentation. This is a proposed distribution workflow; no repository has been created or published, and no integration has been installed.

## Recommended route

Keep the integration in your own **public GitHub repository**, then add that URL to HACS as a **custom repository**. This gives you installation and update management without submitting it to Home Assistant core or applying for inclusion in HACS's default catalog. HACS documents custom repositories as a separate installation route. [HACS custom repositories](https://www.hacs.xyz/docs/faq/custom_repositories/)

If you want the repository private, use manual installation instead: **HACS does not support private repositories**, including a private repository owned by the signed-in user. [HACS private repositories](https://www.hacs.xyz/docs/faq/private_repositories/)

## Proposed repository layout

```text
ha-skylink-orbit/                 # proposed repository name
├── README.md                     # setup, compatibility, known limitations
├── LICENSE
├── hacs.json
├── pyproject.toml                # development/test configuration
├── .gitignore
├── .github/workflows/
│   ├── tests.yml
│   └── validate.yml              # HACS + hassfest
├── custom_components/
│   └── skylink_orbit/            # integration domain; keep stable
│       ├── __init__.py
│       ├── manifest.json
│       ├── const.py
│       ├── config_flow.py
│       ├── coordinator.py
│       ├── cover.py
│       ├── button.py
│       ├── diagnostics.py
│       ├── strings.json
│       ├── translations/en.json
│       ├── brand/icon.png
│       └── orbitclient/          # bundled async protocol package
│           ├── __init__.py
│           ├── auth.py
│           ├── mqtt.py
│           └── models.py
└── tests/
    └── components/skylink_orbit/
```

HACS expects one integration per repository, with its runtime files beneath `custom_components/<domain>/`. The integration manifest needs `domain`, `name`, `version`, `documentation`, `issue_tracker`, and `codeowners`; our implementation also needs `config_flow: true`, `iot_class: cloud_push`, and any external Python requirements. [HACS integration packaging](https://www.hacs.xyz/docs/publish/integration/)

**Design recommendation:** initially bundle `orbitclient` inside the integration while keeping its API independent of HA. That avoids requiring a second repository or PyPI release and ensures HACS copies it. External dependencies still belong in `manifest.json` requirements. Separating the client onto PyPI is a later choice; core-contribution library requirements are not a prerequisite for installing your own custom integration. Custom integration loading is based on the local integration folder. [HA file structure](https://contributors.home-assistant.io/core/integrations/file-structure/)

Put `hacs.json` at the repository root. Start with the name and add the `homeassistant` minimum-version field once tested. Leave `content_in_root` and `zip_release` unset for this ordinary directory layout. There is no need to build a special release ZIP. [HACS repository metadata](https://www.hacs.xyz/docs/publish/start/)

Keep credentials, account/device captures, APKs, decompiled vendor source, and local research artifacts out of the published repository. Publish the integration implementation, sanitized tests, and useful documentation.

## Development and release workflow

1. Choose the supported Home Assistant version based on your installed system, then test against that release and a newer supported release. Current developer pages may describe APIs newer than the installed stable version.
2. Build and test login, MQTT discovery, state mapping, disconnect/reconnect, and reauthentication. Add garage movement only after verifying the actual model's command semantics. The recovered app uses a door activation command, so first-release behavior must not claim unverified independent open/close operations.
3. Run protocol/HA tests plus HACS validation and hassfest in GitHub Actions. HACS's action supports `category: integration` and checks pushes/PRs; its checks validate packaging, not actual door behavior. [HACS validation](https://www.hacs.xyz/docs/publish/action/) and [hassfest for custom integrations](https://contributors.home-assistant.io/blog/2020/04/16/hassfest/)
4. For the first tested release, set a version such as `0.1.0` in the manifest, commit the tested files, and publish a GitHub Release tagged `v0.1.0`. Keep release tags and manifest versions consistent. For subsequent releases, increment both and explain changes in the release notes.

GitHub Releases are optional, but recommended here for clear updates and rollback. If using releases, publishing a Git tag alone is insufficient for HACS's release-version tracking; publish a GitHub Release too. [HACS version handling](https://www.hacs.xyz/docs/publish/start/)

## Install through HACS

These steps target standalone HACS on Home Assistant 2026.10 or earlier and assume HACS is already installed. If needed, first follow its [official installation instructions](https://www.hacs.xyz/docs/use/download/download/). That page already describes a Marketplace transition for 2026.11; use the instructions matching the version actually installed rather than assuming the newer UI.

1. Open **HACS** in Home Assistant.
2. Open the top-right menu → **Custom repositories**.
3. Enter your repository URL, choose **Integration**, and select **Add**. [Official custom-repository steps](https://www.hacs.xyz/docs/faq/custom_repositories/)
4. Find **Skylink Orbit**, open its details, and select **Download**, choosing the tested release.
5. Restart Home Assistant when prompted. Downloading files alone does not activate a newly installed integration. [HACS download and restart workflow](https://www.hacs.xyz/docs/use/repositories/dashboard/)
6. Open **Settings → Devices & services → Add integration → Skylink Orbit**. The planned config flow asks for your Orbit email and password locally and discovers your existing devices. HACS handles the files; the integration's config flow handles account setup. [HACS integration configuration](https://www.hacs.xyz/docs/use/repositories/type/integration/)

For updates, HACS checks repository metadata and exposes update entities. Install the offered version and restart as requested; you should not need to enter the account again if the integration's config-entry migration remains compatible. [HACS update entities](https://www.hacs.xyz/docs/use/entities/update/)

To check immediately, use the repository menu's **Update information**. That refreshes metadata only; then download/redownload the selected version. To roll back, use **Redownload → Need a different version?**, choose the previous compatible release, and restart. A rollback after a breaking config-entry migration may require restoring a backup. [HACS version selection](https://www.hacs.xyz/docs/use/repositories/dashboard/)

## Manual installation, including private GitHub repositories

Download the selected release/source archive while signed into GitHub, or clone it locally. Copy **only the `skylink_orbit` folder inside `custom_components`** into your Home Assistant configuration directory's `custom_components` folder, creating that parent folder if needed. The resulting path must be:

```text
<Home Assistant configuration directory>/custom_components/skylink_orbit/manifest.json
```

Do not add an extra repository-name directory. Home Assistant loads custom integrations from this location. [HA integration loading](https://contributors.home-assistant.io/core/integrations/file-structure/)

Restart Home Assistant, then complete the same **Add integration** flow. For manual updates, replace the integration folder with the new release, restart, and verify it loads. Manual installation does not provide HACS-managed update notices; keep track of your own releases. Use the actual configuration directory for your installation, rather than assuming its host-machine path is `/config`.
