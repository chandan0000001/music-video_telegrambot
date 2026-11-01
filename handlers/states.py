"""
FSM (Finite State Machine) States for Interactive User and Admin Flows.
"""
from aiogram.fsm.state import State, StatesGroup


class UserStates(StatesGroup):
    waiting_for_search_query = State()
    waiting_for_audio_link = State()
    waiting_for_video_link = State()
    waiting_for_ytmusic = State()
    waiting_for_twitter = State()
    waiting_for_utr = State()


class AdminStates(StatesGroup):
    waiting_for_broadcast_text = State()
    waiting_for_user_lookup_id = State()
