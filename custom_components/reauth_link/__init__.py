"""Reauth Link: the account owner renews a sign-in with one tap, without opening Home Assistant."""

from __future__ import annotations

from datetime import datetime

from homeassistant.config_entries import (
    SIGNAL_CONFIG_ENTRY_CHANGED,
    ConfigEntry,
    ConfigEntryChange,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from . import const
from .const import CONF_TIME, DEFAULT_TIME, DOMAIN, SERVICE_GENERATE_LINK
from .manager import ReauthLinkManager, async_forget
from .view import CallbackView

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the callback and the action once: neither goes away on a reload."""
    hass.http.register_view(CallbackView(hass))

    async def _generate_link(call: ServiceCall) -> ServiceResponse:
        manager: ReauthLinkManager | None = hass.data.get(DOMAIN)
        if manager is None or not manager.subentries():
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_logins")
        return {"links": await manager.async_links()}

    async_register_admin_service(
        hass, DOMAIN, SERVICE_GENERATE_LINK, _generate_link, supports_response=SupportsResponse.ONLY
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from the config entry."""
    manager = ReauthLinkManager(hass, entry)
    await manager.async_load()
    hass.data[DOMAIN] = manager

    at = dt_util.parse_time(entry.data.get(CONF_TIME, "")) or dt_util.parse_time(DEFAULT_TIME)

    async def _daily(now: datetime) -> None:
        await manager.async_cycle(now)

    entry.async_on_unload(
        async_track_time_change(hass, _daily, hour=at.hour, minute=at.minute, second=0)
    )

    @callback
    def _target_changed(change: ConfigEntryChange, other: ConfigEntry) -> None:
        """A watched login appeared, left or changed."""
        if other.domain in const.SUPPORTED_DOMAINS:
            manager.async_sync()

    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, _target_changed)
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    manager.async_sync()  # a renewal may have been cut short by a power loss
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The address, the time or a login changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload: the callback stays, but without a manager it only says the link is not valid."""
    hass.data.pop(DOMAIN, None)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Forget the pending links."""
    await async_forget(hass, entry)
