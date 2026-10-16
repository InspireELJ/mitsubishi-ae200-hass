"""Passive cached observers never create or retain coordinator polling demand."""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def coordinator(monkeypatch):
    def module(name, **members):
        result = types.ModuleType(name)
        result.__dict__.update(members)
        monkeypatch.setitem(sys.modules, name, result)
        return result

    class BaseCoordinator:
        @classmethod
        def __class_getitem__(cls, _):
            return cls

        def __init__(self, *args, **kwargs):
            self._listeners = {}
            self.schedule_count = self.unschedule_count = self.notifications = 0
            self.last_update_success = True
            self.data = {}

        def async_add_listener(self, update_callback, context=None):
            if not self._listeners:
                self.schedule_count += 1
            key = object()
            self._listeners[key] = (update_callback, context)

            def remove():
                self._listeners.pop(key)
                if not self._listeners:
                    self.unschedule_count += 1

            return remove

        def async_update_listeners(self):
            self.notifications += 1
            for callback, _ in list(self._listeners.values()):
                callback()

        async def async_shutdown(self):
            pass

    class Client:
        def __init__(self, *args, **kwargs):
            self._request = AsyncMock(side_effect=AssertionError("Passive observer must not request data"))
            self.async_update = AsyncMock(side_effect=AssertionError("Passive observer must not poll"))
            self.close = AsyncMock()

    for name in ("homeassistant", "homeassistant.helpers"):
        module(name).__path__ = []
    module("homeassistant.core", HomeAssistant=object, callback=lambda func: func)
    module("homeassistant.config_entries", ConfigEntry=object)
    module("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda _: None)
    module("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=BaseCoordinator,
           UpdateFailed=type("UpdateFailed", (Exception,), {}))
    package = "ae200_passive_coordinator_test"
    module(package).__path__ = [str(Path(__file__).resolve().parents[1] / "custom_components/ae200")]
    module(package + "._pymelans", MelansClient=Client, MelansDevice=object,
           MelansError=type("MelansError", (Exception,), {}))
    loaded = importlib.import_module(package + ".coordinator")
    result = loaded.AE200Coordinator(object(), object(), host="fixture.invalid", port=80,
                                    prefer_websocket=True, scan_interval=30)
    yield result
    result.client._request.assert_not_awaited()
    result.client.async_update.assert_not_awaited()
    for name in list(sys.modules):
        if name.startswith(package + "."):
            sys.modules.pop(name, None)


def test_passive_subscription_with_no_entities_never_arms_polling_and_can_be_removed(coordinator):
    c = coordinator
    observed = []
    remove = c.async_add_passive_listener(lambda: observed.append(c.data))
    assert c._listeners == {} and c.schedule_count == c.unschedule_count == 0
    assert observed == []
    c.data = {"cached": "value"}
    c.async_update_listeners()
    assert observed == [{"cached": "value"}] and c.notifications == 1
    assert c._listeners == {} and c.schedule_count == c.unschedule_count == 0
    remove()
    remove()
    c.async_update_listeners()
    assert len(observed) == 1 and c.notifications == 2
    assert c.schedule_count == c.unschedule_count == 0


def test_entities_are_notified_first_and_last_entity_removal_still_stops_polling(coordinator):
    c = coordinator
    order = []
    remove_entity = c.async_add_listener(lambda: order.append("entity"))
    remove_passive = c.async_add_passive_listener(lambda: order.append("passive"))
    assert len(c._listeners) == 1 and c.schedule_count == 1
    c.async_update_listeners()
    assert order == ["entity", "passive"] and c.notifications == 1
    remove_entity()
    assert c._listeners == {} and c.unschedule_count == 1
    c.async_update_listeners()
    assert order == ["entity", "passive", "passive"]
    assert c.schedule_count == c.unschedule_count == 1
    remove_passive()
    assert c.schedule_count == c.unschedule_count == 1


def test_callback_can_unsubscribe_during_notification_without_mutating_demand(coordinator):
    c = coordinator
    seen = []

    def once():
        seen.append("once")
        remove()

    remove = c.async_add_passive_listener(once)
    c.async_update_listeners()
    c.async_update_listeners()
    assert seen == ["once"]
    assert c._listeners == {} and c.schedule_count == c.unschedule_count == 0


def test_one_observer_failure_does_not_block_other_observers_or_normal_notifications(coordinator, caplog):
    c = coordinator
    seen = []

    def broken():
        raise ValueError("synthetic observer failure")

    c.async_add_passive_listener(broken)
    c.async_add_passive_listener(lambda: seen.append("healthy"))
    c.async_update_listeners()
    assert seen == ["healthy"] and c.notifications == 1
    assert "Unexpected error updating passive AE-200 listener" in caplog.text
    assert c._listeners == {} and c.schedule_count == c.unschedule_count == 0


@pytest.mark.asyncio
async def test_shutdown_clears_passive_callbacks_and_late_unload_cleanup_is_idempotent(coordinator):
    c = coordinator
    observed = []
    remove = c.async_add_passive_listener(lambda: observed.append("called"))
    await c.async_shutdown()
    remove()
    c.async_update_listeners()
    assert observed == [] and c._passive_listeners == set()
    assert c._listeners == {} and c.schedule_count == c.unschedule_count == 0
    c.client.close.assert_awaited_once()
