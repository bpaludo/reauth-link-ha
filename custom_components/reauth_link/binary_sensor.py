"""Is it time to renew?"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
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
            [RenewalDue(manager, subentry, "renewal_due")], config_subentry_id=subentry.subentry_id
        )


class RenewalDue(ReauthLinkEntity, BinarySensorEntity):
    """On from 30 days before the sign-in expires."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool:
        """Is it time?"""
        return self.manager.due(self.subentry)
