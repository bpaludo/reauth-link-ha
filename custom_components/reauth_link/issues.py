"""What needs a person, in Settings > System > Repairs.

They are never persistent: Home Assistant saves a persistent one, and puts it in the downloaded diagnostics, with the
name of the account. The identifiers only carry identifiers of Home Assistant's, and the texts carry no link.
"""

from __future__ import annotations

from typing import NamedTuple

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .const import DOMAIN, ESCALATE_BEFORE_DAYS, README_URL
from .manager import ReauthLinkManager
from .oauth import account_name
from .texts import cached_texts


class _Issue(NamedTuple):
    key: str
    severity: ir.IssueSeverity
    placeholders: dict[str, str]


@callback
def async_update_issues(hass: HomeAssistant) -> None:
    """Create what is missing, update what changed and delete what is over.

    An issue that still stands is never deleted and created again: the user may have ignored it.
    """
    manager: ReauthLinkManager | None = hass.data.get(DOMAIN)
    if manager is None or manager.entry.disabled_by is not None:  # being unloaded, or disabled
        return
    wanted = _wanted(hass, manager)
    _delete_all_but(hass, {*wanted, *_undecided(hass, manager)})
    for issue_id, issue in wanted.items():
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            is_persistent=False,
            learn_more_url=f"{README_URL}#repairs",
            severity=issue.severity,
            translation_key=issue.key,
            translation_placeholders=issue.placeholders,
        )


@callback
def async_clear_issues(hass: HomeAssistant) -> None:
    """The integration was removed or disabled."""
    _delete_all_but(hass, set())


@callback
def _delete_all_but(hass: HomeAssistant, wanted: set[str]) -> None:
    for domain, issue_id in list(ir.async_get(hass).issues):
        if domain == DOMAIN and issue_id not in wanted:
            ir.async_delete_issue(hass, DOMAIN, issue_id)


@callback
def _undecided(hass: HomeAssistant, manager: ReauthLinkManager) -> set[str]:
    """What not to touch: a renewal on its way may end in the same problem.

    While the approved login waits for its integration, only what the user ignored stays as it is.
    """
    registry, kept = ir.async_get(hass), set()
    for subentry in manager.subentries():
        state, deciding = manager.state(subentry), manager.deciding(subentry)
        if state is None or manager.target(subentry) is None:
            continue
        for kind in ("expiring", "rejected", "not_back"):
            issue = registry.async_get_issue(DOMAIN, f"{kind}_{subentry.subentry_id}")
            if issue is not None and (
                deciding or (state.unconfirmed_at is not None and issue.dismissed_version)
            ):
                kept.add(issue.issue_id)
    return kept


@callback
def _wanted(hass: HomeAssistant, manager: ReauthLinkManager) -> dict[str, _Issue]:
    """Every problem there is now, by identifier."""
    wanted: dict[str, _Issue] = {}
    now, date_format = dt_util.utcnow(), cached_texts(hass).get("date_format", "%Y-%m-%d")
    for subentry in manager.subentries():
        state = manager.state(subentry)
        if (
            state is None
            or manager.target(subentry) is None  # removed or disabled
            or manager.deciding(subentry)
        ):
            continue
        who = {"integration": manager.name(subentry), "account": _plain(manager.account(subentry))}
        if state.unconfirmed_at is not None:  # the owner did their part
            if manager.late(subentry, now):
                wanted[f"not_back_{subentry.subentry_id}"] = _Issue(
                    "not_back", ir.IssueSeverity.ERROR, who
                )
            continue
        if state.rejected:
            wanted[f"rejected_{subentry.subentry_id}"] = _Issue(
                "rejected", ir.IssueSeverity.ERROR, who
            )
        expires = manager.expires_at(subentry)
        if expires is not None and manager.days_left(subentry, now) <= ESCALATE_BEFORE_DAYS:
            over = expires <= now
            wanted[f"expiring_{subentry.subentry_id}"] = _Issue(
                "expired" if over else "expiring",
                ir.IssueSeverity.ERROR if over else ir.IssueSeverity.WARNING,
                {**who, "date": dt_util.as_local(expires).strftime(date_format)},
            )
    for target in manager.unlisted():
        name = manager.names.get(target.domain, target.domain)
        wanted[f"unlisted_{target.entry_id}"] = _Issue(
            "unlisted",
            ir.IssueSeverity.WARNING,
            {"integration": name, "account": _plain(account_name(target, name))},
        )
    return wanted


def _plain(account: str) -> str:
    """Its owner chose the name. The panel renders markdown, where it goes as inline code, and text in any direction."""
    return " ".join("".join(c if c.isprintable() else " " for c in account.replace("`", "'")).split())
