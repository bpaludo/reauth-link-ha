"""The diagnostics someone may attach to a public report.

No link, state, account, address or name of a notify action: only counts, dates and states.
"""

from __future__ import annotations

import re
from typing import Any

from yarl import URL

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import CONF_ADMIN, CONF_DAYS, CONF_DOMAIN, CONF_OWNER, CONF_TIME, CONF_URL, DOMAIN
from .manager import ReauthLinkManager
from .oauth import async_requests

# our own results and the reasons of the target integration are identifiers: anything else stays out
_RESULT = re.compile(r"[a-z][a-z0-9_]{0,59}")


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Everything that helps, and nothing that names someone."""
    url = URL(entry.data[CONF_URL])
    manager: ReauthLinkManager | None = hass.data.get(DOMAIN)
    return {
        "address": {
            "nabu_casa": (url.host or "").endswith(".ui.nabu.casa"),
            "own_port": url.explicit_port is not None,
        },
        "time": entry.data.get(CONF_TIME),
        "my_loaded": "my" in hass.config.components,
        "running": manager is not None,
        "logins": [] if manager is None else _logins(hass, manager),
        "logins_not_listed": [] if manager is None else sorted(t.domain for t in manager.unlisted()),
    }


@callback
def _logins(hass: HomeAssistant, manager: ReauthLinkManager) -> list[dict[str, Any]]:
    now, logins = dt_util.utcnow(), []
    for subentry in manager.subentries():
        target, state, expires = (
            manager.target(subentry),
            manager.state(subentry),
            manager.expires_at(subentry),
        )
        login: dict[str, Any] = {
            "integration": subentry.data[CONF_DOMAIN],
            "in_home_assistant": target is not None,
            "integration_state": target.state.value if target is not None else None,
            "asks_to_sign_in": target is not None and bool(async_requests(hass, target)),
            "status": manager.status(subentry, now),
            "renewal_due": manager.due(subentry, now),
            "days": subentry.data.get(CONF_DAYS),
            "days_left": manager.days_left(subentry, now) if expires is not None else None,
            "expires": expires.isoformat() if expires is not None else None,
            "owner": _actions(hass, subentry.data.get(CONF_OWNER, [])),
            "administrator": _actions(hass, subentry.data.get(CONF_ADMIN, [])),
        }
        if state is not None:
            login |= {
                "signed_in_at": state.signed_in_at,
                "link_pending": state.link is not None,
                "link_since": state.link_at,
                "failed_codes": state.attempts,
                "approved_waiting_since": state.unconfirmed_at,
                "refused": state.rejected,
                "last_notice": state.last_notice,
                "last_late": state.last_late,
                "last_problem": state.last_problem,
                "last_result": _result(state.last_result),
            }
        logins.append(login)
    return logins


def _result(value: str | None) -> str | None:
    return value if value is None or _RESULT.fullmatch(value) else "other"


@callback
def _actions(hass: HomeAssistant, actions: list[str]) -> dict[str, int]:
    """How many were chosen, and how many of those do not exist now."""
    missing = [a for a in actions if not hass.services.has_service(*_split(a))]
    return {"chosen": len(actions), "missing": len(missing)}


def _split(action: str) -> tuple[str, str]:
    domain, _, service = action.partition(".")
    return domain, service
