"""Send the link now."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .entity import ReauthLinkEntity
from .oauth import ReauthLinkError


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """One per login."""
    manager = hass.data[DOMAIN]
    for subentry in manager.subentries():
        async_add_entities(
            [SendLink(manager, subentry, "send_link")], config_subentry_id=subentry.subentry_id
        )


class SendLink(ReauthLinkEntity, ButtonEntity):
    """Send the link to the account owner."""

    _attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        """Send it, and say so when it reached no one."""
        if (problem := await self.manager.async_send(self.subentry)) is not None:
            raise ReauthLinkError(problem, self.manager.name(self.subentry))
