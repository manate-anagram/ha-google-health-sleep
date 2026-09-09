"""Config flow for Google Health integration."""
import logging
from typing import Any

from homeassistant.helpers import config_entry_oauth2_flow

import voluptuous as vol

from .const import DOMAIN, SCOPES


class OAuth2FlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Config flow to handle Google Health API OAuth2 authentication."""

    DOMAIN = DOMAIN

    def __init__(self) -> None:
        """Initialize the flow handler."""
        super().__init__()
        self.oauth_data: dict[str, Any] = {}

    @property
    def logger(self) -> logging.Logger:
        """Return logger."""
        return logging.getLogger(__name__)

    @property
    def extra_authorize_data(self) -> dict[str, str]:
        """Extra data to append to the authorize URL."""
        return {
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
        }

    async def async_oauth_create_entry_setup(self, data: dict[str, Any]) -> dict[str, Any]:
        """Proceed to ask for a custom name after OAuth completes."""
        self.oauth_data = data
        return await self.async_step_name()

    async def async_step_name(self, user_input: dict[str, Any] | None = None) -> dict[str, Any]:
        """Form step to ask user for a custom name."""
        if user_input is not None:
            return self.async_create_entry(
                title=user_input["name"],
                data=self.oauth_data,
            )

        return self.async_show_form(
            step_id="name",
            data_schema=vol.Schema({
                vol.Required("name", default="My"): str
            }),
        )
