"""Optional unfurled-based Remote Two/3 diagnostic (not a daemon).

Example:
  python tools/diagnose_remote.py http://192.168.1.100:80 MY_API_KEY
"""
import argparse
import asyncio
from unfurled.remote import Remote


async def inspect(url: str, api_key: str) -> None:
    remote = Remote(url, api_key=api_key)
    await remote.init()
    print(f"Remote: {remote.name} ({remote.info.model_name})")
    print(f"Firmware: {remote.sw_version}")
    print("Activities:")
    for activity in remote.activities:
        print(f"  {activity.name}: {activity.state}")
    try:
        matches = await remote.integrations.get_by_driver("ps5_control_driver")
        print(f"PS5 Control integration: {matches}")
    except Exception as exc:
        print(f"Integration lookup failed: {exc}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Unfurled-based UC Remote diagnostics")
    parser.add_argument("url", help="Remote base URL including port")
    parser.add_argument("api_key", help="Remote Core API key")
    args = parser.parse_args()
    asyncio.run(inspect(args.url, args.api_key))
