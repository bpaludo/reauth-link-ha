"""The links, the way back and the calendar.

How it works:
- the link is the `authorize` URL the target integration itself builds, with this Home Assistant's `redirect_uri` and a
  `state` of ours. So it needs no open flow, no browser window and survives restarts: the reauthentication only starts on
  the way back, with the code in hand, and finishes by itself;
- the target integration exchanges the code for tokens, the usual way. No token is read here.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta
import hashlib
import hmac
import logging
import secrets
from typing import Any

from yarl import URL

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigSubentry,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import const
from .const import (
    CALLBACK_PATH,
    CONF_ACCOUNT,
    CONF_ACCOUNT_NAME,
    CONF_ADMIN,
    CONF_DAYS,
    CONF_DOMAIN,
    CONF_ENTRY_ID,
    CONF_OWNER,
    CONF_URL,
    DEFAULT_DAYS,
    DOMAIN,
    ESCALATE_BEFORE_DAYS,
    EVENT_FINISHED,
    LINK_MARGIN,
    LINK_VALIDITY,
    MAX_ATTEMPTS,
    MAX_TRIES,
    NOTIFY_BEFORE_DAYS,
    NOTIFY_TIMEOUT,
    REPEAT_DAYS,
    SETTLE_TIMEOUT,
    SIGNAL_UPDATED,
    SUBENTRY_LOGIN,
)
from .oauth import (
    ReauthLinkError,
    account_name,
    async_authorize_url,
    async_close_flow,
    async_integration_name,
    async_open_flow,
    async_requests,
    keep_reauth_issue,
)
from .texts import async_texts

_LOGGER = logging.getLogger(__name__)
_SUCCESS = {"reauth_successful", "reconfigure_successful"}
_DATA_SHARED = f"{DOMAIN}_shared"
_BUSY = {ConfigEntryState.SETUP_IN_PROGRESS, ConfigEntryState.UNLOAD_IN_PROGRESS}


def _store(hass: HomeAssistant) -> Store[dict[str, Any]]:
    return Store(hass, 1, DOMAIN, private=True)


async def async_forget(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The integration was removed: no link survives."""
    hass.data.get(_DATA_SHARED, {}).pop(entry.entry_id, None)
    await _store(hass).async_remove()


@callback
def async_targets(hass: HomeAssistant) -> list[ConfigEntry]:
    """Every login of the supported integrations in this Home Assistant."""
    return [
        entry
        for domain in const.SUPPORTED_DOMAINS
        for entry in hass.config_entries.async_entries(
            domain, include_ignore=False, include_disabled=False
        )
    ]


@callback
def async_find_target(hass: HomeAssistant, data: Any) -> ConfigEntry | None:
    """The login of a subentry: the same entry or, if it was redone, the same account."""
    targets = [t for t in async_targets(hass) if t.domain == data[CONF_DOMAIN]]
    for target in targets:
        if target.entry_id == data[CONF_ENTRY_ID]:
            return target
    for target in targets:
        if data[CONF_ACCOUNT] and target.unique_id == data[CONF_ACCOUNT]:
            return target
    return None


@dataclass
class LoginState:
    """What is kept about each login (never a token)."""

    target_id: str
    signed_in_at: str | None  # None: the entry is older than Home Assistant keeping that date
    link: str | None = None
    link_at: str | None = None
    attempts: int = 0  # failed codes with the current link
    used: str | None = None  # the last finished link, so reloading the page says "already done"
    unconfirmed_at: str | None = None  # approved, but the integration has not come back yet
    rejected: bool = False  # the last renewal left the integration asking to sign in again
    last_notice: str | None = None  # the owner was told
    last_late: str | None = None  # the administrator was told the owner has not renewed
    last_problem: str | None = None  # the administrator was told the owner could not be reached
    last_result: str | None = None


_FIELDS = {f.name for f in fields(LoginState)}


