from typing import TYPE_CHECKING

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError

from .const import ATTR_METER_ID, DOMAIN
from .exceptions import EircSpbApiError
from .models import Meter

if TYPE_CHECKING:
    from . import EircSpbRuntime

SERVICE_SEND_METER_READING = "send_meter_reading"

READING_SCHEMA = vol.Schema(
    {
        vol.Required("scale_id"): vol.Any(int, str),
        vol.Required("value"): vol.Coerce(float),
    }
)

SEND_METER_READING_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required("readings"): vol.All(cv.ensure_list, [READING_SCHEMA]),
    }
)


def _resolve_meter(
    hass: HomeAssistant, entity_id: str
) -> tuple["EircSpbRuntime", Meter]:
    state = hass.states.get(entity_id)
    if state is None:
        raise HomeAssistantError(f"Сущность не найдена: {entity_id}")
    meter_id = state.attributes.get(ATTR_METER_ID)
    if not meter_id:
        raise HomeAssistantError(f"Сущность без счётчика: {entity_id}")
    for runtime in hass.data.get(DOMAIN, {}).values():
        data = getattr(runtime.coordinator, "data", None)
        if data is not None and meter_id in data.meters:
            return runtime, data.meters[meter_id]
    raise HomeAssistantError(f"Счётчик недоступен: {entity_id}")


async def async_setup_services(hass: HomeAssistant) -> None:
    async def handle_send_meter_reading(call: ServiceCall) -> ServiceResponse:
        entity_ids = call.data[ATTR_ENTITY_ID]
        resolved: tuple[EircSpbRuntime, Meter] | None = None
        error: HomeAssistantError | None = None
        for entity_id in entity_ids:
            try:
                resolved = _resolve_meter(hass, entity_id)
                break
            except HomeAssistantError as err:
                error = err
        if resolved is None:
            raise error or HomeAssistantError(f"Счётчик не найден: {entity_ids}")
        runtime, meter = resolved
        readings = [
            {"scale_id": str(reading["scale_id"]), "value": reading["value"]}
            for reading in call.data["readings"]
        ]
        try:
            result = await runtime.client.submit_reading(
                meter.account_id, meter.meter_id, readings
            )
        except EircSpbApiError as err:
            raise HomeAssistantError(str(err)) from err
        for entry_runtime in hass.data.get(DOMAIN, {}).values():
            if entry_runtime.coordinator is not None:
                await entry_runtime.coordinator.async_request_refresh()
        return {
            "code": str(result.get("code", "")),
            "message": result.get("message", ""),
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_METER_READING,
        handle_send_meter_reading,
        schema=SEND_METER_READING_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
