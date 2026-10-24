# PlayStation 5 Control + PSN for Unfolded Circle Remote Two / Remote 3

A **single, self-contained Remote integration** combining [SBR PS5 Control](https://github.com/sbr-labs/ps5-control-uc) and [Jack Powell's PSN integration](https://github.com/JackJPowell/uc-intg-psn).

The Remote itself performs LAN Remote Play registration, session management, button dispatch and optional PlayStation Network queries. **No Linux box, Docker daemon, Home Assistant instance, MQTT broker or HTTP bridge is needed.**

## Features

| Capability | Local Remote Play | Sony PSN account |
|---|---:|---:|
| Power on from rest / enter rest mode | ✓ | — |
| D-pad, Cross, Circle, Square, Triangle | ✓ | — |
| L1/R1, L2/R2, L3/R3 | ✓ | — |
| PS, Options, Share, Touchpad | ✓ | — |
| UC Home / Menu / Info / Back mappings | ✓ | — |
| Button repeats while navigating | ✓ | — |
| Force disconnect/reconnect Remote Play session | ✓ | — |
| Console's local online/offline status | ✓ | — |
| Current game title, platform and cover art | — | ✓ |
| Played-game library browsing | — | ✓ |
| Direct PSN title-ID game launching | — | ✓ (new in v0.4.0; pending hardware validation) |
| PSN authentication health sensor | — | ✓ |
| Power switch + media player entities | ✓ | Optional |

Streaming apps may not reliably report their titles to PSN. Game launching sends the retail PS5 a Sony cloud-assisted navigation command and waits for confirmation. This path has been reverse-engineered and unit-tested, but not yet validated on a physical console.

## PS5 Remote Play protocol compatibility

The PS5 network-test handshake used by upstream `pyremoteplay 0.7.6` sends
an outdated client version (`7`) and is rejected by newer PS5 firmware.
This manifests as ten `Version not accepted` errors followed by
`Network Test timed out. Using Default MTU and RTT`, even when Remote Play
authentication succeeds. See [pyremoteplay issue #22](https://github.com/ktnrg45/pyremoteplay/issues/22).

This integration pins [Jeffser's pyremoteplay fork](https://github.com/Jeffser/pyremoteplay)
at commit `06ff76480c8cc8b35624a7aecf57ebe70eb8e915`, which selects client version **12** for PS5 and **9** for
PS4 during the test handshake. This changes only the Python dependency inside
the Remote integration. No additional daemon, host, or PS5 re-pairing is necessary.

PSN authentication sensor subscriptions are also explicitly routed back to the
parent PS5 device when the framework sees an `_authentication` suffix.

**Note:** The fix is based on an upstream community patch and has not yet been
validated with the specific PS5 firmware on your physical Remote 3. The network
test can still time out for other network reasons.

## Installation

1. Open [GitHub Actions](https://github.com/jstnjx/uc-intg-ps5-control/actions/workflows/build.yml) and download the **uc-intg-ps5-control-aarch64** artifact from a successful run. Extract the outer artifact ZIP if GitHub supplies one; the file you upload is **`uc-intg-ps5-control-aarch64.tar.gz`**. A tagged release attaches the same installable archive.
2. On the Remote Two / Remote 3 web configurator, go to **Settings → Integrations → Upload custom integration** and select the `.tar.gz`.
3. Run setup for **PlayStation 5 Control + PSN**. Supply either an NPSSO cookie or your PSN Account ID. The cookie enables PSN presence and library browsing; the Account ID alone supports local console control.
4. Leave **Pair PS5 for local navigation and power** checked. Select a discovered console or **Enter IP address manually**. Ensure the PS5 and Remote are on the same LAN.
5. Enable **Settings → System → Remote Play → Enable Remote Play** on the PS5. Set **Stay Connected to the Internet** and **Enable Turning On PS5 from Network** under Rest Mode features.
6. On PS5, open **Settings → System → Remote Play → Link Device**, get a **new eight-digit PIN**, and enter it in the Remote setup flow. Registration is performed on the Remote and credentials are stored in the integration's local configuration.

### PSN account ID and NPSSO

Obtain your NPSSO cookie from [Sony's signed-in account endpoint](https://ca.account.sony.com/api/v1/ssocookie) in a web browser logged into PSN. This optional cookie allows PSN account verification and populates game artwork, presence, and game browsing. It is stored in the Remote's configuration. The account ID may be supplied as a decimal number or Sony's eight-byte Base64 representation, and **must be the same account linked on your PS5**.

If you don't supply NPSSO, provide Account ID and pair normally. PSN-dependent features are disabled, but local PS5 controls remain functional. If PSN credentials expire, local button/power control continues to work.

## Entities and command mapping

- **Media player:** power, d-pad, Home → PS, Back → Circle, OK → Cross, Menu → Options, Info → Touchpad, Play/Pause → Cross (best effort, not a genuine media-playback API), and simple commands for all supported face/shoulder buttons.
- **Power switch:** on/off/toggle.
- **Game title sensor:** title of the currently active PSN game, when available.
- **Authentication sensor:** PSN status (valid, expired, or not configured).

Simple commands also include `SESSION_DISCONNECT` (suspends for two minutes) and `SESSION_RECONNECT`. Disconnecting the session can be necessary to access certain PS5 settings that Remote Play locks out.

## Launch PS5 games directly by title

Version 0.4.0 implements the **Sony cloud-assisted navigation launch-title
protocol** recovered from the user-provided signed PXPlay Linux v3.1.1 build.
It launches by title ID, **not** by home-screen navigation, and requires no
third-party daemon, macro, streaming UI or jailbreak.

1. Pair the PS5 as usual and configure your NPSSO PSN token.
2. On the Remote media player, open **Played games**.
3. Select a title and press **Play**. The integration discovers the console's
   PSN `duid`, requests `launchTitle` and polls Sony's command status.
4. Sony must return `completed` before the integration reports success.
   A failed, invalid or timed-out command returns an error (and is logged).

For accounts with exactly one PS5, the target is selected automatically. If
the account has several PS5 consoles, the integration matches the console's
local DDP hostname against PSN device names; if no unique match is found it
**refuses** to launch anything to avoid targeting the wrong console. The
optional **PSN console device ID** setup field pins a specific PSN DUID.

Supported game IDs look like `PPSA28183_00` or `CUSA00605_00`; Sony
requires the base ID without the `_00` suffix. Games must actually be
installed on the target console. The running account needs appropriate access.

**Testing status:** request structure and error/status logic have offline unit
tests, but Sony may change undocumented API behavior, and the flow has **not
yet been tested on the user's physical PS5**. See
[research and protocol details](docs/game-launch-research.md).

## How local control works

```text
UC Remote 3 / Remote Two
  └── PS5 integration process
       ├── ucapi-framework: config, entities, state and setup
       ├── pyremoteplay: PS5 LAN pairing, wake, standby, Remote Play buttons
       └── PSNAWP (optional): PSN presence, covers and game browser
             ↳ PS5 via LAN / Sony PSN via Internet
```

Session establishment occurs when the first control command needs it; the session is reused and is automatically released after 90 seconds of inactivity. A warmup after PS5 wake attempts to reconnect proactively. Remote Play's controller stream uses very low video settings, but it **still opens a Remote Play session**. It will compete with another Remote Play client (such as the official app) and could consume noticeable CPU/RAM on a Remote. No console video is displayed or stored.

### Troubleshooting

- **PS5 not found:** enter its IPv4 address manually. Confirm Remote Play is enabled and LAN UDP 9295/9302 and associated Remote Play traffic are not blocked.
- **Pair failed:** ensure the console is awake, use the PSN account ID registered on it, generate a NEW PIN and retry; PINs expire rapidly.
- **Buttons slow on first use:** a cold Remote Play handshake is needed; subsequent buttons share the session.
- **Cannot open PS5 video settings:** use `SESSION_DISCONNECT` to release Remote Play. `SESSION_RECONNECT` resumes.
- **No game artwork:** provide an NPSSO cookie and confirm your PSN privacy/presence settings. Sony does not consistently surface streaming apps.
- **PSN authentication expired:** rerun setup with a fresh NPSSO. Local control remains available; PSN features may not be.

## Development and build

Python 3.11+ is required. For desktop development:

```bash
python3.11 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python -m compileall -q intg-ps5
```

On an ARM64-capable Docker host (including Apple Silicon with Docker Desktop), you can also build an installable archive locally with \`./build.sh\`. On x86 Linux, binfmt/QEMU for ARM64 must be installed first.\n\n**GitHub Actions builds a native aarch64 `--onedir` PyInstaller bundle** using `unfoldedcircle/r2-pyinstaller`. Upload the generated tarball, not the repository ZIP. The repository also provides an optional [`unfurled`](https://github.com/JackJPowell/unfurled)-based diagnostic for Remote firmware, activities and integration lookup:

```bash
python tools/diagnose_remote.py http://REMOTE_IP:80 YOUR_REMOTE_API_KEY
```

Unfurled is intentionally a developer-side verification tool; it is not an external daemon and is not required at runtime on the Remote.

## Acknowledgments and licensing

- [sbr-labs/ps5-control-uc](https://github.com/sbr-labs/ps5-control-uc): MIT (see `LICENSE.sbr`), adapted controller behavior and button mappings.
- [JackJPowell/uc-intg-psn](https://github.com/JackJPowell/uc-intg-psn): MPL-2.0 (see `LICENSE`), PSN API, entities, setup structure and framework integration.
- [JackJPowell/ucapi-framework](https://github.com/JackJPowell/ucapi-framework): declarative entity and lifecycle framework.
- [JackJPowell/unfurled](https://github.com/JackJPowell/unfurled): optional external validation tooling.
- [pyremoteplay](https://github.com/ktnrg45/pyremoteplay) is GPL-3.0. Distributing a built binary containing this dependency requires complying with that license, including applicable source/offers and notices. See the respective upstream repositories for source.

This project is independent of Sony and Unfolded Circle and is not endorsed by either.

**Hardware status:** The automatic build/test workflow and offline tests do not replace a real Remote 3 + PS5 pairing, navigation, standby and wake validation.
