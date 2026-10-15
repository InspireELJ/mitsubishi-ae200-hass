# Mitsubishi AE-200 / EW-50 for Home Assistant

## InspireELJ internal pilot

The company `main` branch contains the `0.2.0b3` capability pilot.
Start with [HACS deployment and updates](docs/hacs-deployment-zh.md),
[deployment and rollback](docs/deployment-zh.md), and
[diagnostics without Java](docs/no-java-diagnostics.md).

The pilot keeps the `ae200` domain and existing entity identifiers. It adds
per-group freshness, controller health, checked command acknowledgements,
cached raw-field diagnostics and a self-contained HACS bundle. Zero humidity
is treated as an unverified value rather than a measurement.

Version `0.2.0b3` adds a bounded capability query, per-group mode and fan choices,
vertical vane control, reported temperature precision and limits, atomic
mode/power commands and cached command-readback diagnostics. See the
[capability matrix and development scope](docs/capabilities.md) for verified
behaviour, equipment-family limits and the remaining expansion work.

This branch requires Home Assistant 2025.12 or later. Protocol tests use
synthetic failures and loopback HTTP/WebSocket servers; HA entity contracts
use API boundary stubs. A live ordinary indoor AC group has accepted and
confirmed temperature, fan, vane and compound fan-only/power commands, with
its original settings restored. This does not validate every controller,
equipment family or licensed function.

The underlying protocol source remains in `pymelans/`. Run
`python scripts/sync_ha_library.py` after changing it; never edit the generated
`custom_components/ae200/_pymelans/` copy directly. This fork directs installation
and support to InspireELJ. The original licence and authorship are retained.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Local, cloud-free control of **Mitsubishi Electric centralised air-conditioning
controllers** from Home Assistant. Works with the **AE-200E / AE-50 / EW-50E**
family and the older **G-50 / G-50A / GB-50 / GB-50A** controllers, which all
speak the same "MELANS" XML protocol.

These controllers are the building-management front end for Mitsubishi City
Multi (VRF) systems, Mr. Slim splits, Lossnay energy-recovery ventilation and
air-to-water (ATW) hydro units. They are common in commercial buildings and
increasingly in larger homes. This project brings every zone into Home
Assistant as a first-class device, with **no MELCloud account and no internet
dependency**.

> It auto-discovers your zones and their names directly from the controller.
> Point it at the controller's IP and your rooms appear, correctly named.

---

## Two ways to install

This repository ships **both** so you can pick what suits your setup. They
share one protocol engine (`pymelans`); native HACS capability discovery and
vane controls currently extend beyond the MQTT add-on's feature set.

| | Add-on | Custom integration (HACS) |
|---|---|---|
| Install via | Add-on store | HACS |
| Runs as | Supervised container with **watchdog + autostart** | In-process integration |
| Setup UI | Add-on **settings page** | Config flow (UI) |
| Creates entities via | MQTT Discovery | Native HA entities |
| Needs MQTT broker | Yes (auto-detected Mosquitto) | No |
| Best for | HA OS / Supervised, "set and forget", BMS sites | HA Core/Container, simplest path |

Both give you `climate` entities per zone plus filter, fault and remote-lockout
controls. Most home users want the **custom integration**; site/building
managers who value the watchdog and a dedicated settings page often prefer the
**add-on**.

---

## Features

- **Auto-discovery** of all configured groups, including their human names
  (read straight from the controller over its websocket API).
- Native **`climate`** entity per indoor unit: power, mode
  (cool / heat / fan / dry / auto), target temperature, fan speed. HACS narrows
  these choices using reported capabilities and adds vertical vane control.
- **Diagnostics** per unit: return-air temperature, filter-cleaning alert,
  fault flag, and (where supported) outdoor temperature and humidity.
- **Local remote lockout** switch (`RemoCon`) — lock out the wall/handset
  remote so occupants cannot override a centrally managed setpoint. A genuine
  building-manager feature.
- **Equipment classification**: indoor AC units, Lossnay / air handlers and
  air-to-water units are inferred from reported attributes. Classification
  alone does not establish verified control support for every family; consult
  the [capability matrix](docs/capabilities.md).
