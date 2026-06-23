# AGENTS.md — mitsubishi-ae200-hass

Context for AI agents working on this repository.

## What this is
Home Assistant integration + add-on for Mitsubishi Electric AE-200 / EW-50
(MELANS protocol) centralised HVAC controllers. Three parts share one engine:

- `pymelans/` — standalone async protocol library (the single source of truth
  for the wire format). Used by BOTH the add-on and the custom component.
- `custom_components/ae200/` — HACS custom integration (config flow, coordinator,
  climate/sensor/binary_sensor/switch).
- `addon/` — Supervisor add-on: `ae200_bridge.py` polls via pymelans and
  publishes MQTT Discovery; `run`/`finish` are s6 services; `config.yaml`
  declares options + watchdog.

## Protocol facts (verified against a live AE-200, firmware ~2021)
- Endpoints: `ws://<host>/b_xmlproc/` (subprotocol `b_xmlproc`, permessage-
  deflate) preferred; `POST /servlet/MIMEReceiveServlet` (text/xml) fallback.
- Read: `getRequest` with `<Mnet Group="N" Attr="*" .../>`. Names only come
  from `<ControlGroup><MnetList/></ControlGroup>` over websocket (`GroupNameWeb`).
- Write: `setRequest` with the attributes to change.
- Gotchas: multi-group wildcard reads are rejected on some firmware (query per
  group); unconfigured groups echo `*`; AC units report HWHP/ATW fields as
  `0.0` placeholders (classify on non-zero values); a faulted return-air
  thermistor reads ~ -40 °C.

## Safety (do not regress)
- Unauthenticated control plane; CVE-2008-1546 is a DoS on this servlet. Keep
  the rate limiting in `pymelans` (MIN_POLL_INTERVAL, write spacing). Never
  add an uncapped poll or a write storm.

## Validating without HA installed
- `homeassistant` imports won't resolve locally — that's expected. Validate by:
  - `python -m py_compile` on all modules,
  - `PYTHONPATH=pymelans pytest tests/ -c /dev/null` (offline, real fixtures),
  - the add-on bridge can be exercised by stubbing `aiomqtt` (see session notes)
    against a reachable controller for read-only checks.

## Conventions
- No Docker for local dev; the add-on container is the sanctioned exception
  (that is just how HA add-ons ship).
- British English in docs; no em-dashes.
