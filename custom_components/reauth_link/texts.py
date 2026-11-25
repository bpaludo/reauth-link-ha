"""The page and the notices, in the language of this Home Assistant (English when there is no translation)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.translation import (
    async_get_cached_translations,
    async_get_translations,
)

from .const import DOMAIN

_PREFIX = f"component.{DOMAIN}.common."


def _ours(found: dict[str, str]) -> dict[str, str]:
    return {k.removeprefix(_PREFIX): v for k, v in found.items() if k.startswith(_PREFIX)}


async def async_texts(hass: HomeAssistant) -> dict[str, str]:
    """Our texts."""
    return _ours(await async_get_translations(hass, hass.config.language, "common", {DOMAIN}))


@callback
def cached_texts(hass: HomeAssistant) -> dict[str, str]:
    """The same, without waiting: in English while those of the language are not loaded."""
    for language in (hass.config.language, "en"):
        if texts := _ours(async_get_cached_translations(hass, language, "common", DOMAIN)):
            return texts
    return {}
