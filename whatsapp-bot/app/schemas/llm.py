"""
app/schemas/llm.py
------------------
Structured Pydantic schemas for LLM Gateway (Phase 6).
These enforce strict JSON parsing of the LLM output.
"""
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

class IntentType(str, Enum):
    task_add = "task_add"
    task_list = "task_list"
    task_done = "task_done"
    task_delete = "task_delete"
    memory_add = "memory_add"
    memory_view = "memory_view"
    memory_forget = "memory_forget"
    help = "help"
    unclear = "unclear"

class ExtractedIntent(BaseModel):
    intent: IntentType = Field(
        description="The primary intent classified from the user's message."
    )
    entities: Dict[str, Any] = Field(
        default_factory=dict,
        description="Parameters extracted from the text (e.g., 'title', 'due_date' for tasks; 'fact' for memory)."
    )
    clarification_question: Optional[str] = Field(
        default=None,
        description="If the intent is unclear or ambiguous, ask a concise clarifying question here."
    )
