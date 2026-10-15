"""AE-200 → MQTT Discovery bridge.

Runs inside the Home Assistant add-on container. Polls the Mitsubishi
controller via :mod:`pymelans` and publishes Home Assistant MQTT Discovery
config plus state for each zone, so the units appear as native ``climate``
entities (plus diagnostic sensors) without any YAML.

Configuration comes from the add-on options, surfaced as environment variables
by ``run.sh``:

* ``AE200_HOST``            controller IP/host (required)
* ``AE200_PORT``           controller port (default 80)
* ``AE200_PREFER_WS``      "true"/"false" prefer websocket transport
* ``AE200_SCAN_INTERVAL``  poll seconds (floored to 15)
* ``MQTT_HOST/PORT/USER/PASSWORD``  broker connection
* ``MQTT_DISCOVERY_PREFIX`` default "homeassistant"
* ``LOG_LEVEL``            INFO/DEBUG/...

The bridge is its own watchdog: any unhandled error in the poll loop is logged
and retried with backoff rather than crashing the process, and a liveness file
is touched each successful cycle for the add-on health check.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from pathlib import Path

import aiomqtt

from pymelans import MelansClient, MelansDevice, MelansError
from pymelans import const as mc

_LOGGER = logging.getLogger("ae200bridge")

LIVENESS_FILE = Path("/tmp/ae200bridge.alive")  # noqa: S108 - container tmp

MODE_TO_HA = {
    mc.MODE_COOL: "cool",
    mc.MODE_HEAT: "heat",
    mc.MODE_FAN: "fan_only",
    mc.MODE_DRY: "dry",
    mc.MODE_AUTO: "heat_cool",
}
HA_TO_MODE = {v: k for k, v in MODE_TO_HA.items()}
FAN_TO_HA = {mc.FAN_LOW: "low", mc.FAN_MID: "medium", mc.FAN_HIGH: "high", mc.FAN_AUTO: "auto"}
HA_TO_FAN = {v: k for k, v in FAN_TO_HA.items()}


def _env(name: str, default: str = "") -> str:
    val = os.environ.get(name)
    return val if val not in (None, "") else default


def _env_opt(name: str) -> str | None:
    """Like :func:`_env` but returns ``None`` when unset (for optional creds)."""
    val = os.environ.get(name)
    return val if val not in (None, "") else None


class Config:
    def __init__(self) -> None:
        self.host = _env("AE200_HOST")
        self.port = int(_env("AE200_PORT", "80"))
        self.prefer_ws = _env("AE200_PREFER_WS", "true") == "true"
        self.scan_interval = max(int(_env("AE200_SCAN_INTERVAL", "30")), mc.MIN_POLL_INTERVAL)
        self.mqtt_host = _env("MQTT_HOST", "core-mosquitto")
        self.mqtt_port = int(_env("MQTT_PORT", "1883"))
        self.mqtt_user = _env_opt("MQTT_USER")
        self.mqtt_password = _env_opt("MQTT_PASSWORD")
        self.discovery_prefix = _env("MQTT_DISCOVERY_PREFIX", "homeassistant")
        self.log_level = _env("LOG_LEVEL", "INFO")
        if not self.host:
            raise SystemExit("AE200_HOST is required")


class Bridge:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.client = MelansClient(cfg.host, port=cfg.port, prefer_websocket=cfg.prefer_ws)
        self._stop = asyncio.Event()
        self._discovery_sent: set[str] = set()

    # -- topic helpers ----------------------------------------------------
    def _node_id(self) -> str:
        return f"ae200_{self.cfg.host.replace('.', '_')}"

    def _base_topic(self, gid: str) -> str:
        return f"{self._node_id()}/{gid}"

    def _device_block(self, dev: MelansDevice) -> dict:
        return {
            "identifiers": [f"{self._node_id()}_{dev.group_id}"],
            "name": dev.name,
            "manufacturer": "Mitsubishi Electric",
            "model": f"AE-200 {dev.kind} (group {dev.group_id})",
            "via_device": self._node_id(),
        }

    # -- discovery --------------------------------------------------------
    async def _publish_discovery(self, mqtt: aiomqtt.Client, dev: MelansDevice) -> None:
        gid = dev.group_id
        base = self._base_topic(gid)
        uid = f"{self._node_id()}_{gid}"
        dprefix = self.cfg.discovery_prefix

        if dev.kind in (mc.KIND_AC, mc.KIND_ATW):
            climate_cfg = {
                "name": None,
                "unique_id": f"{uid}_climate",
                "device": self._device_block(dev),
                "modes": ["off", "cool", "heat", "fan_only", "dry", "heat_cool"],
                "fan_modes": ["auto", "low", "medium", "high"],
                "mode_state_topic": f"{base}/mode",
                "mode_command_topic": f"{base}/mode/set",
                "temperature_state_topic": f"{base}/target_temp",
                "temperature_command_topic": f"{base}/target_temp/set",
                "current_temperature_topic": f"{base}/current_temp",
                "fan_mode_state_topic": f"{base}/fan",
                "fan_mode_command_topic": f"{base}/fan/set",
                "min_temp": 16,
                "max_temp": 30,
                "temp_step": 0.5,
                "availability": [
                    {"topic": f"{self._node_id()}/availability"},
                    {"topic": f"{base}/availability"},
                    {"topic": f"{base}/climate/availability"},
                ],
                "availability_mode": "all",
            }
            await mqtt.publish(
                f"{dprefix}/climate/{uid}/config",
                json.dumps(climate_cfg),
                retain=True,
            )

        # Diagnostic binary sensors: filter + fault
        for key, name, dc in (
            ("filter", "Filter cleaning required", "problem"),
            ("error", "Fault", "problem"),
        ):
            cfg = {
                "name": name,
                "unique_id": f"{uid}_{key}",
                "device": self._device_block(dev),
                "state_topic": f"{base}/{key}",
                "device_class": dc,
                "payload_on": "ON",
                "payload_off": "OFF",
                "entity_category": "diagnostic",
                "availability": [
                    {"topic": f"{self._node_id()}/availability"},
                    {"topic": f"{base}/availability"},
                    {"topic": f"{base}/{key}/availability"},
                ],
                "availability_mode": "all",
            }
            await mqtt.publish(
                f"{dprefix}/binary_sensor/{uid}_{key}/config",
                json.dumps(cfg),
                retain=True,
            )
        self._discovery_sent.add(gid)

    # -- state ------------------------------------------------------------
    async def _publish_state(self, mqtt: aiomqtt.Client, dev: MelansDevice) -> None:
        base = self._base_topic(dev.group_id)
        mode = (
            "off" if dev.is_on is False else MODE_TO_HA.get(dev.mode or "") if dev.is_on else None
        )
        await mqtt.publish(
            f"{base}/climate/availability", "online" if mode is not None else "offline", retain=True
        )
        if mode is not None:
            await mqtt.publish(f"{base}/mode", mode)
        if dev.target_temperature is not None:
            await mqtt.publish(f"{base}/target_temp", f"{dev.target_temperature:g}")
        if dev.current_temperature is not None:
            await mqtt.publish(f"{base}/current_temp", f"{dev.current_temperature:g}")
        if dev.fan_speed:
            fan = FAN_TO_HA.get(dev.fan_speed)
            if fan is not None:
                await mqtt.publish(f"{base}/fan", fan)
        for field, value in (("filter", dev.filter_sign), ("error", dev.error_sign)):
            await mqtt.publish(
                f"{base}/{field}/availability",
                "online" if value is not None else "offline",
                retain=True,
            )
            if value is not None:
                await mqtt.publish(f"{base}/{field}", "ON" if value else "OFF")

    # -- command handling -------------------------------------------------
    async def _handle_command(self, topic: str, payload: str) -> None:
        # topic = <node>/<gid>/<field>/set
        parts = topic.split("/")
        try:
            gid = parts[1]
            field = parts[2]
        except IndexError:
            return
        _LOGGER.info("Command: group=%s field=%s payload=%s", gid, field, payload)
        if field == "mode":
            if payload == "off":
                await self.client.set_power(gid, False)
            else:
                mode = HA_TO_MODE.get(payload)
                if mode:
                    dev = self.client.devices.get(gid)
                    if dev and not dev.is_on:
                        await self.client.set_power(gid, True)
                    await self.client.set_mode(gid, mode)
        elif field == "target_temp":
            await self.client.set_temperature(gid, float(payload))
        elif field == "fan":
            speed = HA_TO_FAN.get(payload)
            if speed:
                await self.client.set_fan_speed(gid, speed)

    # -- main loops -------------------------------------------------------
    async def run(self) -> None:
        will = aiomqtt.Will(f"{self._node_id()}/availability", "offline", retain=True)
        async with aiomqtt.Client(
            hostname=self.cfg.mqtt_host,
            port=self.cfg.mqtt_port,
            username=self.cfg.mqtt_user,
            password=self.cfg.mqtt_password,
            will=will,
        ) as mqtt:
            await mqtt.publish(f"{self._node_id()}/availability", "online", retain=True)
            await mqtt.subscribe(f"{self._node_id()}/+/+/set")
            poller = asyncio.create_task(self._poll_loop(mqtt))
            try:
                async for message in mqtt.messages:
                    try:
                        await self._handle_command(
                            str(message.topic),
                            message.payload.decode() if message.payload else "",
                        )
                    except (MelansError, ValueError) as err:
                        _LOGGER.error("Command failed: %s", err)
            finally:
                poller.cancel()

    async def _poll_loop(self, mqtt: aiomqtt.Client) -> None:
        backoff = 5
        while not self._stop.is_set():
            try:
                await self.client.async_update()
                await mqtt.publish(f"{self._node_id()}/availability", "online", retain=True)
                for dev in self.client.devices.values():
                    if dev.group_id not in self._discovery_sent:
                        await self._publish_discovery(mqtt, dev)
                    fresh = self.client.is_group_available(dev.group_id)
                    if fresh:
                        await self._publish_state(mqtt, dev)
                    await mqtt.publish(
                        f"{self._base_topic(dev.group_id)}/availability",
                        "online" if fresh else "offline",
                        retain=True,
                    )
                LIVENESS_FILE.write_text(str(int(time.time())))
                backoff = 5
                await asyncio.sleep(self.cfg.scan_interval)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - watchdog: never die on a poll
                _LOGGER.error("Poll cycle failed (%s); retrying in %ss", err, backoff)
                await mqtt.publish(f"{self._node_id()}/availability", "offline", retain=True)
                await asyncio.sleep(max(backoff, mc.MIN_POLL_INTERVAL))
                backoff = min(backoff * 2, 120)

    def request_stop(self) -> None:
        self._stop.set()


async def _amain() -> None:
    cfg = Config()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level or "INFO", logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _LOGGER.info(
        "AE-200 bridge starting: host=%s port=%s ws=%s interval=%ss",
        cfg.host,
        cfg.port,
        cfg.prefer_ws,
        cfg.scan_interval,
    )
    bridge = Bridge(cfg)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, bridge.request_stop)

    while not bridge._stop.is_set():  # noqa: SLF001
        try:
            await bridge.run()
        except Exception as err:  # noqa: BLE001 - reconnect to broker on any failure
            _LOGGER.error("Bridge run failed (%s); reconnecting in 10s", err)
            await asyncio.sleep(10)
    await bridge.client.close()
    _LOGGER.info("AE-200 bridge stopped")


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
