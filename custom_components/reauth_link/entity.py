"""Base of the entities."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_UPDATED
from .manager import ReauthLinkManager


class ReauthLinkEntity(Entity):
    """An entity of one login, in a service device."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: ReauthLinkManager, subentry: ConfigSubentry, key: str) -> None:
        """Initialize."""
        self.manager = manager
        self.subentry = subentry
        self._attr_unique_id = f"{subentry.subentry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            translation_key="login",
            translation_placeholders={
                "integration": manager.name(subentry),
                "account": manager.account(subentry),
            },
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def target(self) -> ConfigEntry | None:
        """The login."""
        return self.manager.target(self.subentry)

    @property
    def available(self) -> bool:
        """The login may have been removed from Home Assistant."""
        return self.target is not None and self.manager.state(self.subentry) is not None

    async def async_added_to_hass(self) -> None:
        """Update when something changes."""
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_UPDATED, self.async_write_ha_state)
        )
