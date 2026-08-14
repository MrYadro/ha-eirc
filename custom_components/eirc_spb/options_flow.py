from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlowResult, OptionsFlow

from .const import (
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
        current = self.config_entry.options.get(
            "scan_interval_hours", DEFAULT_SCAN_INTERVAL_HOURS
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional("scan_interval_hours", default=current): vol.All(
                        vol.Coerce(float),
                        vol.Range(
                            min=MIN_SCAN_INTERVAL_HOURS, max=MAX_SCAN_INTERVAL_HOURS
                        ),
                    )
                }
            ),
        )
