from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlowResult, OptionsFlow

from .const import (
    CONF_DEADLINE_DAYS,
    CONF_PERSISTENT_NOTIFICATIONS,
    DEFAULT_DEADLINE_DAYS,
    DEFAULT_SCAN_INTERVAL_HOURS,
    MIN_SCAN_INTERVAL_HOURS,
)

MAX_SCAN_INTERVAL_HOURS = 24


class EircSpbOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        options = self.config_entry.options
        current_interval = options.get(
            "scan_interval_hours", DEFAULT_SCAN_INTERVAL_HOURS
        )
        current_deadline = options.get(CONF_DEADLINE_DAYS, DEFAULT_DEADLINE_DAYS)
        current_persistent = options.get(CONF_PERSISTENT_NOTIFICATIONS, True)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional("scan_interval_hours", default=current_interval): vol.All(
                        vol.Coerce(float),
                        vol.Range(
                            min=MIN_SCAN_INTERVAL_HOURS, max=MAX_SCAN_INTERVAL_HOURS
                        ),
                    ),
                    vol.Optional(
                        CONF_DEADLINE_DAYS, default=current_deadline
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=10)),
                    vol.Optional(
                        CONF_PERSISTENT_NOTIFICATIONS, default=current_persistent
                    ): bool,
                }
            ),
        )
