# Mitsubishi AE-200 add-on

Control Mitsubishi Electric AE-200 / AE-50 / EW-50 (and G-50/GB-50) centralised
HVAC controllers from Home Assistant via MQTT Discovery.

## Configuration

| Option | Required | Default | Description |
|---|---|---|---|
| `ae200_host` | yes | – | Controller IP address or hostname. |
| `ae200_port` | no | `80` | Controller HTTP port. |
| `prefer_websocket` | no | `true` | Use the websocket API (gives zone names). Disable only for very old firmware. |
| `scan_interval` | no | `30` | Polling interval in seconds (minimum 15). |
| `log_level` | no | `info` | `trace`, `debug`, `info`, `warning`, `error`, `fatal`. |
| `mqtt_host` | no | auto | Override the auto-detected MQTT broker host. |
| `mqtt_port` | no | auto | Override the MQTT broker port. |
| `mqtt_username` | no | auto | Override the MQTT username. |
| `mqtt_password` | no | auto | Override the MQTT password. |

Leave the `mqtt_*` options blank to use the broker provided by the Mosquitto
add-on automatically.

## First run

1. Set `ae200_host` to your controller's IP.
2. Enable **Start on boot** and **Watchdog**.
3. **Start** the add-on and watch the log — you should see it discover your
   zones and publish MQTT discovery for each.
4. Your units appear under **Settings → Devices & Services → MQTT** as a
   *Mitsubishi Electric* device per zone.

## Safety

The controller's control API is unauthenticated and historically had a
denial-of-service CVE on the same endpoint. Keep the controller on a trusted
VLAN, never expose it to the internet, and leave `scan_interval` at a sane
value. This add-on deliberately rate-limits itself.

## Support

Issues: https://github.com/InspireELJ/mitsubishi-ae200-hass/issues
