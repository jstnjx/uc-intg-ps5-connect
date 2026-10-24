"""Launch a PS4/PS5 title using Sony's cloud-assisted navigation service.

Protocol reconstructed from the user's signed PXPlay Linux 3.1.1 distribution,
specifically the PSN API client. Independent implementation: no PXPlay code
is distributed or loaded. Does not control the PS5 home-screen cursor.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from urllib.parse import urljoin

_LOG = logging.getLogger(__name__)
_BASE = "https://web.np.playstation.com/api/cloudAssistedNavigation/v2/users/me/"
_USER_AGENT = "RpNetHttpUtilImpl"
_TITLE = re.compile(r"^[A-Z]{4}[0-9]{5}(?:_00)?$")
_DUID = re.compile(r"^[A-Za-z0-9._:-]{8,256}$")
_REQUEST_TIMEOUT = 15
_STATUS_TIMEOUT = 12
_POLL_INTERVAL = 1.5


class GameLaunchError(RuntimeError):
    """PSN launch rejected, unavailable, or not confirmed."""


class PSNAuthorizationError(GameLaunchError):
    """The PSN access token was rejected; caller may renew and retry once."""


class ConsoleSelectionError(GameLaunchError):
    """No unambiguous PSN console device could be selected."""


class GameLaunchTimeout(GameLaunchError):
    """Sony did not report a completed command within the allowed time."""


def normalize_title_id(value: object) -> str:
    title_id = str(value or "").strip().upper()
    if not _TITLE.fullmatch(title_id):
        raise ValueError("Unsupported or invalid PlayStation title ID")
    return title_id.removesuffix("_00")


def build_launch_payload(duid: str, title_id: object) -> dict:
    if not isinstance(duid, str) or not _DUID.fullmatch(duid.strip()):
        raise ValueError("Invalid PSN console device identifier")
    return {
        "commandDetail": {
            "platform": "PS5",
            "duid": duid.strip(),
            "commandType": "launchTitle",
            "parameters": {"titleIds": [normalize_title_id(title_id)]},
        }
    }


def _headers() -> dict[str, str]:
    return {"Accept": "application/json", "User-Agent": _USER_AGENT}


def _response_json(response, subject: str) -> dict:
    status = response.status_code
    if status in (401, 403):
        raise PSNAuthorizationError(f"PSN authorization rejected for {subject} (HTTP {status})")
    if not 200 <= status < 300:
        raise GameLaunchError(f"PSN {subject} failed (HTTP {status})")
    try:
        data = response.json()
    except (TypeError, ValueError) as error:
        raise GameLaunchError(f"PSN returned invalid JSON for {subject}") from error
    if not isinstance(data, dict):
        raise GameLaunchError(f"PSN returned an invalid {subject} response")
    return data


def _read_clients(authenticator, *, include_system_data: bool = False) -> list[dict]:
    consoles: list[dict] = []
    known: set[str] = set()
    for offset in range(0, 50, 10):
        response = authenticator.get(
            url=urljoin(_BASE, "clients"),
            params={"platform": "PS5", "includeFields": "device,systemData" if include_system_data else "device", "limit": 10, "offset": offset},
            headers=_headers(),
            allow_redirects=False,
            timeout=_REQUEST_TIMEOUT,
        )
        data = _response_json(response, "PS5 console discovery")
        batch = data.get("clients", [])
        if isinstance(batch, dict):
            batch = batch.get("clients", [])
        if not isinstance(batch, list):
            raise GameLaunchError("PSN console list has an unexpected format")
        for client in batch:
            if not isinstance(client, dict):
                continue
            duid = client.get("duid")
            platform = str(client.get("platform") or "PS5").upper()
            if not isinstance(duid, str) or not _DUID.fullmatch(duid) or platform not in ("PS5", "PROSPERO"):
                continue
            if duid not in known:
                consoles.append(client)
                known.add(duid)
        if len(batch) < 10:
            break
    return consoles


def _console_name(client: dict) -> str:
    device = client.get("device")
    return str(device.get("name") or "").strip() if isinstance(device, dict) else ""


def choose_console(
    clients: list[dict], *,
    requested_duid: str = "",
    local_name: str = "",
) -> str:
    if not clients:
        raise ConsoleSelectionError("No PS5 is registered in the Sony account")
    if requested_duid:
        matches = [x for x in clients if str(x.get("duid") or "").casefold() == requested_duid.casefold()]
        if len(matches) != 1:
            raise ConsoleSelectionError("Configured PS5 DUID was not found in the PSN account")
        return str(matches[0]["duid"])
    if len(clients) == 1:
        return str(clients[0]["duid"])
    if local_name:
        matches = [x for x in clients if _console_name(x).casefold() == local_name.casefold()]
        if len(matches) == 1:
            return str(matches[0]["duid"])
    raise ConsoleSelectionError(
        "Several PS5 consoles are registered. Configure this PS5's PSN device ID "
        "to prevent accidentally launching a game on another console."
    )




def parse_installed_titles(client: dict) -> list[dict[str, str]]:
    """Read the exact installed-title collection exposed by PXPlay PC 3.1.1.

    Console inventory can contain only npTitleId; optional display fields are
    not assumed to exist. Preserve identifiers even without PSN history art.
    """
    system = client.get("systemData")
    if not isinstance(system, dict):
        return []
    installed = system.get("installedTitles")
    if not isinstance(installed, dict) or not isinstance(installed.get("titles"), list):
        return []
    found: dict[str, dict[str, str]] = {}
    for row in installed["titles"]:
        if not isinstance(row, dict):
            continue
        raw_id = row.get("npTitleId")
        try:
            title = normalize_title_id(raw_id)
        except ValueError:
            continue
        if title not in found:
            found[title] = {
                "title_id": title,
                "name": str(row.get("titleName") or row.get("name") or "").strip()[:160],
                "category": str(row.get("category") or "").strip()[:40],
            }
    return list(found.values())


def select_installed_inventory(
    clients: list[dict], *, requested_duid: str = "", local_name: str = ""
) -> tuple[str, str, list[dict[str, str]], bool]:
    """Select a unique PS5, retaining 'unknown inventory' vs empty inventory."""
    duid = choose_console(clients, requested_duid=requested_duid, local_name=local_name)
    console = next(c for c in clients if c["duid"] == duid)
    system = console.get("systemData")
    installed = system.get("installedTitles") if isinstance(system, dict) else None
    available = isinstance(installed, dict) and isinstance(installed.get("titles"), list)
    return duid, _console_name(console), parse_installed_titles(console), available

def _command_status(data: dict, command_id: str) -> str:
    commands = data.get("commands")
    cmd = commands[0] if isinstance(commands, list) and commands else None
    if not isinstance(cmd, dict):
        cmd = data.get("command")
    if not isinstance(cmd, dict) and "commandStatus" in data:
        cmd = data
    if not isinstance(cmd, dict):
        return "pending"
    returned_id = cmd.get("commandId")
    if returned_id and str(returned_id) != command_id:
        raise GameLaunchError("Sony returned a status for a different command")
    status = str(cmd.get("commandStatus") or "").lower()
    if status == "completed":
        result = str(cmd.get("commandResult") or "").lower()
        if result in ("failed", "failure", "error", "rejected"):
            raise GameLaunchError(f"PS5 rejected the launch command ({result})")
        return "completed"
    if status in ("failed", "rejected", "cancelled", "canceled"):
        raise GameLaunchError(f"PS5 launch command {status}")
    return "pending"


def _launch_sync(authenticator, title_id: str, preferred_duid: str, local_name: str) -> bool:
    clients = _read_clients(authenticator)
    duid = choose_console(clients, requested_duid=preferred_duid, local_name=local_name)
    payload = build_launch_payload(duid, title_id)
    response = authenticator.post(
        url=urljoin(_BASE, "commands"),
        json=payload,
        headers={**_headers(), "Content-Type": "application/json; charset=utf-8"},
        timeout=_REQUEST_TIMEOUT,
        allow_redirects=False,
    )
    data = _response_json(response, "game-launch command")
    command_id = data.get("commandId")
    if not isinstance(command_id, str) or not command_id.strip():
        raise GameLaunchError("Sony accepted the request without a command ID")
    command_id = command_id.strip()
    if len(command_id) > 256:
        raise GameLaunchError("Invalid PSN command identifier")
    deadline = time.monotonic() + _STATUS_TIMEOUT
    while time.monotonic() < deadline:
        status_response = authenticator.get(
            url=urljoin(_BASE, "commands"),
            params={
                "platform": "PS5",
                "commandIds": command_id,
                "includeFields": "actions,commandDetail",
                "commandStatus": "executing,completed",
            },
            headers=_headers(),
            timeout=_REQUEST_TIMEOUT,
            allow_redirects=False,
        )
        if _command_status(_response_json(status_response, "game-launch status"), command_id) == "completed":
            _LOG.info("Sony confirmed completion of game launch request for %s", title_id)
            return True
        time.sleep(_POLL_INTERVAL)
    raise GameLaunchTimeout("Sony did not confirm PS5 game launch within 12 seconds")


class SonyGameLauncher:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def fetch_console_inventory(
        self, authenticator, *, duid: str = "", local_name: str = "",
    ) -> tuple[str, str, list[dict[str, str]], bool]:
        """Return selected console identity and installed titles without launching."""
        if authenticator is None:
            raise GameLaunchError("PSN authentication is unavailable")
        async with self._lock:
            try:
                clients = await asyncio.to_thread(
                    _read_clients, authenticator, include_system_data=True,
                )
                return select_installed_inventory(
                    clients, requested_duid=duid, local_name=local_name,
                )
            except GameLaunchError:
                raise
            except Exception as error:
                _LOG.warning("Console inventory fetch failed: %s", type(error).__name__)
                raise GameLaunchError("PSN console inventory could not be fetched") from error

    async def launch(
        self, *,
        authenticator,
        title_id: str,
        duid: str = "",
        local_name: str = "",
    ) -> bool:
        if authenticator is None:
            raise GameLaunchError("PSN authentication is not configured")
        title = normalize_title_id(title_id)
        async with self._lock:
            try:
                return await asyncio.to_thread(
                    _launch_sync, authenticator, title, duid, local_name,
                )
            except GameLaunchError:
                raise
            except Exception as error:
                _LOG.warning("PSN title launch failed: %s", type(error).__name__)
                raise GameLaunchError("PSN request failed; check authentication and connectivity") from error
