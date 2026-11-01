"""
Database Layer for Telegram Music & Video Downloader Bot.
Uses aiosqlite for non-blocking asynchronous database operations.
"""
import io
import csv
import logging
from pathlib import Path
from typing import Any, Optional, AsyncIterator
from contextlib import asynccontextmanager
import aiosqlite

from config import DATABASE_PATH, BASE_DIR

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: Optional[Path | str] = None):
        self.db_path = Path(db_path or DATABASE_PATH)

    @asynccontextmanager
    async def _get_connection(self) -> AsyncIterator[aiosqlite.Connection]:
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys = ON;")
            await conn.execute("PRAGMA journal_mode = WAL;")
            yield conn

    async def init_db(self, schema_path: Optional[Path | str] = None) -> None:
        """Initialize database tables, indexes, and triggers."""
        schema_file = Path(schema_path or (BASE_DIR / "database" / "schema.sql"))
        if not schema_file.exists():
            raise FileNotFoundError(f"Schema file not found at {schema_file}")

        async with self._get_connection() as conn:
            with open(schema_file, "r", encoding="utf-8") as f:
                schema_sql = f.read()
            await conn.executescript(schema_sql)
            try:
                await conn.execute("ALTER TABLE orders ADD COLUMN expires_at TIMESTAMP")
            except Exception:
                pass
            await conn.commit()
        logger.info(f"Database initialized successfully at {self.db_path}")

    async def add_or_update_user(
        self,
        telegram_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None
    ) -> dict[str, Any]:
        """Upsert user and update last_active timestamp."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                "SELECT id, telegram_id, username, first_name, is_blocked FROM users WHERE telegram_id = ?",
                (telegram_id,)
            )
            existing = await cursor.fetchone()

            if existing:
                await conn.execute(
                    """
                    UPDATE users
                    SET username = ?, first_name = ?, last_active = CURRENT_TIMESTAMP
                    WHERE telegram_id = ?
                    """,
                    (username, first_name, telegram_id)
                )
                await conn.commit()
                return dict(existing)
            else:
                await conn.execute(
                    """
                    INSERT INTO users (telegram_id, username, first_name)
                    VALUES (?, ?, ?)
                    """,
                    (telegram_id, username, first_name)
                )
                await conn.execute(
                    "UPDATE bot_statistics SET total_users = total_users + 1, updated_at = CURRENT_TIMESTAMP WHERE id = 1"
                )
                await conn.commit()
                return {
                    "telegram_id": telegram_id,
                    "username": username,
                    "first_name": first_name,
                    "is_blocked": 0
                }

    async def get_user(self, telegram_id: int) -> Optional[dict[str, Any]]:
        """Retrieve user details by Telegram ID."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM users WHERE telegram_id = ?",
                (telegram_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def is_user_blocked(self, telegram_id: int) -> bool:
        """Check if user is marked as blocked."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                "SELECT is_blocked FROM users WHERE telegram_id = ?",
                (telegram_id,)
            )
            row = await cursor.fetchone()
            if row:
                return bool(row["is_blocked"])
            return False

    async def set_user_blocked(self, telegram_id: int, is_blocked: bool) -> bool:
        """Block or unblock a user. Returns True if user found and updated."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                "UPDATE users SET is_blocked = ? WHERE telegram_id = ?",
                (1 if is_blocked else 0, telegram_id)
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def record_search(self, telegram_id: int, search_query: str, source: str = "youtube") -> int:
        """Record a search query in history and increment statistics."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO search_history (telegram_id, search_query, source)
                VALUES (?, ?, ?)
                """,
                (telegram_id, search_query, source)
            )
            await conn.execute(
                "UPDATE bot_statistics SET total_searches = total_searches + 1, updated_at = CURRENT_TIMESTAMP WHERE id = 1"
            )
            await conn.commit()
            return cursor.lastrowid or 0

    async def record_download(
        self,
        telegram_id: int,
        media_title: str,
        source: str,
        media_type: str,
        selected_quality: str,
        selected_format: str,
        status: str = "pending"
    ) -> int:
        """Create a new download record with 'pending' status."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO downloads (
                    telegram_id, media_title, source, media_type,
                    selected_quality, selected_format, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (telegram_id, media_title, source, media_type, selected_quality, selected_format, status)
            )
            await conn.commit()
            return cursor.lastrowid or 0

    async def update_download_status(self, download_id: int, status: str) -> None:
        """Update status of a download ('completed', 'failed', etc.)."""
        async with self._get_connection() as conn:
            await conn.execute(
                "UPDATE downloads SET status = ? WHERE id = ?",
                (status, download_id)
            )
            if status == "completed":
                await conn.execute(
                    "UPDATE bot_statistics SET total_successful_downloads = total_successful_downloads + 1, updated_at = CURRENT_TIMESTAMP WHERE id = 1"
                )
            elif status == "failed":
                await conn.execute(
                    "UPDATE bot_statistics SET total_failed_downloads = total_failed_downloads + 1, updated_at = CURRENT_TIMESTAMP WHERE id = 1"
                )
            await conn.commit()

    async def get_user_search_history(self, telegram_id: int, limit: int = 10) -> list[dict[str, Any]]:
        """Retrieve recent searches for a specific user."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT id, search_query, source, searched_at
                FROM search_history
                WHERE telegram_id = ?
                ORDER BY searched_at DESC, id DESC
                LIMIT ?
                """,
                (telegram_id, limit)
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_user_downloads(self, telegram_id: int, limit: int = 10) -> list[dict[str, Any]]:
        """Retrieve recent downloads for a specific user."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT id, media_title, source, media_type, selected_quality, selected_format, status, created_at
                FROM downloads
                WHERE telegram_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (telegram_id, limit)
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_all_users(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """Retrieve registered users for admin listing."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT id, telegram_id, username, first_name, joined_at, last_active, is_blocked
                FROM users
                ORDER BY joined_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset)
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_recent_searches(self, limit: int = 50) -> list[dict[str, Any]]:
        """Retrieve recent searches across all users for admin review."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT s.id, s.telegram_id, u.username, s.search_query, s.source, s.searched_at
                FROM search_history s
                LEFT JOIN users u ON s.telegram_id = u.telegram_id
                ORDER BY s.searched_at DESC
                LIMIT ?
                """,
                (limit,)
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_recent_downloads(self, limit: int = 50) -> list[dict[str, Any]]:
        """Retrieve recent downloads across all users for admin review."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT d.id, d.telegram_id, u.username, d.media_title, d.media_type,
                       d.selected_quality, d.selected_format, d.status, d.created_at
                FROM downloads d
                LEFT JOIN users u ON d.telegram_id = u.telegram_id
                ORDER BY d.created_at DESC
                LIMIT ?
                """,
                (limit,)
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_top_searches(self, limit: int = 5) -> list[dict[str, Any]]:
        """Retrieve most searched queries."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT search_query, COUNT(*) as count
                FROM search_history
                GROUP BY LOWER(search_query)
                ORDER BY count DESC
                LIMIT ?
                """,
                (limit,)
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_statistics(self) -> dict[str, Any]:
        """Aggregate all dashboard metrics."""
        async with self._get_connection() as conn:
            # Total users
            cur = await conn.execute("SELECT COUNT(*) as count FROM users")
            total_users = (await cur.fetchone())["count"]

            # New users today
            cur = await conn.execute(
                "SELECT COUNT(*) as count FROM users WHERE date(joined_at) = date('now', 'localtime')"
            )
            new_users_today = (await cur.fetchone())["count"]

            # Total searches
            cur = await conn.execute("SELECT COUNT(*) as count FROM search_history")
            total_searches = (await cur.fetchone())["count"]

            # Total successful downloads
            cur = await conn.execute("SELECT COUNT(*) as count FROM downloads WHERE status = 'completed'")
            successful_downloads = (await cur.fetchone())["count"]

            # Total failed downloads
            cur = await conn.execute("SELECT COUNT(*) as count FROM downloads WHERE status = 'failed'")
            failed_downloads = (await cur.fetchone())["count"]

            # Most searched songs
            top_searches = await self.get_top_searches(limit=5)

            # Recently joined users
            cur = await conn.execute(
                "SELECT telegram_id, username, first_name, joined_at FROM users ORDER BY joined_at DESC LIMIT 5"
            )
            recent_users = [dict(r) for r in await cur.fetchall()]

            return {
                "total_users": total_users,
                "new_users_today": new_users_today,
                "total_searches": total_searches,
                "successful_downloads": successful_downloads,
                "failed_downloads": failed_downloads,
                "top_searches": top_searches,
                "recent_users": recent_users
            }

    async def export_table_to_csv(self, table_name: str) -> str:
        """Export specified table data as a CSV formatted string."""
        if table_name not in {"users", "search_history", "downloads", "user_subscriptions", "orders"}:
            raise ValueError(f"Invalid table name: {table_name}")

        async with self._get_connection() as conn:
            cursor = await conn.execute(f"SELECT * FROM {table_name}")
            rows = await cursor.fetchall()
            if not rows:
                return ""

            output = io.StringIO()
            writer = csv.writer(output)
            # Column headers
            headers = [col[0] for col in cursor.description]
            writer.writerow(headers)
            # Data rows
            for row in rows:
                writer.writerow(list(row))

            return output.getvalue()

    # ------------------ Orders & Payments ------------------

    async def create_order(
        self,
        telegram_id: int,
        order_type: str,
        amount_inr: float,
        media_id: Optional[str] = None,
        media_title: Optional[str] = None,
        quality: Optional[str] = None
    ) -> str:
        """Create new pending order with 10-minute validity and return unique order_id."""
        import time
        import uuid
        from datetime import datetime, timezone, timedelta
        from config import ORDER_EXPIRY_MINUTES

        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(minutes=ORDER_EXPIRY_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
        order_id = f"ORD_{int(time.time() * 1000)}_{telegram_id}_{uuid.uuid4().hex[:6].upper()}"

        async with self._get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO orders (order_id, telegram_id, order_type, amount_inr, status, media_id, media_title, quality, expires_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                """,
                (order_id, telegram_id, order_type, amount_inr, media_id, media_title, quality, expires_at)
            )
            await conn.commit()
        return order_id

    async def get_order(self, order_id: str) -> Optional[dict[str, Any]]:
        """Retrieve order record by order_id."""
        async with self._get_connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM orders WHERE order_id = ?",
                (order_id,)
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    def is_order_expired(self, order: dict[str, Any]) -> bool:
        """Check if order has exceeded its 10-minute validity."""
        from datetime import datetime, timezone
        from config import ORDER_EXPIRY_MINUTES

        expires_at_str = order.get("expires_at")
        if not expires_at_str:
            try:
                created_str = order.get("created_at", "")
                created_dt = datetime.fromisoformat(created_str.replace(" ", "T")).replace(tzinfo=timezone.utc)
                return (datetime.now(timezone.utc) - created_dt).total_seconds() > (ORDER_EXPIRY_MINUTES * 60)
            except Exception:
                return False

        try:
            exp_dt = datetime.fromisoformat(expires_at_str.replace(" ", "T")).replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) > exp_dt
        except Exception:
            return False

    async def is_utr_already_used(self, utr_number: str) -> bool:
        """Check if UTR has already been successfully redeemed (anti-fraud)."""
        async with self._get_connection() as conn:
            cur = await conn.execute(
                "SELECT id FROM orders WHERE utr_number = ? AND status = 'paid'",
                (utr_number,)
            )
            row = await cur.fetchone()
            return row is not None

    async def record_order_utr(self, order_id: str, utr_number: str) -> bool:
        """Attach UTR number to order."""
        async with self._get_connection() as conn:
            await conn.execute(
                "UPDATE orders SET utr_number = ? WHERE order_id = ?",
                (utr_number, order_id)
            )
            await conn.commit()
            return True

    async def mark_order_paid(self, order_id: str, utr_number: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Mark order as paid and activate subscription if order_type is a pass."""
        async with self._get_connection() as conn:
            cur = await conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,))
            order = await cur.fetchone()
            if not order:
                return None

            order_dict = dict(order)
            update_sql = "UPDATE orders SET status = 'paid', paid_at = CURRENT_TIMESTAMP"
            params = []
            if utr_number:
                update_sql += ", utr_number = ?"
                params.append(utr_number)
            update_sql += " WHERE order_id = ?"
            params.append(order_id)

            await conn.execute(update_sql, tuple(params))
            await conn.commit()

        # If it's a subscription pass, activate it!
        if order_dict["order_type"].startswith("sub_"):
            plan = order_dict["order_type"].replace("sub_", "")
            await self.activate_subscription(order_dict["telegram_id"], plan)

        order_dict["status"] = "paid"
        return order_dict

    # ------------------ Subscriptions ------------------

    async def activate_subscription(
        self,
        telegram_id: int,
        plan_tier: str,
        custom_days: Optional[int] = None
    ) -> dict[str, Any]:
        """Activate or extend user subscription (Go, Pro, Ultra, Max)."""
        from datetime import datetime, timezone, timedelta
        from config import SUBSCRIPTION_PLANS

        now = datetime.now(timezone.utc)
        today_str = now.strftime("%Y-%m-%d")

        plan_key = plan_tier.lower().replace("sub_", "")
        plan_info = SUBSCRIPTION_PLANS.get(plan_key, SUBSCRIPTION_PLANS["go"])

        days = custom_days if (custom_days and custom_days > 0) else plan_info["days"]
        daily_limit = plan_info["daily_limit"]
        expiry_date = now + timedelta(days=days)
        expiry_str = expiry_date.strftime("%Y-%m-%d %H:%M:%S")

        async with self._get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO user_subscriptions (telegram_id, plan_tier, start_date, expiry_date, daily_video_limit, today_video_used, last_reset_date)
                VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, 0, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    plan_tier = excluded.plan_tier,
                    expiry_date = excluded.expiry_date,
                    daily_video_limit = excluded.daily_video_limit,
                    today_video_used = 0,
                    last_reset_date = excluded.last_reset_date
                """,
                (telegram_id, plan_key, expiry_str, daily_limit, today_str)
            )
            await conn.commit()

        return {
            "telegram_id": telegram_id,
            "plan_tier": plan_key,
            "expiry_date": expiry_str,
            "daily_video_limit": daily_limit,
            "days": days
        }

    async def deactivate_subscription(self, telegram_id: int) -> bool:
        """Deactivate/revoke user subscription immediately."""
        async with self._get_connection() as conn:
            await conn.execute(
                "DELETE FROM user_subscriptions WHERE telegram_id = ?",
                (telegram_id,)
            )
            await conn.commit()
            return True
            await conn.commit()

        return {
            "telegram_id": telegram_id,
            "plan_tier": plan_tier,
            "expiry_date": expiry_str,
            "daily_video_limit": daily_limit
        }

    async def get_user_subscription(self, telegram_id: int) -> Optional[dict[str, Any]]:
        """Get user subscription status, auto-resetting daily count if midnight passed."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        today_str = now.strftime("%Y-%m-%d")

        async with self._get_connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM user_subscriptions WHERE telegram_id = ?",
                (telegram_id,)
            )
            row = await cur.fetchone()
            if not row:
                return None

            sub = dict(row)
            try:
                exp_dt = datetime.fromisoformat(sub["expiry_date"].replace(" ", "T")).replace(tzinfo=timezone.utc)
            except Exception:
                exp_dt = now

            if exp_dt < now:
                return {
                    "is_active": False,
                    "plan_tier": sub["plan_tier"],
                    "expired": True,
                    "today_remaining": 0
                }

            if sub["last_reset_date"] != today_str:
                await conn.execute(
                    "UPDATE user_subscriptions SET today_video_used = 0, last_reset_date = ? WHERE telegram_id = ?",
                    (today_str, telegram_id)
                )
                await conn.commit()
                sub["today_video_used"] = 0
                sub["last_reset_date"] = today_str

            remaining = max(0, sub["daily_video_limit"] - sub["today_video_used"])
            return {
                "is_active": True,
                "plan_tier": sub["plan_tier"],
                "daily_limit": sub["daily_video_limit"],
                "today_used": sub["today_video_used"],
                "today_remaining": remaining,
                "expiry_date": sub["expiry_date"],
                "expired": False
            }

    async def use_subscription_video_quota(self, telegram_id: int) -> bool:
        """Increment daily video usage for subscriber."""
        async with self._get_connection() as conn:
            await conn.execute(
                "UPDATE user_subscriptions SET today_video_used = today_video_used + 1 WHERE telegram_id = ?",
                (telegram_id,)
            )
            await conn.commit()
            return True


# Default singleton instance
db = Database()
