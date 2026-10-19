"""Haglo Assist als Gesprächs-Agent in Home Assistant (Sprachassistenten → Konversationsagent).

Wird von der App Haglo Assist angelegt und aktuell gehalten. Jeder Satz geht an die App auf diesem Gerät;
die App entscheidet wie gewohnt: Schnellweg, Haglo Local oder Claude (nach dem gewählten Modus)."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

PLATFORMS = [Platform.CONVERSATION]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
