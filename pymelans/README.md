# pymelans

Async Python client for Mitsubishi Electric **MELANS** centralised HVAC
controllers: G-50 / G-50A / GB-50 / GB-50A / AE-50 / AE-200 / AE-200E /
EW-50 / EW-50E.

It speaks the controller's native XML protocol over the websocket endpoint
(`ws://<host>/b_xmlproc/`, preferred — exposes zone names) with automatic
fallback to the HTTP servlet (`POST /servlet/MIMEReceiveServlet`).

```python
import asyncio
from pymelans import MelansClient

async def main():
    async with MelansClient("192.168.1.100") as client:
        await client.discover()
        await client.async_update()
        for dev in client.devices.values():
            print(dev.name, dev.kind, dev.is_on, dev.current_temperature)
        await client.set_power("2", True)
        await client.set_temperature("2", 21)
        await client.set_mode("2", "COOL")

asyncio.run(main())
```

## Design notes

- **Generic device model.** Each group is classified (AC / ventilation / ATW)
  from the *values* it reports, not mere attribute presence — controllers echo
  unsupported fields back as empty or `0.0` placeholders.
- **Safe by default.** Enforces a minimum poll interval and spacing between
  writes. The same servlet had a documented DoS (CVE-2008-1546); being a
  rate-limited, well-behaved client is intentional.
- **Resilient parsing.** A single malformed/faulted group never blinds the
  whole system.

## Install

```bash
pip install pymelans
```

MIT licensed. Part of
[mitsubishi-ae200-hass](https://github.com/GitDakky/mitsubishi-ae200-hass).
