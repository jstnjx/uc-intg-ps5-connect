# PS5 Connect

An [Unfolded Circle](https://www.unfoldedcircle.com/) Remote Two / Remote 3 integration for PlayStation 5.

## 1. Features

- **Power:** Wake PS5, enter Rest Mode, toggle power.
- **Controls:** D-pad, face buttons, shoulders, PS, Options, Share, Touchpad, plus a native Remote entity with a controller layout and physical-button mappings.
- **Game launching:** Launch games directly by PlayStation title ID through Sony's PSN command service.
- **Game library:** Browse installed games and apps or recently played titles, with cleaned names and no subtitles. Enrich artwork from Sony's game list, trophy API and PlayStation Store when available.
- **Game Library Remote:** Launch installed games from 4-column × 6-row native Qt StyledText buttons, with centered 85×85 SVG covers.
- **Scheduled refresh:** Automatically regenerate game pages every day (03:00 Europe/Berlin by default), updating the configured Remote in place through Core REST. Time and timezone are configurable during setup.
- **Game details:** PSN playtime, play count, first played and last played dates when Sony reports them.
- **Trophies:** Sensors for trophy level, total earned, platinum trophies and active-game completion percentage.
- **Console identity:** Remember the paired console, account and PSN device ID; rediscover the same PS5 if its IP address changes.
- **Session handling:** Automatically disconnect idle Remote Play sessions; recover from transient PSN errors and limit repeated wake requests.
- **Sensors:**
  - Currently playing game
  - Active Title ID
  - PSN authentication
  - PSN API Health
  - Installed Titles count
  - Connection status
  - Trophy level, total earned, platinum trophies, current-game completion
- **Selectors:**
  - **Profile:** Choose a Remote Play profile already paired with this PS5.
  - **Library View:** Choose the default browser view (Installed games & apps or Recently played).
- **Entities:** Media player, power switch, Remote, sensors and selectors. Each paired PS5 has its own entities.

PSN features require a valid NPSSO token. Local power and controller functions can be configured with a PSN Account ID instead.

**Game artwork and titles:** Installed games are matched with Sony's game-list
metadata, PSNAWP play-history records, trophy title information, and where available
the older PlayStation Store Chihiro image endpoint
([research reference](https://github.com/hmn/home-assistant-config/issues/9)).
The cache refreshes approximately every 15 minutes. Games without returned names
or artwork cannot be identified reliably: the media browser uses a generic title
and icon, with no subtitles. The title ID remains the internal launch identifier.

**Game Library Remote layout:** Each page has 24 native text buttons on a 4×6
grid. The button strings contain Base64 SVG icons through Qt StyledText's
`<img>` support; image size is **85×85 pixels**, centered in each button.
Game-cover thumbnails are downscaled before being embedded in SVGs, with
an SVG title fallback when Sony's metadata is missing. This uses the same
technique as [UC Custom Select](https://github.com/jstnjx/uc-intg-custom-select)
and stock [remote-ui](https://github.com/unfoldedcircle/remote-ui).

**Automatic updates:** Daily game-library regeneration is optional and can be enabled during setup. When enabled, it runs at 03:00 Europe/Berlin by default (configurable). During setup, enable the daily game-library refresh
(default: **03:00 Europe/Berlin**) and enter the Remote address and **Web
Configurator PIN**. The PIN creates a dedicated Core API key, which is saved
in integration configuration; the PIN itself is not stored. Daily refresh
loads the latest installed games, regenerates artwork and updates the
existing Remote's generated `games_*` pages through Core REST. The entity does
not need to be removed and re-added. User-created pages are preserved.
If Core is unreachable, the integration retries later rather than deleting
pages. The Remote address and Web Configurator PIN are **not required** for
regular PS5 control, pairing or PSN browsing. Enter them only if you enable
automatic daily game-library updates.

**Select controls:** The Profile and Library View entities explicitly publish
their options on connection and subsequent state updates. The Profile selector
always includes the currently paired user.

## 2. Installation

1. Download **`uc-intg-ps5-connect-aarch64.tar.gz`** from the [latest release](https://github.com/jstnjx/uc-intg-ps5-control/releases) or a successful [build workflow](https://github.com/jstnjx/uc-intg-ps5-control/actions/workflows/build.yml).
2. Open the Remote web configurator and go to **Settings → Integrations → Upload custom integration**. Upload the `.tar.gz` archive.
3. Start setup and enter your **PSN token (NPSSO)**. Alternatively, enter your **PSN Account ID** for local control only. **Daily game-library updates are optional**: enable them and enter the Remote address and Web Configurator PIN only if desired.
4. Select your PS5. If it is not listed, choose **Enter IP address manually**.
5. On the PS5, open **Settings → System → Remote Play → Link Device**. Enter the displayed eight-digit PIN to finish pairing.

**Changing settings later:** Choose **Update information for selected device** in integration setup. Previously saved PSN credentials and pairing are reused; enter only the fields you want to change. Enable **Re-pair PS5** only when you actually need to register Remote Play again. No second console discovery or pairing PIN is needed for routine edits.

Make sure **Enable Remote Play** is turned on in your PS5 settings. For waking the console from Rest Mode, enable **Stay Connected to the Internet** and **Enable Turning On PS5 from Network** in Rest Mode settings.

To obtain your NPSSO token, sign in to PlayStation Network in your browser and open [Sony's NPSSO endpoint](https://ca.account.sony.com/api/v1/ssocookie). Keep the token private.

To find your **PSN Account ID (Base64)** instead, use the [PSNTools PSN Checker](https://www.psntools.com/psn/checker). The integration accepts either a Base64-encoded or decimal PSN Account ID. Do not enter your PSN password or NPSSO token into third-party lookup services.

## 3. Manual Build

**Requirements:** Git, Docker and a host capable of running Linux ARM64 containers. On Apple Silicon, Docker Desktop can run the build image natively.

```bash
git clone https://github.com/jstnjx/uc-intg-ps5-control.git
cd uc-intg-ps5-control
./build.sh
```

The installable archive will be created as `uc-intg-ps5-connect-aarch64.tar.gz` in the repository root. Upload this file using the installation steps above.

To run source checks with Python 3.11+:

```bash
python3 -m compileall -q intg-ps5 tools
python3 -m unittest discover -s tests -v
```

## 4. Acknowledgments

- [JackJPowell/uc-intg-psn](https://github.com/JackJPowell/uc-intg-psn) and [ucapi-framework](https://github.com/JackJPowell/ucapi-framework) — PSN integration and UC framework.
- [sbr-labs/ps5-control-uc](https://github.com/sbr-labs/ps5-control-uc) — PS5 controller integration.
- [ktnrg45/pyremoteplay](https://github.com/ktnrg45/pyremoteplay) and [Jeffser/pyremoteplay](https://github.com/Jeffser/pyremoteplay) — local Remote Play implementation and protocol compatibility fixes.
- [PSNAWP](https://github.com/isFakeAccount/psnawp) — PlayStation Network API client.
- [Unfolded Circle Integration API](https://github.com/unfoldedcircle/integration-python-library) — Remote entity API.
- PXPlay desktop — interoperability research reference for PlayStation's title-launch and installed-title endpoints. No PXPlay source code is included.

## 5. Licenses

This integration's original and MPL-derived source is distributed under the [Mozilla Public License 2.0](LICENSE). Components retain their respective licenses:

- **PS5 Control components:** [MIT](LICENSE.sbr).
- **pyremoteplay:** [GNU GPL v3](LICENSE.pyremoteplay).
- **UC Integration API, ucapi-framework, PSNAWP and other dependencies:** their upstream licenses apply.

See [Third-party Notices](THIRD_PARTY_NOTICES.md) for sources and license information. Redistributing binaries that bundle GPL-licensed dependencies carries applicable source-availability and license obligations.
