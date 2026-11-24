"""The way back: a public address that only acts on a link of ours."""

from __future__ import annotations

import asyncio
from html import escape

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import CALLBACK_PATH, DOMAIN
from .texts import async_texts

# result -> text of the page
_PAGES = {
    "reauth_successful": "done",
    "reconfigure_successful": "done",
    "already_renewed": "already",
    "invalid_link": "invalid",
    "denied": "denied",
    "reauth_account_mismatch": "account",
    "unique_id_mismatch": "account",
    "user_not_premium": "premium",
    "not_accepted": "incomplete",
    "unconfirmed": "received",
}
_ICONS = {"done": "✅", "already": "✅", "received": "⏳"}  # the good ones


async def _page(hass: HomeAssistant, result: str, integration: str = "") -> web.Response:
    texts = await async_texts(hass)
    page = _PAGES.get(result, "failed")
    title = escape(texts[f"page_{page}_title"])
    text = escape(texts[f"page_{page}_text"].format(integration=integration))
    body = (
        f"<!doctype html><html lang='{escape(texts['page_lang'])}'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title><style>body{{font:17px/1.5 system-ui,sans-serif;margin:0;min-height:100vh;"
        "display:grid;place-items:center;background:#f4f5f7;color:#1c1e21}main{max-width:26rem;padding:2rem;text-align:center}"
        "h1{font-size:1.6rem;margin:.5rem 0}@media(prefers-color-scheme:dark){body{background:#16181b;color:#eceef0}}</style>"
        f"<main><div style='font-size:3rem'>{_ICONS.get(page, '⚠️')}</div><h1>{title}</h1><p>{text}</p></main></html>"
    )
    return web.Response(
        text=body,
        content_type="text/html",
        status=200 if page in _ICONS else 400,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
        },
    )


class CallbackView(HomeAssistantView):
    """Receives the code. Without a `state` of ours it starts no flow and names nothing."""

    url = CALLBACK_PATH
    name = "api:reauth_link:callback"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize."""
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Finish the renewal within this request."""
        manager = self.hass.data.get(DOMAIN)
        state = request.query.get("state", "")
        if manager is None or not state:
            return await _page(self.hass, "invalid_link")
        if (subentry := manager.async_find_link(state)) is None:
            if (subentry := manager.async_find_used(state)) is not None:
                result = await manager.async_used_result(subentry, state)
                return await _page(self.hass, result, manager.name(subentry))
            return await _page(self.hass, "invalid_link")
        name = manager.name(subentry)
        if "error" in request.query:
            return await _page(self.hass, "denied", name)
        if not (code := request.query.get("code")):
            return await _page(self.hass, "invalid_link")
        # the owner may close the page meanwhile, and Home Assistant cancels the request: the
        # code can be spent already, so the renewal goes on to the end
        finish = self.hass.async_create_task(
            manager.async_finish(state, code), f"{DOMAIN} renewal"
        )
        return await _page(self.hass, await asyncio.shield(finish), name)
