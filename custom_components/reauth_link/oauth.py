"""Talks to the target integration's own reauthentication flow.

Nothing here reads a token: the target integration exchanges the code and stores the result, as it always does.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager, suppress
import logging
import secrets

from yarl import URL

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry
from homeassistant.core import DOMAIN as HOMEASSISTANT_DOMAIN, HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType, InvalidData, UnknownFlow
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.config_entry_oauth2_flow import (
    LocalOAuth2Implementation,
    LocalOAuth2ImplementationWithPkce,
    async_get_implementations,
)
from homeassistant.loader import IntegrationNotFound, async_get_integration

from .const import DOMAIN, MIN_HA_VERSION

_LOGGER = logging.getLogger(__name__)


class ReauthLinkError(HomeAssistantError):
    """A login this integration cannot renew, with the reason to show to the user."""

    def __init__(self, key: str, integration: str, **placeholders: str) -> None:
        """Keep the key: the config flow uses it as the form error."""
        self.key = key
        self.placeholders = {
            "integration": integration,
            "min_version": MIN_HA_VERSION,
            "error": "",
            "reason": "",
            **placeholders,
        }
        super().__init__(
            translation_domain=DOMAIN,
            translation_key=key,
            translation_placeholders=self.placeholders,
        )


async def async_integration_name(hass: HomeAssistant, domain: str) -> str:
    """The integration's name, as Home Assistant shows it."""
    try:
        return (await async_get_integration(hass, domain)).name
    except IntegrationNotFound:
        return domain


def account_name(target: ConfigEntry, integration: str) -> str:
    """The account, without repeating the integration (some titles start with it)."""
    return target.title.removeprefix(integration).strip() or target.title


@callback
def async_close_flow(hass: HomeAssistant, flow_id: str) -> None:
    """Never leave a flow hanging."""
    with suppress(UnknownFlow):
        hass.config_entries.flow.async_abort(flow_id)


@callback
def async_requests(hass: HomeAssistant, target: ConfigEntry) -> list[str]:
    """The sign-in requests of this login that are not ours."""
    return [
        flow["flow_id"]
        for flow in target.async_get_active_flows(hass, {SOURCE_REAUTH})
        if DOMAIN not in flow["context"]
    ]


@contextmanager
def keep_reauth_issue(hass: HomeAssistant, target: ConfigEntry) -> Generator[None]:
    """Home Assistant drops its "sign in again" repair when any reauth flow of the entry ends, ours included.

    Put it back while the request it stands for is still open.
    """
    registry = ir.async_get(hass)
    issue_id = f"config_entry_reauth_{target.domain}_{target.entry_id}"
    issue = registry.async_get_issue(HOMEASSISTANT_DOMAIN, issue_id)
    try:
        yield
    finally:
        if (
            issue is not None
            and registry.async_get_issue(HOMEASSISTANT_DOMAIN, issue_id) is None
            and async_requests(hass, target)
        ):
            owner = getattr(issue, "config_entry_id", None)  # newer than the minimum version
            ir.async_create_issue(
                hass,
                HOMEASSISTANT_DOMAIN,
                issue_id,
                data=issue.data,
                is_fixable=bool(issue.is_fixable),
                issue_domain=issue.issue_domain,
                severity=issue.severity or ir.IssueSeverity.ERROR,
                translation_key=issue.translation_key or "config_entry_reauth",
                translation_placeholders=issue.translation_placeholders,
                **({"config_entry_id": owner} if owner else {}),
            )
            if issue.dismissed_version:
                ir.async_ignore_issue(hass, HOMEASSISTANT_DOMAIN, issue_id, True)


async def _async_check_implementation(
    hass: HomeAssistant, target: ConfigEntry, name: str
) -> None:
    """Refuse up front what could never work, with a reason the user can act on."""
    if "my" not in hass.config.components:
        # without it, Home Assistant only builds the link inside a browser request
        raise ReauthLinkError("my_missing", name)
    try:
        implementations = await async_get_implementations(hass, target.domain)
    except HomeAssistantError as err:
        raise ReauthLinkError("missing_credentials", name) from err
    implementation = implementations.get(target.data.get("auth_implementation", ""))
    if implementation is None:
        raise ReauthLinkError("missing_credentials", name)
    if isinstance(implementation, LocalOAuth2ImplementationWithPkce):
        raise ReauthLinkError("pkce", name)
    if not isinstance(implementation, LocalOAuth2Implementation):
        # e.g. an account linked through Home Assistant Cloud: the redirect is not ours to change
        raise ReauthLinkError("unsupported", name)


async def async_open_flow(hass: HomeAssistant, target: ConfigEntry) -> tuple[str, str]:
    """Start a reauthentication and take it to the external step: (flow_id, url)."""
    name = await async_integration_name(hass, target.domain)
    await _async_check_implementation(hass, target, name)
    flows = hass.config_entries.flow
    marker = secrets.token_hex(8)  # tells our flow from one Home Assistant opened
    try:
        result = await flows.async_init(
            target.domain,
            context={
                "source": SOURCE_REAUTH,
                "entry_id": target.entry_id,
                "unique_id": target.unique_id,
                "title_placeholders": {"name": target.title},
                DOMAIN: marker,  # type: ignore[typeddict-unknown-key]
            },
            data=dict(target.data),
        )
        for _ in range(3):  # some integrations show a "Submit" form first
            if result["type"] is not FlowResultType.FORM:
                break
            result = await flows.async_configure(result["flow_id"], {})
    except Exception as err:
        # a step that raises leaves its flow behind
        for flow in list(target.async_get_active_flows(hass, {SOURCE_REAUTH})):
            if flow["context"].get(DOMAIN) == marker:
                async_close_flow(hass, flow["flow_id"])
        if isinstance(err, InvalidData):  # the form asks for something we do not have
            raise ReauthLinkError("unsupported", name) from err
        # only the type: the message may carry request parameters
        _LOGGER.error(
            "Reauthentication of %s failed inside Home Assistant: %s", target.domain, type(err).__name__
        )
        raise ReauthLinkError("flow_failed", name, error=type(err).__name__) from None
    if result["type"] is FlowResultType.ABORT:
        raise ReauthLinkError("flow_aborted", name, reason=str(result.get("reason")))
    query = URL(result.get("url") or "").query
    if "code_challenge" in query:  # the verifier would die with the flow
        error = ReauthLinkError("pkce", name)
    elif result["type"] is not FlowResultType.EXTERNAL_STEP or not (
        {"redirect_uri", "state"} <= set(query) and query.get("response_type") == "code"
    ):
        error = ReauthLinkError("unsupported", name)
    else:
        return result["flow_id"], result["url"]
    async_close_flow(hass, result["flow_id"])
    raise error


async def async_authorize_url(hass: HomeAssistant, target: ConfigEntry) -> str:
    """The authorization URL the target integration builds, leaving nothing open."""
    with keep_reauth_issue(hass, target):
        flow_id, url = await async_open_flow(hass, target)
        async_close_flow(hass, flow_id)
    return url


async def async_check(hass: HomeAssistant, target: ConfigEntry) -> None:
    """Can this login be renewed by link? Raises the reason when it cannot."""
    if not target.unique_id:  # Home Assistant would take any account for a different one
        raise ReauthLinkError("no_account", await async_integration_name(hass, target.domain))
    await async_authorize_url(hass, target)
