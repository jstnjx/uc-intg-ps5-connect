# Sony PS5 direct title launching — verified protocol reconstruction

Research date: 2026-10-24. Based on user-supplied signed
PXPlay **Linux v3.1.1** AppImage. The AppImage detached signature validated
against the archive's bundled PGP key (fingerprint
8F03361B10CEAC9FDA4D0D7A67F350DD52D2A789). This verifies that the
archive matches that key; it does not independently establish key ownership.

## Why this works

The desktop application's `remoteplaylib-1.0.0-optimized.jar` exposes a
`PsnApiUtil.launchTitleOnConsole` method. Static Java bytecode inspection
established the concrete request shape, endpoint, title-ID normalization,
command ID and status polling semantics. No proprietary code was copied
into this repository and no binary/source archive is redistributed.

### Command submission

```http
POST https://web.np.playstation.com/api/cloudAssistedNavigation/v2/users/me/commands
Authorization: Bearer <PSN OAuth access token>
Accept: application/json
Content-Type: application/json; charset=utf-8

{
  "commandDetail": {
    "platform": "PS5",
    "duid": "<PSN console DUID>",
    "commandType": "launchTitle",
    "parameters": {
      "titleIds": ["PPSA28183"]
    }
  }
}
```

The PSN game's `_00` suffix is stripped, as in the reference client.
A successful submission returns `commandId`. HTTP 2xx **alone is not
success**.

### Command status

```http
GET https://web.np.playstation.com/api/cloudAssistedNavigation/v2/users/me/commands?platform=PS5&commandIds=<id>&includeFields=actions%2CcommandDetail&commandStatus=executing%2Ccompleted
Authorization: Bearer <PSN OAuth access token>
```

A `commands[0]`, `command` or top-level result contains `commandStatus`,
`commandResult` and `actions[0].action` (optionally). The reference
client checks for `completed` vs `failed`, polling approximately every
1.5 seconds for up to 10 seconds. This integration uses up to 12 seconds.

### PSN console discovery

```http
GET https://web.np.playstation.com/api/cloudAssistedNavigation/v2/users/me/clients?platform=PS5&includeFields=device&limit=10&offset=0
Authorization: Bearer <PSN OAuth access token>
```

Response `clients` contain `duid`, `platform`, and `device.name`. The
integration refuses to auto-select when multiple PS5s are present and the
local console name cannot be uniquely matched, unless an explicit DUID is
configured.

### OAuth

The desktop client uses the Sony mobile OAuth application ID
`09515159-7237-4370-9b40-3806e67c0891`, scope
`psn:mobile.v2.core psn:clientapp`, which matches the existing
PSNAWP account session in this integration. No second sign-in is needed.

## Implementation

The independent Python client is `intg-ps5/game_launch.py`. The existing
`PSNAccount` uses its refresh-aware PSNAWP authenticator, and the media-player
exposes `PLAY_MEDIA` and `can_play` on game entries. There are no controller
macros, jailbreak APIs or additional helper daemons.

The request format has been confirmed **statically** from the supplied
desktop distribution. Success on the user's retail PS5 has **not yet been
established** and must be tested. Game availability/installation, PSN account
entitlements, login state and Sony API changes can affect the outcome.
Never commit NPSSO cookies, Sony OAuth tokens or paired-console credentials.
