"""When the sign-in expires, and how the login is."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, LoginStatus
from .entity import ReauthLinkEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """One per login."""
    manager = hass.data[DOMAIN]
    for subentry in manager.subentries():
        async_add_entities(
            [
                ExpiresSensor(manager, subentry, "expires"),
                StatusSensor(manager, subentry, "status"),
            ],
            config_subentry_id=subentry.subentry_id,
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


class StatusSensor(ReauthLinkEntity, SensorEntity):
    """How the login is. An estimate: it goes by the last sign-in we know of, and reads no token."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = [status.value for status in LoginStatus]

    @property
    def native_value(self) -> LoginStatus | None:
        """No attributes on purpose: there is nothing else to tell."""
        return self.manager.status(self.subentry)
