"""Einrichtung ohne Eingaben – die Verbindung zur App steht in connection.json (legt die App an)."""

from homeassistant import config_entries

from .const import DOMAIN


class HagloAssistFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Haglo Assist", data={})