@dataclass
class _Shared:
    """Outlives a reload of this integration, which may happen while a renewal is on its way."""

    url: str | None = None
    loaded: bool = False
    states: dict[str, LoginState] = field(default_factory=dict)
    locks: defaultdict[str, asyncio.Lock] = field(
        default_factory=lambda: defaultdict(asyncio.Lock)
    )
    # codes tried with the current link of each login: how many times, by digest. Never saved
    tried: defaultdict[str, dict[str, int]] = field(default_factory=lambda: defaultdict(dict))


class ReauthLinkManager:
    """One per Home Assistant: looks after every login chosen by the user."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry
        self.names: dict[str, str] = {}
        self._store = _store(hass)
        self._shared: _Shared = hass.data.setdefault(_DATA_SHARED, {}).setdefault(
            entry.entry_id, _Shared()
        )

    # ---------------------------------------------------------------- data
    @property
    def redirect_uri(self) -> str:
        """The address registered in the Spotify app."""
        return self.entry.data[CONF_URL] + CALLBACK_PATH

    @property
    def _states(self) -> dict[str, LoginState]:
        return self._shared.states

    async def async_load(self) -> None:
        """Read what was saved."""
        shared, url = self._shared, self.entry.data[CONF_URL]
        if not shared.loaded:
            saved = await self._store.async_load() or {}
            shared.url = saved.get("url", url)
            for key, value in saved.get("logins", {}).items():
                # a newer version may have saved more
                shared.states[key] = LoginState(**{k: v for k, v in value.items() if k in _FIELDS})
            shared.loaded = True
        if shared.url != url:
            # a link already sent carries the old address: Spotify would refuse the code
            for state in shared.states.values():
                state.link = state.link_at = None
            shared.url = url
        for subentry in self.subentries():
            domain = subentry.data[CONF_DOMAIN]
            self.names[domain] = await async_integration_name(self.hass, domain)
        self._sync_states()
        await self._async_save()

    async def _async_save(self) -> None:
        if self.hass.config_entries.async_get_entry(self.entry.entry_id) is None:
            return  # removed while a renewal was on its way: leave no file behind
        try:
            await self._store.async_save(
                {
                    "url": self._shared.url,
                    "logins": {k: asdict(v) for k, v in self._states.items()},
                }
            )
        except Exception as err:  # e.g. a full disk: the renewal itself is not lost
            _LOGGER.error("Could not save: %s", type(err).__name__)
        async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    def _sync_states(self) -> bool:
        """New, redone and removed logins. Says whether something changed."""
        changed = False
        subentries = {s.subentry_id: s for s in self.subentries()}
        for subentry_id in set(self._states) - set(subentries):
            del self._states[subentry_id]
            changed = True
        for subentry_id, subentry in subentries.items():
            if (target := self.target(subentry)) is None:
                continue
            state = self._states.get(subentry_id)
            if state is None or state.target_id != target.entry_id:
                # first time, or the login was redone from scratch: the clock restarts.
                # entries older than the date itself were given the year 1970
                known = target.created_at.year > 1970
                self._states[subentry_id] = LoginState(
                    target_id=target.entry_id,
                    signed_in_at=target.created_at.isoformat() if known else None,
                )
                changed = True
        return changed

    @callback
    def async_sync(self) -> None:
        """A login changed in Home Assistant."""
        changed = self._sync_states()
        for subentry in self.subentries():
            changed = self._reconcile(subentry) or changed
        if changed:
            self.hass.async_create_task(self._async_save())
        else:
            async_dispatcher_send(self.hass, SIGNAL_UPDATED)

    @callback
    def _reconcile(self, subentry: ConfigSubentry) -> bool:
        """A login that was left waiting, or refused, is working now."""
        target, state = self.target(subentry), self.state(subentry)
        if (
            target is None
            or state is None
            or not (state.unconfirmed_at or state.rejected)
            or self._shared.locks[subentry.subentry_id].locked()  # a renewal is deciding it
            or target.state is not ConfigEntryState.LOADED
        ):
            return False
        if state.unconfirmed_at:
            self._confirm(subentry, target, state)
        elif not async_requests(self.hass, target):  # signed in again by hand
            state.signed_in_at = dt_util.utcnow().isoformat()
            state.rejected = False
            state.last_notice = state.last_late = state.last_problem = None
        else:
            return False
        return True

    @callback
    def _confirm(
        self, subentry: ConfigSubentry, target: ConfigEntry, state: LoginState, announce: bool = True
    ) -> None:
        """The integration took the new sign-in."""
        state.signed_in_at, state.unconfirmed_at = state.unconfirmed_at, None
        state.rejected = False
        state.last_notice = state.last_late = state.last_problem = None
        for stale in async_requests(self.hass, target):  # asked before the new sign-in got there
            async_close_flow(self.hass, stale)
        if announce:
            state.last_result = "reauth_successful"
            self._announce(subentry, target, state.last_result)

    def subentries(self) -> list[ConfigSubentry]:
        """The logins the user chose."""
        return [s for s in self.entry.subentries.values() if s.subentry_type == SUBENTRY_LOGIN]

    @callback
    def target(self, subentry: ConfigSubentry) -> ConfigEntry | None:
        """The login, if it is (still) in this Home Assistant."""
        return async_find_target(self.hass, subentry.data)

    def state(self, subentry: ConfigSubentry) -> LoginState | None:
        """What is kept about this login."""
        return self._states.get(subentry.subentry_id)

    def expires_at(self, subentry: ConfigSubentry) -> datetime | None:
        """When the sign-in expires: the last one we know of, plus the days chosen for this login."""
        state = self.state(subentry)
        if state is None or state.signed_in_at is None:
            return None
        days = int(subentry.data.get(CONF_DAYS, DEFAULT_DAYS))
        return dt_util.parse_datetime(state.signed_in_at, raise_on_error=True) + timedelta(days=days)

    def _days_left(self, subentry: ConfigSubentry, now: datetime) -> int:
        """Whole days, by the calendar of this Home Assistant. An unknown date counts as today."""
        if (expires := self.expires_at(subentry)) is None:
            return 0
        return (dt_util.as_local(expires).date() - dt_util.as_local(now).date()).days

    def due(self, subentry: ConfigSubentry, now: datetime | None = None) -> bool:
        """Is it time to renew?"""
        if (state := self.state(subentry)) is None:
            return False
        left = self._days_left(subentry, now or dt_util.utcnow())
        return state.rejected or left <= NOTIFY_BEFORE_DAYS

    # ---------------------------------------------------------------- links
    async def async_link(self, subentry: ConfigSubentry) -> str:
        """The owner's link. It stays the same until used or near its end."""
        target, state = self.target(subentry), self.state(subentry)
        if target is None or state is None:
            raise ReauthLinkError("login_gone", subentry.title)
        url = await async_authorize_url(self.hass, target)
        now = dt_util.utcnow()
        if not self._link_valid(state, now + LINK_MARGIN):
            state.link, state.link_at, state.attempts = secrets.token_urlsafe(32), now.isoformat(), 0
            self._shared.tried.pop(subentry.subentry_id, None)
            await self._async_save()
        return str(
            URL(url).update_query({"redirect_uri": self.redirect_uri, "state": state.link})
        )

    async def async_links(self) -> list[dict[str, str]]:
        """Every login's link, or why there is none."""
        links = []
        for subentry in self.subentries():
            item = {"login": subentry.title, "domain": subentry.data[CONF_DOMAIN]}
            if (expires := self.expires_at(subentry)) is not None:
                item["expires"] = expires.isoformat()
            try:
                item["link"] = await self.async_link(subentry)
            except ReauthLinkError as err:
                item["error"] = err.key
            links.append(item)
        return links

    @staticmethod
    def _link_valid(state: LoginState, now: datetime) -> bool:
        return bool(
            state.link
            and state.link_at
            and now - dt_util.parse_datetime(state.link_at, raise_on_error=True) <= LINK_VALIDITY
        )

    @callback
    def async_find_link(self, received: str) -> ConfigSubentry | None:
        """Whose link is this? Compares every one, in constant time."""
        now, found = dt_util.utcnow(), None
        value = received.encode(errors="ignore")
        for subentry in self.subentries():
            state = self.state(subentry)
            if (
                state is not None
                and self._link_valid(state, now)
                and hmac.compare_digest(state.link.encode(), value)
                and self.target(subentry) is not None
            ):
                found = subentry
        return found

    @callback
    def async_find_used(self, received: str) -> ConfigSubentry | None:
        """Was this link just finished?"""
        value = received.encode(errors="ignore")
        for subentry in self.subentries():
            state = self.state(subentry)
            if state is not None and state.used and hmac.compare_digest(state.used.encode(), value):
                return subentry
        return None

    def name(self, subentry: ConfigSubentry) -> str:
        """The integration's name."""
        domain = subentry.data[CONF_DOMAIN]
        return self.names.get(domain, domain)

    def account(self, subentry: ConfigSubentry) -> str:
        """The account's name."""
        if (target := self.target(subentry)) is None:
            return subentry.data.get(CONF_ACCOUNT_NAME) or subentry.title
        return account_name(target, self.name(subentry))

    # ---------------------------------------------------------------- the way back
    async def async_used_result(self, subentry: ConfigSubentry, received: str) -> str:
        """The page for a link that was spent. If that renewal is still on its way, wait for it."""
        async with self._shared.locks[subentry.subentry_id]:
            state = self.state(subentry)
        if state is None or state.used != received:
            return "not_accepted" if state is not None and state.rejected else "invalid_link"
        return "unconfirmed" if state.unconfirmed_at else "already_renewed"

    async def async_finish(self, received: str, code: str) -> str:
        """Hand the code over to the target's flow and finish it. Returns the result."""
        if (subentry := self.async_find_link(received)) is None:
            return "invalid_link"
        async with self._shared.locks[subentry.subentry_id]:
            if self.async_find_link(received) is None:  # the double tap got here later
                state = self.state(subentry)
                if state is None or state.used != received:
                    return "not_accepted" if state is not None and state.rejected else "invalid_link"
                return "unconfirmed" if state.unconfirmed_at else "already_renewed"
            target, state = self.target(subentry), self.state(subentry)
            tried, digest = self._shared.tried[subentry.subentry_id], hashlib.sha256(code.encode(errors="ignore")).hexdigest()
            if tried.get(digest, 0) >= MAX_TRIES:
                return "error"
            tried[digest] = tried.get(digest, 0) + 1
            with keep_reauth_issue(self.hass, target):
                outcome = await self._async_exchange(target, code)
                if outcome in _SUCCESS:
                    # requests Home Assistant itself opened are now stale
                    for stale in async_requests(self.hass, target):
                        async_close_flow(self.hass, stale)
            if outcome in _SUCCESS:
                # spent, whatever comes next: saved before waiting for the integration
                state.used, state.link, state.link_at, state.attempts = received, None, None, 0
                state.unconfirmed_at = dt_util.utcnow().isoformat()
                self._shared.tried.pop(subentry.subentry_id, None)
                await self._async_save()
                verdict = await self._async_verdict(target)
                if verdict == "asks":
                    outcome = "not_accepted"
                    state.unconfirmed_at, state.rejected = None, True
                    state.used = None  # opening it again must not say "already done"
                    state.last_notice = None  # a new link goes out in the next round
                elif verdict == "ok":
                    self._confirm(subentry, target, state, announce=False)
                else:
                    outcome = "unconfirmed"
            elif tried[digest] == 1:  # reloading the page tries the same code again
                state.attempts += 1
                if state.attempts >= MAX_ATTEMPTS:
                    state.link = state.link_at = None
                    state.last_notice = None  # the owner is not left without a link for a week
                    self._shared.tried.pop(subentry.subentry_id, None)
                    self.hass.async_create_task(self._async_notify(subentry, "admin", "admin_link_off"))
            state.last_result = outcome
            await self._async_save()
        _LOGGER.info("Renewing %s: %s", target.domain, outcome)
        self._announce(subentry, target, outcome)
        self.async_sync()  # it may have come back in the meantime
        return outcome

    async def _async_exchange(self, target: ConfigEntry, code: str) -> str:
        """The target's own reauthentication, with the code in hand."""
        flow_id = None
        try:
            flow_id, _ = await async_open_flow(self.hass, target)
            flows = self.hass.config_entries.flow
            result = await flows.async_configure(
                flow_id,
                {"code": code, "state": {"flow_id": flow_id, "redirect_uri": self.redirect_uri}},
            )
            if result["type"] is FlowResultType.EXTERNAL_STEP_DONE:
                result = await flows.async_configure(flow_id)
            return result.get("reason") or str(result["type"])
        except ReauthLinkError as err:
            return err.key
        except Exception as err:  # the page has to answer
            # only the type: the message may carry request parameters
            _LOGGER.error("Renewing %s failed: %s", target.domain, type(err).__name__)
            return "error"
        finally:
            if flow_id:
                async_close_flow(self.hass, flow_id)

    async def _async_verdict(self, target: ConfigEntry) -> str:
        """After the reload: "ok", "asks" (wants another sign-in, e.g. lacks a permission) or "waiting".

        Some integrations turn a missing permission into "not ready", so anything that is not loaded
        is never taken for a success.
        """
        try:
            async with asyncio.timeout(SETTLE_TIMEOUT):
                async with target.setup_lock:  # the reload was already queued on it
                    pass
                while target.state in _BUSY:
                    await asyncio.sleep(0.2)
        except TimeoutError:
            return "waiting"
        if target.state is ConfigEntryState.LOADED:
            return "ok"
        for _ in range(20):  # Home Assistant opens its request in a task of its own
            if async_requests(self.hass, target):
                return "asks"
            if target.state is not ConfigEntryState.SETUP_ERROR:
                break
            await asyncio.sleep(0.1)
        return "waiting"

    @callback
    def _announce(self, subentry: ConfigSubentry, target: ConfigEntry, outcome: str) -> None:
        """The event and the notices, which do not hold the page."""
        self.hass.bus.async_fire(
            EVENT_FINISHED,
            {"domain": target.domain, "config_entry_id": target.entry_id, "result": outcome},
        )
        self.hass.async_create_task(self._async_notify_outcome(subentry, outcome))

    # ---------------------------------------------------------------- notices
    async def _async_notify(
        self, subentry: ConfigSubentry, who: str, key: str, data: dict[str, Any] | None = None, **extra: str
    ) -> bool:
        """Send one of our texts, in the language of this Home Assistant. Says whether anyone got it."""
        texts = await async_texts(self.hass)
        expires = self.expires_at(subentry)
        values = {
            "integration": self.name(subentry),
            "account": self.account(subentry),
            "date": dt_util.as_local(expires).strftime(texts["date_format"]) if expires else "",
            **extra,
        }
        payload = {
            "title": texts[f"{who}_title"].format(**values),
            "message": texts[key].format(**values),
            "data": {"tag": f"{DOMAIN}_{subentry.subentry_id}", "channel": texts["channel"], **(data or {})},
        }
        delivered = False
        for action in subentry.data.get(CONF_OWNER if who == "owner" else CONF_ADMIN, []):
            domain, _, service = action.partition(".")
            try:
                # blocking: a failure left to Home Assistant would log the whole call, link included
                async with asyncio.timeout(NOTIFY_TIMEOUT):
                    await self.hass.services.async_call(domain, service, payload, blocking=True)
            except Exception as err:  # one bad action does not silence the others
                _LOGGER.warning("The notify action %s failed: %s", action, type(err).__name__)
            else:
                delivered = True
        return delivered

    async def async_send(self, subentry: ConfigSubentry, now: datetime | None = None) -> str | None:
        """Send the link to the account owner. Returns why it reached no one, if so."""
        link = await self.async_link(subentry)
        now = now or dt_util.utcnow()
        state, expires = self.state(subentry), self.expires_at(subentry)
        if state.rejected:
            key = "owner_again"
        elif expires is None:
            key = "owner_unknown"
        else:
            key = "owner_expired" if expires <= now else "owner_renew"
        if not subentry.data.get(CONF_OWNER):
            return "owner_none"
        # url: iOS; clickAction: Android
        if not await self._async_notify(subentry, "owner", key, {"url": link, "clickAction": link}):
            return "owner_unreachable"
        state.last_notice = now.isoformat()
        await self._async_save()
        return None

    async def _async_notify_outcome(self, subentry: ConfigSubentry, outcome: str) -> None:
        if outcome in _SUCCESS:
            await self._async_notify(subentry, "owner", "owner_done")
            await self._async_notify(subentry, "admin", "admin_done")
        elif outcome == "not_accepted":
            await self._async_notify(subentry, "admin", "admin_rejected")
        elif outcome == "unconfirmed":
            await self._async_notify(subentry, "admin", "admin_unconfirmed")

    async def async_cycle(self, now: datetime | None = None) -> None:
        """Once a day: tell the owner and, near the end, the administrator."""
        now = now or dt_util.utcnow()
        for subentry in self.subentries():
            try:
                await self._async_cycle_login(subentry, now)
            except Exception as err:  # one login does not hold the others
                _LOGGER.error(
                    "The daily check of %s failed: %s", subentry.data[CONF_DOMAIN], type(err).__name__
                )
        await self._async_save()

    async def _async_cycle_login(self, subentry: ConfigSubentry, now: datetime) -> None:
        self._reconcile(subentry)
        state = self.state(subentry)
        if state is None or self.target(subentry) is None or not self.due(subentry, now):
            return
        told_before = state.last_notice is not None
        if self._again(state.last_notice, now):
            problem, details = None, {}
            try:
                problem = await self.async_send(subentry, now)
            except ReauthLinkError as err:
                problem, details = err.key, err.placeholders
            if problem and self._again(state.last_problem, now):
                _LOGGER.error("The owner of %s was not told: %s", subentry.data[CONF_DOMAIN], problem)
                state.last_result = problem
                texts = await async_texts(self.hass)
                values = {"integration": self.name(subentry), **details}
                reason = texts.get(f"reason_{problem}", problem).format(**values)
                if await self._async_notify(subentry, "admin", "admin_failed", reason=reason):
                    state.last_problem = now.isoformat()
        left, expires = self._days_left(subentry, now), self.expires_at(subentry)
        if left <= ESCALATE_BEFORE_DAYS and self._again(state.last_late, now):
            if expires is None:
                key = "admin_unknown" if told_before else None  # give the owner a week first
            else:
                key = "admin_expired" if expires <= now else "admin_late"
            if key and await self._async_notify(subentry, "admin", key):
                state.last_late = now.isoformat()

    @staticmethod
    def _again(last: str | None, now: datetime) -> bool:
        """Never, or a week ago by the calendar: the time of the day drifts by milliseconds."""
        if not last:
            return True
        day = dt_util.as_local(dt_util.parse_datetime(last, raise_on_error=True)).date()
        return (dt_util.as_local(now).date() - day).days >= REPEAT_DAYS
