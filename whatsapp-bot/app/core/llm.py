"""
app/core/llm.py
---------------
Phase 6 LLM Gateway.
Uses the OpenAI SDK to convert natural language text into 
structured intent and entities using GPT-4o-mini.
"""
from __future__ import annotations

import json
from openai import AsyncOpenAI

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.llm import ExtractedIntent

logger = get_logger(__name__)

# Fallback instruction prompt to guide the LLM if it's uncertain.
_SYSTEM_INSTRUCTION = """
You are a helpful WhatsApp AI assistant. Your job is to extract the user's intent 
and relevant entities into a structured format.

Intents:
- task_add: User wants to add a new task or reminder.
  Entities: 'title' (string), 'due_date' (string, if mentioned, format ISO-8601 if possible).
- task_list: User wants to see their pending tasks.
- task_done: User wants to mark a task as completed.
- task_delete: User wants to delete all or specific tasks.
- memory_add: User wants to save a fact about themselves for later.
  Entities: 'fact' (string).
- memory_view: User wants to know what you remember about them.
- memory_forget: User wants you to forget their memories.
- help: User is asking for help or starting a conversation (e.g. 'hi', 'hello').
- unclear: You cannot determine the intent or you need more info.

If unclear, provide a 'clarification_question'.
Do not guess. Be precise.
"""

class LLMGateway:
    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.openai_api_key
        # We only instantiate the client if the API key is present
        # This allows tests to run without an API key if they mock the gateway.
        self.client = AsyncOpenAI(api_key=self.api_key) if self.api_key else None
        # Use gpt-4o-mini as default, since it supports structured outputs natively via beta.chat.completions.parse
        self.model_name = "gpt-4o-mini"

    async def extract_intent(self, text: str) -> ExtractedIntent:
        """
        Extracts structured intent from raw text.
        """
        if not self.client:
            logger.warning("No openai_api_key provided. Using string-matching fallback for tests.")
            text_lower = text.lower().strip()
            
            if text_lower in ("help", "/help", "hi", "hello", "hey", "start"):
                return ExtractedIntent(intent="help", entities={})
            elif text_lower.startswith("task add "):
                return ExtractedIntent(intent="task_add", entities={"title": text[9:]})
            elif text_lower == "task list":
                return ExtractedIntent(intent="task_list", entities={})
            elif text_lower.startswith("task done"):
                return ExtractedIntent(intent="task_done", entities={})
            elif text_lower == "task delete":
                return ExtractedIntent(intent="task_delete", entities={})
            elif text_lower.startswith("memory add "):
                return ExtractedIntent(intent="memory_add", entities={"fact": text[11:]})
            elif text_lower == "memory view":
                return ExtractedIntent(intent="memory_view", entities={})
            elif text_lower == "memory forget":
                return ExtractedIntent(intent="memory_forget", entities={})
            
            return ExtractedIntent(
                intent="unclear",
                entities={},
                clarification_question="I'm currently running without an LLM. Please use strict commands like 'task add' or 'memory add'."
            )
            
        logger.info(f"Extracting intent from text: {text}")
        
        try:
            completion = await self.client.beta.chat.completions.parse(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": _SYSTEM_INSTRUCTION},
                    {"role": "user", "content": text},
                ],
                response_format=ExtractedIntent,
                temperature=0.0,
            )
            
            # The structured output is available under `parsed`
            if completion.choices[0].message.parsed:
                return completion.choices[0].message.parsed
            
            raise ValueError("Parsed output was None")
            
        except Exception as e:
            logger.error("Failed to extract intent from LLM", exc_info=e)
            return ExtractedIntent(
                intent="unclear",
                entities={},
                clarification_question="Sorry, I'm having trouble understanding right now. Could you rephrase that?"
            )
