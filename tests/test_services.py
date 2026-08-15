from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.eirc_spb import (
    EircSpbRuntime,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.eirc_spb.const import (
    ATTR_METER_ID,
    ATTR_SCALE_ID,
    CONF_ACCOUNTS,
    CONF_LOGIN,
    CONF_PASSWORD,
    DOMAIN,
)
from custom_components.eirc_spb.coordinator import EircSpbData
from custom_components.eirc_spb.exceptions import EircSpbApiError
from custom_components.eirc_spb.models import Account, Meter, Scale
from custom_components.eirc_spb.services import SERVICE_SEND_METER_READING

SUBMIT_RESULT = {"code": "0", "message": "Показания приняты"}


def make_data() -> EircSpbData:
    return EircSpbData(
        accounts={"a1": Account("a1", "1000000001", "addr")},
        meters={
            "m1": Meter(
                meter_id="m1",
                account_id="a1",
                name="X",
                device_class="water",
                unit="м³",
                scales=[Scale("0", None, 1.0, None)],
            )
        },
    )


def install_runtime(hass: HomeAssistant, data: EircSpbData | None = None):
    coordinator = MagicMock()
    coordinator.data = data if data is not None else make_data()
    coordinator.async_request_refresh = AsyncMock()
    client = MagicMock()
    client.submit_reading = AsyncMock(return_value=SUBMIT_RESULT)
    runtime = EircSpbRuntime(client=client, coordinator=coordinator)
    hass.data.setdefault(DOMAIN, {})["test_entry"] = runtime
    return runtime


async def call_service(hass: HomeAssistant, data: dict, **kwargs):
    return await hass.services.async_call(
        DOMAIN,
        SERVICE_SEND_METER_READING,
        data,
        blocking=True,
        **kwargs,
    )


async def test_send_reading_success(hass: HomeAssistant):
    from custom_components.eirc_spb.services import async_setup_services

    runtime = install_runtime(hass)
    await async_setup_services(hass)
    hass.states.async_set(
        "sensor.m", "1", {ATTR_METER_ID: "m1", ATTR_SCALE_ID: "0"}
    )
    response = await call_service(
        hass,
        {
            "entity_id": "sensor.m",
            "readings": [{"scale_id": 0, "value": 123}],
        },
        return_response=True,
    )
    runtime.client.submit_reading.assert_awaited_once_with(
        "a1", "m1", [{"scale_id": "0", "value": 123.0}]
    )
    runtime.coordinator.async_request_refresh.assert_awaited_once()
    assert response == SUBMIT_RESULT


async def test_send_reading_rejection_raises(hass: HomeAssistant):
    from custom_components.eirc_spb.services import async_setup_services

    runtime = install_runtime(hass)
    runtime.client.submit_reading = AsyncMock(
        side_effect=EircSpbApiError("Показания меньше предыдущих")
    )
    await async_setup_services(hass)
    hass.states.async_set(
        "sensor.m", "1", {ATTR_METER_ID: "m1", ATTR_SCALE_ID: "0"}
    )
    with pytest.raises(HomeAssistantError, match="меньше предыдущих"):
        await call_service(
            hass,
            {
                "entity_id": "sensor.m",
                "readings": [{"scale_id": "0", "value": 1}],
            },
        )
    runtime.coordinator.async_request_refresh.assert_not_awaited()


async def test_send_reading_unknown_entity_raises(hass: HomeAssistant):
    from custom_components.eirc_spb.services import async_setup_services

    runtime = install_runtime(hass)
    await async_setup_services(hass)
    with pytest.raises(HomeAssistantError, match="sensor.unknown"):
        await call_service(
            hass,
            {
                "entity_id": "sensor.unknown",
                "readings": [{"scale_id": "0", "value": 1}],
            },
        )
    runtime.client.submit_reading.assert_not_awaited()


async def test_send_reading_entity_without_meter_id_raises(hass: HomeAssistant):
    from custom_components.eirc_spb.services import async_setup_services

    runtime = install_runtime(hass)
    await async_setup_services(hass)
    hass.states.async_set("sensor.balance", "100", {"account_id": "a1"})
    with pytest.raises(HomeAssistantError, match="sensor.balance"):
        await call_service(
            hass,
            {
                "entity_id": "sensor.balance",
                "readings": [{"scale_id": "0", "value": 1}],
            },
        )
    runtime.client.submit_reading.assert_not_awaited()


async def test_send_reading_removed_meter_raises(hass: HomeAssistant):
    from custom_components.eirc_spb.services import async_setup_services

    data = make_data()
    data.meters = {}
    runtime = install_runtime(hass, data)
    await async_setup_services(hass)
    hass.states.async_set(
        "sensor.m", "unknown", {ATTR_METER_ID: "m1", ATTR_SCALE_ID: "0"}
    )
    with pytest.raises(HomeAssistantError, match="Счётчик недоступен"):
        await call_service(
            hass,
            {
                "entity_id": "sensor.m",
                "readings": [{"scale_id": "0", "value": 1}],
            },
        )
    runtime.client.submit_reading.assert_not_awaited()


async def test_send_reading_multi_entity_uses_first_resolvable(hass: HomeAssistant):
    from custom_components.eirc_spb.services import async_setup_services

    runtime = install_runtime(hass)
    await async_setup_services(hass)
    hass.states.async_set("sensor.balance", "100", {"account_id": "a1"})
    hass.states.async_set(
        "sensor.m", "1", {ATTR_METER_ID: "m1", ATTR_SCALE_ID: "0"}
    )
    await call_service(
        hass,
        {
            "entity_id": ["sensor.missing", "sensor.balance", "sensor.m"],
            "readings": [{"scale_id": "0", "value": 5}],
        },
    )
    runtime.client.submit_reading.assert_awaited_once_with(
        "a1", "m1", [{"scale_id": "0", "value": 5.0}]
    )


async def test_service_registered_and_removed_with_entry(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_LOGIN: "u", CONF_PASSWORD: "p", CONF_ACCOUNTS: ["a1"]},
    )
    entry.add_to_hass(hass)
    client = AsyncMock()
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    with (
        patch("custom_components.eirc_spb.EircSpbApiClient", return_value=client),
        patch(
            "custom_components.eirc_spb.EircSpbCoordinator",
            return_value=coordinator,
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new_callable=AsyncMock,
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        assert await async_setup_entry(hass, entry) is True
        assert hass.services.has_service(DOMAIN, SERVICE_SEND_METER_READING)
        assert await async_unload_entry(hass, entry) is True
        assert not hass.services.has_service(DOMAIN, SERVICE_SEND_METER_READING)
