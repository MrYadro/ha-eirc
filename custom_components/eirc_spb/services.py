import os
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

from .const import ATTR_ACCOUNT_ID, ATTR_METER_ID, ATTR_SCALE_ID, DOMAIN
from .exceptions import EircSpbApiError
from .models import Meter

if TYPE_CHECKING:
    from . import EircSpbRuntime

SERVICE_SEND_METER_READING = "send_meter_reading"
SERVICE_DOWNLOAD_BILL = "download_bill"

READING_SCHEMA = vol.Schema(
    {
        vol.Optional("scale_id"): vol.Any(int, str),
        vol.Required("value"): vol.Coerce(float),
    }
)

SEND_METER_READING_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required("readings"): vol.All(cv.ensure_list, [READING_SCHEMA]),
        vol.Optional("confirm", default=False): bool,
    }
)

DOWNLOAD_BILL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional("bill_id"): str,
        vol.Optional("path"): str,
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


def _resolve_account_and_bill(
    hass: HomeAssistant, entity_id: str
) -> tuple["EircSpbRuntime", str, str]:
    state = hass.states.get(entity_id)
    if state is None:
        raise HomeAssistantError(f"Сущность не найдена: {entity_id}")
    account_id = state.attributes.get(ATTR_ACCOUNT_ID)
    bill_id = state.attributes.get("bill_id")
    if not account_id or not bill_id:
        raise HomeAssistantError(f"Сущность без счёта: {entity_id}")
    for runtime in hass.data.get(DOMAIN, {}).values():
        data = getattr(runtime.coordinator, "data", None)
        if data is not None and account_id in data.accounts:
            return runtime, account_id, str(bill_id)
    raise HomeAssistantError(f"Счёт недоступен: {entity_id}")


async def async_setup_services(hass: HomeAssistant) -> None:
    async def handle_send_meter_reading(call: ServiceCall) -> ServiceResponse:
        entity_ids = call.data[ATTR_ENTITY_ID]
        resolved: tuple[EircSpbRuntime, Meter, str] | None = None
        error: HomeAssistantError | None = None
        for entity_id in entity_ids:
            try:
                runtime, meter = _resolve_meter(hass, entity_id)
                resolved = (runtime, meter, entity_id)
                break
            except HomeAssistantError as err:
                error = err
        if resolved is None:
            raise error or HomeAssistantError(f"Счётчик не найден: {entity_ids}")
        runtime, meter, resolved_entity_id = resolved
        readings = []
        entity_scale_id = hass.states.get(resolved_entity_id).attributes.get(
            ATTR_SCALE_ID
        )
        untagged = [r for r in call.data["readings"] if "scale_id" not in r]
        if untagged:
            if len(call.data["readings"]) > 1:
                raise HomeAssistantError(
                    "scale_id обязателен, когда передаётся несколько показаний"
                )
            if not entity_scale_id:
                raise HomeAssistantError(
                    f"У сущности нет атрибута scale_id: {entity_ids[0]}"
                )
        for reading in call.data["readings"]:
            scale_id = (
                str(reading["scale_id"])
                if "scale_id" in reading
                else str(entity_scale_id)
            )
            readings.append({"scale_id": scale_id, "value": reading["value"]})
        validated = False
        for reading in readings:
            result = await runtime.client.validate_reading(
                meter.account_id,
                meter.meter_id,
                reading["scale_id"],
                reading["value"],
            )
            if result["status"] == "error":
                raise HomeAssistantError(
                    result.get("message") or "Сервер отклонил показания"
                )
            if result["status"] == "warning" and not call.data["confirm"]:
                raise HomeAssistantError(
                    f"Подтвердите отправку (confirm: true): "
                    f"{result.get('message') or 'необычный расход'}"
                )
            if result["status"] in {"ok", "warning"}:
                validated = True
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
            "validated": validated,
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_METER_READING,
        handle_send_meter_reading,
        schema=SEND_METER_READING_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def handle_download_bill(call: ServiceCall) -> ServiceResponse:
        runtime, account_id, bill_id = _resolve_account_and_bill(
            hass, call.data[ATTR_ENTITY_ID]
        )
        bill_id = str(call.data.get("bill_id") or bill_id)
        account = runtime.coordinator.data.accounts[account_id]
        default_dir = hass.config.path("www", "eirc")
        target = call.data.get("path") or os.path.join(
            default_dir, f"{account.number}_{bill_id}.pdf"
        )
        target = os.path.abspath(target)
        config_dir = os.path.abspath(hass.config.config_dir)
        if not target.startswith(config_dir + os.sep):
            raise HomeAssistantError(
                "Путь должен находиться внутри каталога конфигурации Home Assistant"
            )
        data = await runtime.client.download_bill(account_id, bill_id)
        if data is None:
            raise HomeAssistantError(f"Файл счёта недоступен: {bill_id}")

        def _write() -> None:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(data)

        await hass.async_add_executor_job(_write)
        rel = os.path.relpath(target, hass.config.path("www"))
        return {
            "path": target,
            "url": f"/local/{rel}",
            "bytes": len(data),
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_DOWNLOAD_BILL,
        handle_download_bill,
        schema=DOWNLOAD_BILL_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
