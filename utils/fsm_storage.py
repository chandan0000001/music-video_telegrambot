"""
SQLite-backed FSM Storage for aiogram 3.
Persists user conversational states (e.g., UPI UTR submission, coupon input, admin broadcast)
across bot restarts using the shared Database layer.
"""
import json
import logging
from typing import Any, Dict, Optional

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey, StateType

from database import Database, db as default_db

logger = logging.getLogger(__name__)


class SQLiteStorage(BaseStorage):
    """Persistent aiogram FSM storage backed by SQLite."""

    def __init__(self, database: Database = default_db):
        self.db = database

    @staticmethod
    def _make_key(key: StorageKey) -> str:
        thread = f":{key.thread_id}" if key.thread_id else ""
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.destiny}{thread}"

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        state_str: Optional[str] = state.state if isinstance(state, State) else state
        await self.db.fsm_set_state(self._make_key(key), state_str)

    async def get_state(self, key: StorageKey) -> Optional[str]:
        return await self.db.fsm_get_state(self._make_key(key))

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        serialized = json.dumps(data or {}, ensure_ascii=False)
        await self.db.fsm_set_data(self._make_key(key), serialized)

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        raw = await self.db.fsm_get_data(self._make_key(key))
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception as e:
            logger.warning(f"Failed to decode persisted FSM data: {e}")
            return {}

    async def close(self) -> None:
        return None
