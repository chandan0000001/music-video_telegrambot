"""Validate callback structure before handlers index fields or perform work."""
from aiogram.types import CallbackQuery


async def parse_callback(callback: CallbackQuery, prefix: str, fields: int | tuple[int, ...]) -> list[str] | None:
    counts = (fields,) if isinstance(fields, int) else fields
    parts = callback.data.split(":") if isinstance(callback.data, str) else []
    if not parts or parts[0] != prefix or len(parts) not in counts or not all(parts):
        await callback.answer("This button is invalid. Open /start and try again.", show_alert=True)
        return None
    return parts
