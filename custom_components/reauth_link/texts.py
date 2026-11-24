"""The page and the notices, in the language of this Home Assistant (English when there is no translation)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.translation import async_get_translations

from .const import DOMAIN

_PREFIX = f"component.{DOMAIN}.common."


async def async_texts(hass: HomeAssistant) -> dict[str, str]:
    """Our texts."""
    found = await async_get_translations(hass, hass.config.language, "common", {DOMAIN})
    return {k.removeprefix(_PREFIX): v for k, v in found.items() if k.startswith(_PREFIX)}
