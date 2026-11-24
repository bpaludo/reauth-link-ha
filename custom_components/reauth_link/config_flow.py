"""Configuration from the Home Assistant screen."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

try:
    import probatio as vol
except ImportError:  # Home Assistant older than 2026.11
    import voluptuous as vol
from yarl import URL

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryData,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.util.network import is_ip_address

from .const import (
    CALLBACK_PATH,
    CONF_ACCOUNT,
    CONF_ACCOUNT_NAME,
    CONF_ADMIN,
    CONF_DAYS,
    CONF_DOMAIN,
    CONF_ENTRY_ID,
    CONF_LOGIN,
    CONF_OWNER,
    CONF_TIME,
    CONF_URL,
    DEFAULT_DAYS,
    DEFAULT_TIME,
    DOMAIN,
    MAX_DAYS,
    MIN_DAYS,
    MIN_HA_VERSION,
    SUBENTRY_LOGIN,
)
from .manager import async_find_target, async_targets
from .oauth import ReauthLinkError, account_name, async_check, async_integration_name

# notify actions that cannot take a title, a message and a link for one person
_NOT_OFFERED = {"persistent_notification", "send_message"}
_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_HOST = re.compile(rf"({_LABEL}\.)+(?=[a-z]){_LABEL}")  # a name, not a number


# ------------------------------------------------------------------ the address
@callback
def suggested_urls(hass: HomeAssistant) -> list[str]:
    """The https addresses Home Assistant knows about itself: its own domain first, then Nabu Casa."""
    found: list[str] = []
    for options in ({"allow_cloud": False}, {"require_cloud": True}):
        try:
            url = get_url(hass, allow_internal=False, require_ssl=True, **options)
        except NoURLAvailableError:
            continue
        if url not in found:
            found.append(url)
    return found


def clean_url(value: str) -> str:
    """The address alone, without path. Raises ValueError with the form's error."""
    try:
        url = URL(value.strip())
        scheme, host, port = url.scheme, url.raw_host or "", url.explicit_port
        origin = str(url.origin()).rstrip("/") if scheme and host else ""
    except ValueError as err:
        raise ValueError("invalid_url") from err
    if scheme == "http":
        raise ValueError("not_https")
    if (
        scheme != "https"
        or len(host) > 253
        or not _HOST.fullmatch(host)
        or host.endswith((".local", ".localhost"))  # the phone has to reach it from outside
        or is_ip_address(host)
        or port == 0
    ):
        raise ValueError("invalid_url")
    return origin


def _main_schema(hass: HomeAssistant, current: Mapping[str, Any]) -> vol.Schema:
    urls = suggested_urls(hass)
    if (url := current.get(CONF_URL)) and url not in urls:
        urls.insert(0, url)
    default = {"default": current.get(CONF_URL) or urls[0]} if urls else {}
    return vol.Schema(
        {
            vol.Required(CONF_URL, **default): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=urls, custom_value=True, mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
            vol.Required(
                CONF_TIME, default=current.get(CONF_TIME, DEFAULT_TIME)
            ): selector.TimeSelector(),
        }
    )


# ------------------------------------------------------------------ a login
async def _async_login_options(
    hass: HomeAssistant, targets: list[ConfigEntry]
) -> list[selector.SelectOptionDict]:
    options = []
    for target in targets:
        name = await async_integration_name(hass, target.domain)
        options.append(
            selector.SelectOptionDict(
                value=target.entry_id, label=f"{name} ({account_name(target, name)})"
            )
        )
    return options


def _login_schema(
    hass: HomeAssistant,
    current: Mapping[str, Any],
    logins: list[selector.SelectOptionDict] | None = None,
) -> vol.Schema:
    actions = sorted(
        f"notify.{service}"
        for service in hass.services.async_services_for_domain("notify")
        if service not in _NOT_OFFERED
    )
    notify = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=actions,
            multiple=True,
            custom_value=True,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )
    fields: dict[Any, Any] = {}
    if logins is not None:
        fields[
            vol.Required(CONF_LOGIN, default=current.get(CONF_LOGIN, logins[0]["value"]))
        ] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=logins, mode=selector.SelectSelectorMode.DROPDOWN
            )
        )
    fields[vol.Required(CONF_OWNER, default=list(current.get(CONF_OWNER, [])))] = notify
    fields[vol.Required(CONF_ADMIN, default=list(current.get(CONF_ADMIN, [])))] = notify
    fields[vol.Required(CONF_DAYS, default=current.get(CONF_DAYS, DEFAULT_DAYS))] = (
        selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=MIN_DAYS, max=MAX_DAYS, step=1, mode=selector.NumberSelectorMode.BOX
            )
        )
    )
    return vol.Schema(fields)


def _login_errors(hass: HomeAssistant, user_input: Mapping[str, Any]) -> dict[str, str]:
    """An action that does not exist would only show up in the log, when the notice never arrives."""
    errors = {
        field: "invalid_notify"
        for field in (CONF_OWNER, CONF_ADMIN)
        if not all(_is_notify(hass, action) for action in user_input[field])
    }
    if not errors and not user_input[CONF_OWNER] and not user_input[CONF_ADMIN]:
        errors[CONF_OWNER] = "nobody"
    return errors


