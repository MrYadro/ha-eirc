from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EircSpbApiClient
from .const import (
    CONF_ACCOUNTS,
    CONF_LOGIN,
    CONF_PASSWORD,
    CONF_VERIFICATION_TOKEN,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
)
from .coordinator import EircSpbCoordinator
from .exceptions import EircSpbAuthError

PLATFORMS: list[str] = ["sensor"]


@dataclass
class EircSpbRuntime:
    client: EircSpbApiClient
    coordinator: EircSpbCoordinator


def async_get_entry_data(hass: HomeAssistant, entry_id: str) -> EircSpbRuntime | None:
    return hass.data.get(DOMAIN, {}).get(entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client = EircSpbApiClient(
        entry.data[CONF_LOGIN],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_VERIFICATION_TOKEN),
        async_get_clientsession(hass),
    )
    scan_hours = entry.options.get(
        "scan_interval_hours", DEFAULT_SCAN_INTERVAL_HOURS
    )
    coordinator = EircSpbCoordinator(hass, client, entry.data[CONF_ACCOUNTS], scan_hours)
    try:
        await coordinator.async_config_entry_first_refresh()
    except EircSpbAuthError as err:
        raise ConfigEntryAuthFailed from err
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = EircSpbRuntime(
        client, coordinator
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime: EircSpbRuntime | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime:
        await runtime.client.close()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
