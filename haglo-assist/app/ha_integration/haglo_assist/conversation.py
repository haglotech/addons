"""Gesprächs-Agent: reicht den Satz an die App Haglo Assist weiter und spricht deren Antwort."""

import json
from pathlib import Path

import aiohttp

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import ulid

from .const import DOMAIN

CONNECTION = Path(__file__).parent / "connection.json"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    async_add_entities([HagloAgent(entry)])


class HagloAgent(conversation.ConversationEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self, entry: ConfigEntry) -> None:
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": "Haglo Assist",
                                  "manufacturer": "HAGLO", "entry_type": "service"}

    @property
    def supported_languages(self):
        return "*"

    async def async_process(self, user_input: conversation.ConversationInput) -> conversation.ConversationResult:
        cid = user_input.conversation_id or ulid.ulid_now()
        response = intent.IntentResponse(language=user_input.language)
        try:
            con = json.loads(await self.hass.async_add_executor_job(CONNECTION.read_text))
            async with async_get_clientsession(self.hass).post(
                    f"{con['url']}/api/agent/conversation",
                    json={"text": user_input.text, "conversation_id": cid, "language": user_input.language,
                          "device_id": user_input.device_id},
                    headers={"Authorization": f"Bearer {con['token']}"},
                    timeout=aiohttp.ClientTimeout(total=180)) as r:
                data = await r.json(content_type=None)
            speech = str(data.get("speech") or "Dazu habe ich keine Antwort.")
            more = bool(data.get("continue"))
        except Exception as e:  # App nicht erreichbar oder neu gestartet
            speech, more = f"Haglo Assist ist gerade nicht erreichbar ({type(e).__name__}).", False
        response.async_set_speech(speech)
        return conversation.ConversationResult(response=response, conversation_id=cid, continue_conversation=more)