- **Resilient & safe**: websocket transport with automatic HTTP fallback,
  a self-healing poll loop with backoff, and deliberate rate-limiting (see
  [Safety](#safety--security)).
- **100% local**. No cloud, no account, no telemetry.

---

## Requirements

- A Mitsubishi AE-200, AE-50, EW-50, G-50(A) or GB-50(A) controller reachable
  on your LAN, with its web interface enabled.
- Home Assistant 2025.12 or newer.
- For the **add-on**: an MQTT broker (the official *Mosquitto broker* add-on is
  auto-detected).

Find the controller's IP from your router/DHCP leases or the controller's own
network settings. Browsing to `http://<controller-ip>/` should show a page
titled *"MITSUBISHI Air Conditioning Control System"*.

---

## Install via HACS

For an existing installation, follow the [company HACS migration guide](docs/hacs-deployment-zh.md).
After the company release is published, new installations can use these steps:

1. HACS → **Integrations** → ⋮ → **Custom repositories**.
2. Add `https://github.com/InspireELJ/mitsubishi-ae200-hass`, category
   **Integration**.
3. Install **Mitsubishi AE-200 / EW-50** and restart Home Assistant.
4. **Settings → Devices & Services → Add Integration → Mitsubishi AE-200**.
5. Enter the controller IP. Your zones are discovered automatically.

The polling interval is adjustable later under the integration's **Configure**
options (minimum 15 s — see Safety).

### Manual install

Copy `custom_components/ae200/` into your HA `config/custom_components/`
directory and restart.

---

## Install — Add-on

1. **Settings → Add-ons → Add-on Store** → ⋮ → **Repositories**.
2. Add `https://github.com/InspireELJ/mitsubishi-ae200-hass`.
3. Install **Mitsubishi AE-200**.
4. Open the add-on's **Configuration** tab and set:

   | Option | Meaning | Default |
   |---|---|---|
   | `ae200_host` | Controller IP/host (**required**) | – |
   | `ae200_port` | Controller HTTP port | `80` |
   | `prefer_websocket` | Use websocket transport (gives zone names) | `true` |
   | `scan_interval` | Poll seconds (min 15) | `30` |
   | `log_level` | Logging verbosity | `info` |
   | `mqtt_*` | Optional manual MQTT override | auto-detected |

5. Enable **Start on boot** and **Watchdog** (recommended), then **Start**.

Zones appear automatically via MQTT Discovery as a *Mitsubishi Electric* device
per group.

---

## Settings & operation (building-manager notes)

- **Watchdog / autostart**: the add-on declares a watchdog and `boot: auto`, and
  its internal poll loop never dies on a transient controller/network blip — it
  backs off and retries, and on a hard failure the Supervisor restarts the
  container.
- **Polling interval**: keep this sensible. 30 s is a good default; 15 s is the
  enforced floor. There is rarely value in polling a building controller faster.
- **Remote lockout**: use the per-zone *Local remote lockout* switch to enforce
  central control. Pair with HA automations for scheduled setback while keeping
  occupants from overriding.
- **Multiple controllers / AE-50 expansion**: add one integration entry (or one
  add-on instance) per AE-200/EW-50 controller. Each exposes its own groups.

---

## Safety & security

**This control plane is unauthenticated.** The AE-200 family serves its XML
control API with no login on most deployments, and historically
[CVE-2008-1546](https://nvd.nist.gov/vuln/detail/CVE-2008-1546) documented a
denial-of-service on the very same `MIMEReceiveServlet` endpoint. Treat the
controller as a sensitive piece of building infrastructure:

- **Put it on a trusted management VLAN.** Do not expose the controller to the
  internet or untrusted networks.
- This integration is a deliberately **well-behaved client**: it floors the
  poll interval and spaces out write commands so it can never hammer the
  controller.
- Writing to HVAC affects real comfort and, in commercial settings, can affect
  occupants and equipment. Test automations carefully.

---

## Supported attributes

The protocol exposes far more than basic climate. Currently surfaced:

| Capability | Entity |
|---|---|
| Power / mode / setpoint / fan | `climate` |
| Return-air temperature | `sensor` |
| Outdoor temperature (if reported) | `sensor` (diagnostic) |
| Humidity (if reported) | `sensor` |
| Water outlet temp (ATW units) | `sensor` (diagnostic) |
| Filter cleaning required | `binary_sensor` (problem) |
| Fault | `binary_sensor` (problem) |
| Local remote lockout | `switch` |

HACS exposes capability-based vertical vane/air-direction choices through the
climate swing selector. Complete Lossnay ventilation and ATW controls remain
on the roadmap; library parsing alone does not establish live control support.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "Cannot connect" on setup | Wrong IP, or the controller web UI is disabled. Browse to `http://<ip>/` to confirm. |
| Zones found but **no names** | Names need the websocket API. Older firmware falls back to HTTP and shows `Group N`; rename in HA. |
| A zone shows no current temperature | Its return-air thermistor is faulted/disconnected (the controller reports ~ -40 °C); the integration reports *unknown* rather than a bogus value. |
| Add-on won't start | Check `ae200_host` is set and an MQTT broker is available. |
| Changes revert after a moment | Something else (a schedule, the wall remote, or a BMS) is also commanding the unit. Consider the remote-lockout switch. |

Enable `debug` logging (add-on option, or integration logger) to see the raw
protocol exchange.

---

## How it works

```
Home Assistant
   │
   ├── custom_components/ae200   (config flow → coordinator → climate/sensor/switch)
   │        └── pymelans ─────────────┐
   │                                  │  MELANS XML
   └── add-on  ae200_bridge.py        ├───────────────►  AE-200 / EW-50
            (MQTT Discovery)          │   ws://host/b_xmlproc/  (preferred)
            └── pymelans ─────────────┘   POST /servlet/MIMEReceiveServlet (fallback)
```

`pymelans` is a small, dependency-light async client for the MELANS protocol
and can be used standalone in your own scripts.

---

## Credits & prior art

- [`natevoci/ae200`](https://github.com/natevoci/ae200) — first surfaced the
  AE-200 websocket (`b_xmlproc`) approach.
- [`adgelbfish/mitsPy`](https://github.com/adgelbfish/mitsPy) and the
  `g50a-client` from `jozemlakar/homebridge-mnet` — useful MELANS references.

This project goes further: a shared typed client, generic device-family
support, native config flow **and** a watchdog'd add-on, diagnostics, and
safety-first rate limiting.

## License

MIT — see [LICENSE](LICENSE).

*Not affiliated with or endorsed by Mitsubishi Electric. "AE-200", "EW-50",
"City Multi" and "Lossnay" are trademarks of their respective owners.*
