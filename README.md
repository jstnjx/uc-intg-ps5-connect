# PlayStation 5 Control + PSN

An [Unfolded Circle](https://www.unfoldedcircle.com/) Remote Two / Remote 3 integration for PlayStation 5.

## 1. Features

- **Power:** Wake PS5, enter Rest Mode, toggle power.
- **Controls:** D-pad, face buttons, shoulders, PS, Options, Share, Touchpad, plus a native Remote entity with a controller layout and physical-button mappings.
- **Game launching:** Launch games directly by PlayStation title ID through Sony's PSN command service.
- **Game library:** Browse installed games and apps or recently played titles. Where available, show names and cover artwork.
- **Console identity:** Remember the paired console, account and PSN device ID; rediscover the same PS5 if its IP address changes.
- **Session handling:** Automatically disconnect idle Remote Play sessions; recover from transient PSN errors and limit repeated wake requests.
- **Sensors:**
  - Currently playing game
  - Active Title ID
  - PSN authentication
  - PSN API Health
  - Installed Titles count
  - Connection status
- **Selectors:**
  - **Profile:** Choose a Remote Play profile already paired with this PS5.
  - **Library View:** Choose the default browser view (Installed games & apps or Recently played).
- **Entities:** Media player, power switch, Remote, sensors and selectors. Each paired PS5 has its own entities.

PSN features require a valid NPSSO token. Local power and controller functions can be configured with a PSN Account ID instead.

**Game artwork and titles:** Installed titles are matched with Sony's paginated
PlayStation game-list metadata (localized names and cover images). The integration
caches lookups for 15 minutes. When Sony does not provide metadata for a title,
the browser shows **Unknown PlayStation title**, its title ID and a generic
gamepad icon; it does not fabricate a game name or cover. The **Recently played**
view remains available through the Library View selector.

**Select controls:** The Profile and Library View entities explicitly publish
their options on connection and subsequent state updates. The Profile selector
always includes the currently paired user.

## 2. Installation

1. Download **`uc-intg-ps5-control-aarch64.tar.gz`** from the [latest release](https://github.com/jstnjx/uc-intg-ps5-control/releases) or a successful [build workflow](https://github.com/jstnjx/uc-intg-ps5-control/actions/workflows/build.yml).
2. Open the Remote web configurator and go to **Settings → Integrations → Upload custom integration**. Upload the `.tar.gz` archive.
3. Start setup and enter your **PSN token (NPSSO)**. Alternatively, enter your **PSN Account ID** for local control only.
4. Select your PS5. If it is not listed, choose **Enter IP address manually**.
5. On the PS5, open **Settings → System → Remote Play → Link Device**. Enter the displayed eight-digit PIN to finish pairing.

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

The installable archive will be created as `uc-intg-ps5-control-aarch64.tar.gz` in the repository root. Upload this file using the installation steps above.

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
