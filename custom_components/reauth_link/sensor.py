"""When the sign-in expires."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .entity import ReauthLinkEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """One per login."""
    manager = hass.data[DOMAIN]
    for subentry in manager.subentries():
        async_add_entities(
            [ExpiresSensor(manager, subentry, "expires")], config_subentry_id=subentry.subentry_id
        )


class ExpiresSensor(ReauthLinkEntity, SensorEntity):
    """The date the sign-in expires."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> datetime | None:
        """The date."""
        return self.manager.expires_at(self.subentry)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Never the link: any Home Assistant user can read attributes."""
        if (state := self.manager.state(self.subentry)) is None:
            return {}
        return {
            "signed_in_at": state.signed_in_at,
            "last_notice": state.last_notice,
            "last_result": state.last_result,
        }
