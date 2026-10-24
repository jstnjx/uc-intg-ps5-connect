# PS5 Connect

PlayStation 5 integration for the [Unfolded Circle Remote Two and Remote 3](https://www.unfoldedcircle.com/). Control your console locally and access your games and PlayStation Network data from the Remote.

## 1. Features

- **PS5 controls:** Wake, Rest Mode, D-pad, face buttons, triggers, PS button, Options, Share and Touchpad.
- **Game browser:** Browse installed and recently played games in the media player, with titles and cover art where available. Launch games directly.
- **PSN data:** Current game, playtime, game details and trophy progress.
- **Entities:** Remote, media player, power switch, sensors and profile/library selectors.

## 2. Installation

1. Get `uc-intg-ps5-connect-v1.0.0-aarch64.tar.gz` from [Releases](https://github.com/jstnjx/uc-intg-ps5-control/releases) or a successful [build](https://github.com/jstnjx/uc-intg-ps5-control/actions/workflows/build.yml).
2. In the Remote web configurator, open **Settings → Integrations → Upload custom integration** and upload the archive.
3. Start setup. Enter a **PSN NPSSO token** for PSN features, or a **PSN Account ID** for local controls only.
4. Select your PS5 (or enter its IP address), then enter the eight-digit code from **PS5 Settings → System → Remote Play → Link Device**.

Enable **Remote Play** on the console first. To wake it from Rest Mode, enable **Stay Connected to the Internet** and **Enable Turning On PS5 from Network**.

**PSN credentials:** Get your NPSSO token while signed in at [Sony's account endpoint](https://ca.account.sony.com/api/v1/ssocookie). You can look up a Base64 PSN Account ID using [PSNTools](https://www.psntools.com/psn/checker); decimal IDs also work. Never enter your NPSSO token or password into third-party lookup sites.

To change settings later, use **Update information for selected device**. You only need to pair again if the console registration changes.

## 3. Manual Build

Requires Docker with Linux ARM64 support.

```bash
git clone https://github.com/jstnjx/uc-intg-ps5-control.git
cd uc-intg-ps5-control
./build.sh
```

Output: `uc-intg-ps5-connect-v1.0.0-aarch64.tar.gz`.

## 4. Acknowledgments

Based in part on [uc-intg-psn](https://github.com/JackJPowell/uc-intg-psn) and [ps5-control-uc](https://github.com/sbr-labs/ps5-control-uc). Uses [ucapi-framework](https://github.com/JackJPowell/ucapi-framework), [Unfolded Circle's Integration API](https://github.com/unfoldedcircle/integration-python-library), [pyremoteplay](https://github.com/ktnrg45/pyremoteplay) and [PSNAWP](https://github.com/isFakeAccount/psnawp). [Jeffser's pyremoteplay fork](https://github.com/Jeffser/pyremoteplay) and PXPlay informed compatibility research.

## 5. Licenses

Original and MPL-derived code: [MPL 2.0](LICENSE). PS5 control components: [MIT](LICENSE.sbr). pyremoteplay: [GPL v3](LICENSE.pyremoteplay). See [Third-party Notices](THIRD_PARTY_NOTICES.md) for other dependencies and license requirements.
