# Retail PS5 title-ID launching — interoperability research

Last reviewed: 2026-10-24.

## Requirement

Launch a specified game by its PS5/PS4 title identifier using an authenticated
network request from the Unfolded Circle Remote, independent of home-screen
position. Do not require jailbreaking, an external daemon or video decoding.
The existing local Remote Play setup, controller navigation and PSN game
library already work and must remain unchanged.

## Findings

1. **Sony PlayStation App** officially supports **Game Library → Purchased →
   Play on Console** for downloaded titles on a paired PS5.
   https://www.playstation.com/en-us/support/games/playstation-app-features/
2. **PXPlay PC v3.0.0+**, not Android v8.1.1, explicitly documents direct
   launching of recently played games and a `--title-id` argument.
   The PC 3.1.0 notes describe title launch after a PS5 login PIN is supplied.
   https://github.com/streamingdv/PSPlay-Application-Hosting/releases
3. User-provided **PXPlay Android v8.1.1** decompiled APK examined locally
   (Java, Smali and arm64 native libraries; neither binary nor its decompiled
   source is redistributed in this repository). Findings:
   - `sources/defpackage/ku3.java`: calls
     `GET https://m.np.playstation.com/api/gamelist/v2/users/me/titles`
     to obtain a *recently played game* including artwork. This is read-only.
   - `sources/defpackage/po4.java`: authenticates a PSN push WebSocket,
     creates a remotePlaySession and calls
     `POST https://web.np.playstation.com/api/cloudAssistedNavigation/v2/users/me/commands`.
     The observed command uses `commandType: "remotePlay"`, destination
     `"SQS"`, the PSN console `duid`, and a `parameters.initialParams`
     string containing the PSN account ID, Remote Play session ID, client
     platform and encrypted negotiation data.
     This is a **Remote Play join command**, *not a game-launch request*.
   - Searches across decompiled Java, Smali and ARM64 native libraries
     did not reveal a game-launch-specific command/payload, including no
     `psgm:play`, `playOnConsole`, `launchGame` or `launchTitle` marker.
     Absence of these markers is not absolute proof that no obfuscated
     implementation exists.
4. PS5 DevWiki documents the **console-internal URI**
   `psgm:play?id=<titleId>` for launching an installed game.
   This is not an authenticated PSN HTTP endpoint and cannot just be sent to
   the PS5 over the network:
   https://www.psdevwiki.com/ps5/URI
5. AnyPS5 is an executable relinker for Windows/Linux. It is not a retail-PS5
   remote-control client: https://github.com/boykopovar/AnyPS5
6. Jailbroken PS5 launcher APIs using `sceLncUtilLaunchApp` and payloads
   are not suitable for normal retail consoles.

## Implementation status

**No verified direct title-launch transport has been identified.** Therefore
we do NOT enable PLAY_MEDIA, set can_play on game-list entries, or send a
hypothetical Sony `commandType` to a live PSN account. Previously introduced
recorded UI-navigation macros have been removed (v0.3.1). Existing PSN
authentication and Remote Play button controls remain unchanged.

## What would unblock real implementation

Inspect PXPlay **PC** v3.x's game-launch network request (not the Android APK)
or the official PlayStation App's **Play on Console** request for an account
and console you control. Confirm:

- HTTP endpoint, method, request body (including title identifier format),
  authentication type, required headers and console `duid`;
- whether launch uses cloud-assisted navigation or a different PSN service;
- whether Remote Play session negotiation and PSN login PIN are prerequisites;
- response semantics and confirmation by checking the running app on PS5.

Only then implement a typed per-title launcher, with explicit errors, no
assumption that HTTP acceptance means game start, and unit tests with
redacted request fixtures. Never commit PSN tokens, pairing keys or private
traffic captures.