def _is_notify(hass: HomeAssistant, action: str) -> bool:
    domain, _, service = action.partition(".")
    return (
        domain == "notify"
        and service not in _NOT_OFFERED
        and hass.services.has_service(domain, service)
    )


def _login_settings(user_input: Mapping[str, Any]) -> dict[str, Any]:
    return {
        CONF_OWNER: list(user_input[CONF_OWNER]),
        CONF_ADMIN: list(user_input[CONF_ADMIN]),
        CONF_DAYS: int(user_input[CONF_DAYS]),
    }


async def _async_login(
    hass: HomeAssistant, targets: list[ConfigEntry], user_input: Mapping[str, Any]
) -> ConfigSubentryData:
    """Check the chosen login and describe it. Raises ReauthLinkError when a link cannot renew it."""
    target = next((t for t in targets if t.entry_id == user_input[CONF_LOGIN]), None)
    if target is None:
        raise ReauthLinkError("login_gone", "")
    await async_check(hass, target)
    name = await async_integration_name(hass, target.domain)
    return ConfigSubentryData(
        data={
            CONF_DOMAIN: target.domain,
            CONF_ENTRY_ID: target.entry_id,
            CONF_ACCOUNT: target.unique_id,
            CONF_ACCOUNT_NAME: account_name(target, name),
            **_login_settings(user_input),
        },
        subentry_type=SUBENTRY_LOGIN,
        title=f"{name} ({account_name(target, name)})",
        unique_id=f"{target.domain}:{target.unique_id}",
    )


def _placeholders(url: str, err: ReauthLinkError | None = None) -> dict[str, str]:
    empty = {"integration": "", "min_version": MIN_HA_VERSION, "error": "", "reason": ""}
    return {**(err.placeholders if err else empty), "redirect_uri": url + CALLBACK_PATH}


class ReauthLinkConfigFlow(ConfigFlow, domain=DOMAIN):
    """The address first, then the first login."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._main: dict[str, Any] = {}

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Each login is a subentry."""
        return {SUBENTRY_LOGIN: LoginSubentryFlow}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the public address and the time of the notices."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._main = {**user_input, CONF_URL: clean_url(user_input[CONF_URL])}
            except ValueError as err:
                errors[CONF_URL] = str(err)
            else:
                return await self.async_step_login()
        return self.async_show_form(
            step_id="user", data_schema=_main_schema(self.hass, user_input or {}), errors=errors
        )

    async def async_step_login(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The first login. The others go in later, one by one."""
        if not (targets := async_targets(self.hass)):  # none yet: it can be added later
            return self._create([])
        errors: dict[str, str] = {}
        failure: ReauthLinkError | None = None
        if user_input is not None and not (errors := _login_errors(self.hass, user_input)):
            try:
                return self._create([await _async_login(self.hass, targets, user_input)])
            except ReauthLinkError as err:
                failure, errors = err, {"base": err.key}
        return self.async_show_form(
            step_id="login",
            data_schema=_login_schema(
                self.hass, user_input or {}, await _async_login_options(self.hass, targets)
            ),
            errors=errors,
            description_placeholders=_placeholders(self._main[CONF_URL], failure),
        )

    def _create(self, logins: list[ConfigSubentryData]) -> ConfigFlowResult:
        return self.async_create_entry(
            title="Reauth Link",
            data=self._main,
            subentries=logins,
            description_placeholders=_placeholders(self._main[CONF_URL]),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the address or the time."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = {**user_input, CONF_URL: clean_url(user_input[CONF_URL])}
            except ValueError as err:
                errors[CONF_URL] = str(err)
            else:
                return self.async_update_and_abort(entry, data=data)  # the listener reloads
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_main_schema(self.hass, user_input or entry.data),
            errors=errors,
            description_placeholders=_placeholders(entry.data[CONF_URL]),
        )


class LoginSubentryFlow(ConfigSubentryFlow):
    """Add a login, or change who is told and how many days it lasts."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add one of the logins not yet chosen."""
        entry = self.hass.config_entries.async_get_known_entry(self.handler[0])
        chosen = {
            target.entry_id
            for subentry in entry.subentries.values()
            if (target := async_find_target(self.hass, subentry.data)) is not None
        }
        targets = [t for t in async_targets(self.hass) if t.entry_id not in chosen]
        if not targets:
            return self.async_abort(reason="no_logins")
        errors: dict[str, str] = {}
        failure: ReauthLinkError | None = None
        if user_input is not None and not (errors := _login_errors(self.hass, user_input)):
            try:
                login = await _async_login(self.hass, targets, user_input)
            except ReauthLinkError as err:
                failure, errors = err, {"base": err.key}
            else:
                return self.async_create_entry(
                    title=login["title"], data=login["data"], unique_id=login["unique_id"]
                )
        return self.async_show_form(
            step_id="user",
            data_schema=_login_schema(
                self.hass, user_input or {}, await _async_login_options(self.hass, targets)
            ),
            errors=errors,
            description_placeholders=_placeholders(entry.data[CONF_URL], failure),
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change the settings of one login."""
        entry = self.hass.config_entries.async_get_known_entry(self.handler[0])
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None and not (errors := _login_errors(self.hass, user_input)):
            return self.async_update_and_abort(
                entry, subentry, data_updates=_login_settings(user_input)
            )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_login_schema(self.hass, user_input or subentry.data),
            errors=errors,
            description_placeholders={"login": subentry.title},
        )
