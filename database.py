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
            await conn.execute("PRAGMA synchronous = NORMAL;")
            await conn.execute("PRAGMA temp_store = MEMORY;")
            await conn.execute("PRAGMA cache_size = -64000;")
            await conn.execute("PRAGMA mmap_size = 268435456;")
            await conn.execute("PRAGMA busy_timeout = 5000;")
            yield conn

    async def init_db(self, schema_path: Optional[Path | str] = None) -> None:
        """Initialize and optimize database tables, indexes, and triggers."""
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
            try:
                await conn.execute("ALTER TABLE orders ADD COLUMN attempt_count INTEGER DEFAULT 0")
            except Exception:
                pass
            # Clean up any legacy redundant duplicate indexes using strict allowlist
            ALLOWED_LEGACY_INDEXES = frozenset({"idx_users_telegram_id", "idx_orders_order_id", "idx_orders_utr_number"})
            for legacy_idx in ALLOWED_LEGACY_INDEXES:
                try:
                    await conn.execute(f"DROP INDEX IF EXISTS {legacy_idx}")
                except Exception:
                    pass
            await conn.commit()
            await conn.execute("PRAGMA optimize;")
            await conn.commit()
        logger.info(f"Database initialized and optimized successfully at {self.db_path}")

    async def add_or_update_user(
        self,
        telegram_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None
    ) -> dict[str, Any]:
        """PERF-5: Upsert user and update last_active timestamp in a single round-trip."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO users (telegram_id, username, first_name)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_active = CURRENT_TIMESTAMP
                RETURNING id, telegram_id, username, first_name, is_blocked
                """,
                (telegram_id, username, first_name)
            )
            row = await cursor.fetchone()
            await conn.commit()
            if row:
                return dict(row)
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

    async def clear_search_history(self) -> int:
        """Clear search history table and return count of deleted records."""
        async with self._get_connection() as conn:
            cur = await conn.execute("DELETE FROM search_history")
            await conn.commit()
            return cur.rowcount

    async def get_statistics(self) -> dict[str, Any]:
        """PERF-2: Aggregate all dashboard metrics in a single combined SQL query."""
        async with self._get_connection() as conn:
            cur = await conn.execute(
                """
                SELECT
                    COUNT(*) AS total_users,
                    COALESCE(SUM(CASE WHEN date(joined_at) = date('now', 'localtime') THEN 1 ELSE 0 END), 0) AS new_users_today,
                    (SELECT COUNT(*) FROM search_history) AS total_searches,
                    (SELECT COUNT(*) FROM downloads WHERE status = 'completed') AS successful_downloads,
                    (SELECT COUNT(*) FROM downloads WHERE status = 'failed') AS failed_downloads
                FROM users
                """
            )
            row = await cur.fetchone()
            total_users = row["total_users"] if row else 0
            new_users_today = row["new_users_today"] if row else 0
            total_searches = row["total_searches"] if row else 0
            successful_downloads = row["successful_downloads"] if row else 0
            failed_downloads = row["failed_downloads"] if row else 0

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
        """MED-1: Export specified table data as CSV with strictly validated allowlist."""
        allowed_tables = frozenset({"users", "search_history", "downloads", "user_subscriptions", "orders"})
        if table_name not in allowed_tables:
            raise ValueError(f"Invalid table name: {table_name!r}")

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

    async def is_utr_already_used(self, utr_number: str, exclude_order_id: Optional[str] = None) -> bool:
        """Check if UTR has already been successfully redeemed (anti-fraud)."""
        clean_utr = utr_number.strip()
        async with self._get_connection() as conn:
            if exclude_order_id:
                cur = await conn.execute(
                    "SELECT id FROM orders WHERE utr_number = ? AND status = 'paid' AND order_id != ?",
                    (clean_utr, exclude_order_id)
                )
            else:
                cur = await conn.execute(
                    "SELECT id FROM orders WHERE utr_number = ? AND status = 'paid'",
                    (clean_utr,)
                )
            row = await cur.fetchone()
            return row is not None

    async def record_order_utr(self, order_id: str, utr_number: str) -> bool:
        """Attach UTR number to order."""
        clean_utr = utr_number.strip()
        async with self._get_connection() as conn:
            try:
                await conn.execute(
                    "UPDATE orders SET utr_number = ? WHERE order_id = ?",
                    (clean_utr, order_id)
                )
                await conn.commit()
                return True
            except Exception as e:
                logger.warning(f"Could not record UTR {clean_utr} for {order_id}: {e}")
                return False

    async def cancel_order(self, order_id: str) -> bool:
        """HIGH-5: Cleanly cancel pending order without raw connection bypass."""
        async with self._get_connection() as conn:
            cur = await conn.execute(
                "UPDATE orders SET status = 'cancelled' WHERE order_id = ? AND status = 'pending'",
                (order_id,)
            )
            await conn.commit()
            logger.info(f"Order {order_id} cancelled.")
            return cur.rowcount > 0

    async def reject_order(self, order_id: str) -> bool:
        """HIGH-5: Reject pending order without raw connection bypass."""
        async with self._get_connection() as conn:
            cur = await conn.execute(
                "UPDATE orders SET status = 'rejected' WHERE order_id = ? AND status = 'pending'",
                (order_id,)
            )
            await conn.commit()
            logger.info(f"Order {order_id} rejected by admin.")
            return cur.rowcount > 0

    async def mark_order_expired(self, order_id: str) -> bool:
        """Mark order as expired due to timeout or attempt limit exhaustion."""
        async with self._get_connection() as conn:
            cur = await conn.execute(
                "UPDATE orders SET status = 'expired' WHERE order_id = ? AND status = 'pending'",
                (order_id,)
            )
            await conn.commit()
            logger.info(f"Order {order_id} marked as expired.")
            return cur.rowcount > 0

    async def increment_order_attempts(self, order_id: str) -> int:
        """HIGH-2: Track submission attempts per order in DB."""
        async with self._get_connection() as conn:
            await conn.execute(
                "UPDATE orders SET attempt_count = COALESCE(attempt_count, 0) + 1 WHERE order_id = ?",
                (order_id,)
            )
            await conn.commit()
            cur = await conn.execute("SELECT attempt_count FROM orders WHERE order_id = ?", (order_id,))
            row = await cur.fetchone()
            return row["attempt_count"] if (row and row["attempt_count"] is not None) else 1

    async def atomic_claim_and_mark_paid(
        self, order_id: str, utr_number: str
    ) -> Tuple[bool, Optional[dict[str, Any]], str]:
        """
        CRIT-2: Atomically claim a UTR and mark order as paid inside an immediate exclusive transaction.
        Eliminates TOCTOU race conditions and double-spending across concurrent user sessions.
        Returns (success, order_dict, message).
        """
        clean_utr = utr_number.strip()
        async with self._get_connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                # 1. Check if this UTR has already been claimed by another paid order
                cur = await conn.execute(
                    "SELECT id, order_id FROM orders WHERE utr_number = ? AND status = 'paid' AND order_id != ?",
                    (clean_utr, order_id)
                )
                dup = await cur.fetchone()
                if dup:
                    await conn.rollback()
                    return False, None, "❌ Fraud Alert: This UTR has already been claimed by another transaction."

                # 2. Retrieve order
                cur = await conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,))
                order = await cur.fetchone()
                if not order:
                    await conn.rollback()
                    return False, None, "❌ Order not found."

                order_dict = dict(order)
                if order_dict.get("status") == "paid":
                    await conn.commit()
                    return True, order_dict, "✅ Order has already been paid and activated."

                if self.is_order_expired(order_dict):
                    await conn.execute("UPDATE orders SET status = 'expired' WHERE order_id = ?", (order_id,))
                    await conn.commit()
                    return False, None, "❌ Order Expired: The 10-minute validity period has elapsed."

                # 3. Atomically update status to 'paid'
                cur = await conn.execute(
                    """
                    UPDATE orders
                    SET status = 'paid', utr_number = ?, paid_at = CURRENT_TIMESTAMP
                    WHERE order_id = ? AND status = 'pending'
                    """,
                    (clean_utr, order_id)
                )
                if cur.rowcount == 0:
                    await conn.rollback()
                    return False, None, "❌ Order could not be updated (order may have expired or already processed)."

                await conn.commit()
            except Exception as e:
                try:
                    await conn.rollback()
                except Exception:
                    pass
                logger.exception(f"Error in atomic_claim_and_mark_paid for {order_id}: {e}")
                return False, None, f"Database transaction error: {e}"

        # If it's a subscription pass, activate it!
        if order_dict["order_type"].startswith("sub_"):
            plan = order_dict["order_type"].replace("sub_", "")
            await self.activate_subscription(order_dict["telegram_id"], plan)

        order_dict["status"] = "paid"
        order_dict["utr_number"] = clean_utr
        return True, order_dict, "✅ Successfully verified and activated!"

    async def mark_order_paid(self, order_id: str, utr_number: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Mark order as paid and activate subscription if order_type is a pass (Idempotent)."""
        if utr_number:
            success, order_dict, _ = await self.atomic_claim_and_mark_paid(order_id, utr_number)
            if success:
                return order_dict

        async with self._get_connection() as conn:
            cur = await conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,))
            order = await cur.fetchone()
            if not order:
                return None

            order_dict = dict(order)
            if order_dict.get("status") == "paid":
                return order_dict

            await conn.execute(
                "UPDATE orders SET status = 'paid', paid_at = CURRENT_TIMESTAMP WHERE order_id = ?",
                (order_id,)
            )
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

        async with self._get_connection() as conn:
            # Extend existing active subscription if still valid in the future
            cur = await conn.execute(
                "SELECT plan_tier, expiry_date FROM user_subscriptions WHERE telegram_id = ?",
                (telegram_id,)
            )
            existing_sub = await cur.fetchone()
            if existing_sub and existing_sub["expiry_date"]:
                try:
                    exp_clean = str(existing_sub["expiry_date"]).replace("Z", "+00:00").replace(" ", "T")
                    current_exp = datetime.fromisoformat(exp_clean)
                    if current_exp.tzinfo is None:
                        current_exp = current_exp.replace(tzinfo=timezone.utc)
                    if current_exp > now:
                        expiry_date = current_exp + timedelta(days=days)
                except Exception:
                    pass

            expiry_str = expiry_date.strftime("%Y-%m-%d %H:%M:%S")

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

    async def deactivate_all_subscriptions(self) -> int:
        """Deactivate/revoke all subscriptions for all users immediately."""
        async with self._get_connection() as conn:
            cur = await conn.execute("DELETE FROM user_subscriptions")
            count = cur.rowcount
            await conn.commit()
            logger.info(f"Deactivated all user subscriptions. Total revoked: {count}")
            return count

    async def optimize_database(self) -> None:
        """Run SQLite query optimizer and analysis for maximum execution performance."""
        async with self._get_connection() as conn:
            await conn.execute("ANALYZE;")
            await conn.execute("PRAGMA optimize;")
            await conn.commit()
            logger.info("SQLite database optimization & ANALYZE completed.")

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

    async def reset_daily_quotas(self) -> int:
        """MED-4: Background cron task to reset daily usage counters for all active subscribers."""
        from datetime import datetime, timezone
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with self._get_connection() as conn:
            cur = await conn.execute(
                "UPDATE user_subscriptions SET today_video_used = 0, last_reset_date = ? WHERE last_reset_date != ?",
                (today_str, today_str)
            )
            await conn.commit()
            if cur.rowcount > 0:
                logger.info(f"Daily Quota Reset: Reset quotas for {cur.rowcount} active subscription(s).")
            return cur.rowcount

    async def use_subscription_video_quota(self, telegram_id: int) -> bool:
        """Increment daily video usage for subscriber."""
        async with self._get_connection() as conn:
            await conn.execute(
                "UPDATE user_subscriptions SET today_video_used = today_video_used + 1 WHERE telegram_id = ?",
                (telegram_id,)
            )
            await conn.commit()
            return True

    # ------------------ Coupons ------------------

    async def create_coupon(
        self,
        code: str,
        days: int = 7,
        max_uses: int = 1,
        plan_tier: str = "go",
        expires_in_hours: Optional[int] = None,
        created_by: int = 0
    ) -> tuple[bool, dict[str, Any] | str]:
        """
        Create a new coupon code.
        Returns (success, coupon_dict_or_error_message).
        """
        import re
        from datetime import datetime, timezone, timedelta

        clean_code = (code or "").strip().upper()
        if not re.match(r"^[A-Z0-9_-]{3,30}$", clean_code):
            return False, "⚠️ Coupon code must be 3-30 alphanumeric characters (A-Z, 0-9, dashes, underscores)."

        if days <= 0:
            return False, "⚠️ Subscription days must be at least 1."
        if max_uses <= 0:
            return False, "⚠️ Max uses must be at least 1."

        plan_key = plan_tier.lower().replace("sub_", "")
        from config import SUBSCRIPTION_PLANS
        if plan_key not in SUBSCRIPTION_PLANS or plan_key == "normal":
            plan_key = "go"

        now = datetime.now(timezone.utc)
        expires_at_str = None
        if expires_in_hours and expires_in_hours > 0:
            exp_dt = now + timedelta(hours=expires_in_hours)
            expires_at_str = exp_dt.strftime("%Y-%m-%d %H:%M:%S")

        async with self._get_connection() as conn:
            cur = await conn.execute("SELECT id FROM coupons WHERE code = ?", (clean_code,))
            if await cur.fetchone():
                return False, f"⚠️ Coupon code '{clean_code}' already exists."

            await conn.execute(
                """
                INSERT INTO coupons (code, plan_tier, days, max_uses, used_count, is_active, expires_at, created_by, created_at)
                VALUES (?, ?, ?, ?, 0, 1, ?, ?, CURRENT_TIMESTAMP)
                """,
                (clean_code, plan_key, days, max_uses, expires_at_str, created_by)
            )
            await conn.commit()

            cur = await conn.execute("SELECT * FROM coupons WHERE code = ?", (clean_code,))
            row = await cur.fetchone()
            return True, dict(row)

    async def redeem_coupon(
        self,
        telegram_id: int,
        code: str
    ) -> tuple[bool, Optional[dict[str, Any]], Optional[str]]:
        """
        Redeem a coupon code for a user.
        Validates existence, active status, expiration, max usage, and per-user uniqueness.
        Returns (success, redemption_info, error_message).
        """
        from datetime import datetime, timezone

        clean_code = (code or "").strip().upper()
        if not clean_code:
            return False, None, "⚠️ Please provide a coupon code."

        now = datetime.now(timezone.utc)

        async with self._get_connection() as conn:
            cur = await conn.execute("SELECT * FROM coupons WHERE code = ?", (clean_code,))
            row = await cur.fetchone()
            if not row:
                return False, None, f"❌ Coupon code '<b>{clean_code}</b>' was not found. Please verify spelling."

            coupon = dict(row)
            coupon_id = coupon["id"]

            if not coupon["is_active"]:
                return False, None, "❌ This coupon code has been terminated or revoked by the administrator."

            # Check expiration
            if coupon.get("expires_at"):
                try:
                    exp_clean = str(coupon["expires_at"]).replace("Z", "+00:00").replace(" ", "T")
                    exp_dt = datetime.fromisoformat(exp_clean)
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                    if exp_dt < now:
                        return False, None, "⏳ This coupon code has expired."
                except Exception:
                    pass

            # Check max usage limit
            if coupon["used_count"] >= coupon["max_uses"]:
                return False, None, "⚠️ This coupon code has reached its maximum usage limit."

            # Check if this user already redeemed this specific coupon
            cur = await conn.execute(
                "SELECT id FROM coupon_redemptions WHERE coupon_id = ? AND telegram_id = ?",
                (coupon_id, telegram_id)
            )
            if await cur.fetchone():
                return False, None, "🚫 You have already redeemed this coupon code previously."

            # Record redemption and increment used_count atomically
            await conn.execute(
                "INSERT INTO coupon_redemptions (coupon_id, telegram_id) VALUES (?, ?)",
                (coupon_id, telegram_id)
            )
            await conn.execute(
                "UPDATE coupons SET used_count = used_count + 1 WHERE id = ?",
                (coupon_id,)
            )
            await conn.commit()

        # Activate or extend subscription!
        sub_result = await self.activate_subscription(
            telegram_id=telegram_id,
            plan_tier=coupon["plan_tier"],
            custom_days=coupon["days"]
        )

        return True, {
            "code": clean_code,
            "plan_tier": coupon["plan_tier"],
            "days": coupon["days"],
            "expiry_date": sub_result["expiry_date"],
            "daily_video_limit": sub_result["daily_video_limit"]
        }, None

    async def revoke_coupon(self, code: str) -> tuple[bool, str]:
        """Revoke / terminate an active coupon code immediately."""
        clean_code = (code or "").strip().upper()
        async with self._get_connection() as conn:
            cur = await conn.execute("SELECT id, is_active FROM coupons WHERE code = ?", (clean_code,))
            row = await cur.fetchone()
            if not row:
                return False, f"Coupon '{clean_code}' not found."
            if not row["is_active"]:
                return True, f"Coupon '{clean_code}' is already revoked/inactive."

            await conn.execute("UPDATE coupons SET is_active = 0 WHERE code = ?", (clean_code,))
            await conn.commit()
            return True, f"Coupon '{clean_code}' has been terminated successfully."

    async def list_coupons(self, limit: int = 20) -> list[dict[str, Any]]:
        """List recent coupons with usage stats and active status."""
        async with self._get_connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM coupons ORDER BY id DESC LIMIT ?",
                (limit,)
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    # ------------------ Referrals ------------------

    async def record_referral(
        self,
        inviter_id: int,
        new_user_id: int
    ) -> tuple[bool, int, bool]:
        """
        Record a referral when a new user starts the bot via an invite link.
        Checks:
        1. Cannot refer self.
        2. Cannot refer if new_user_id was already referred.
        3. Milestone check: Every 3 friends grants 1 week (7 days) of VIP!
        Returns (success, total_friends_invited, milestone_unlocked).
        """
        if inviter_id == new_user_id:
            return False, 0, False

        async with self._get_connection() as conn:
            # Check if this user was already referred
            cur = await conn.execute("SELECT id FROM referrals WHERE referred_id = ?", (new_user_id,))
            if await cur.fetchone():
                cur = await conn.execute("SELECT COUNT(*) as cnt FROM referrals WHERE inviter_id = ?", (inviter_id,))
                row = await cur.fetchone()
                return False, (row["cnt"] if row else 0), False

            # Check if inviter exists in database
            cur = await conn.execute("SELECT telegram_id FROM users WHERE telegram_id = ?", (inviter_id,))
            if not await cur.fetchone():
                return False, 0, False

            try:
                await conn.execute(
                    "INSERT INTO referrals (inviter_id, referred_id) VALUES (?, ?)",
                    (inviter_id, new_user_id)
                )
                await conn.commit()
            except Exception:
                return False, 0, False

            # Count total referrals
            cur = await conn.execute(
                "SELECT COUNT(*) as cnt FROM referrals WHERE inviter_id = ?",
                (inviter_id,)
            )
            row = await cur.fetchone()
            total_refs = row["cnt"] if row else 1

            # Milestone check: Every 3 referrals (3, 6, 9, 12...)
            milestone_unlocked = False
            if total_refs > 0 and total_refs % 3 == 0:
                milestone_target = total_refs
                cur = await conn.execute(
                    "SELECT id FROM referral_rewards WHERE user_id = ? AND milestone_count = ?",
                    (inviter_id, milestone_target)
                )
                if not await cur.fetchone():
                    await conn.execute(
                        "INSERT INTO referral_rewards (user_id, milestone_count, reward_days) VALUES (?, ?, 7)",
                        (inviter_id, milestone_target)
                    )
                    await conn.commit()
                    milestone_unlocked = True

        if milestone_unlocked:
            # Grant 7 days of VIP to inviter!
            await self.activate_subscription(
                telegram_id=inviter_id,
                plan_tier="go",
                custom_days=7
            )

        return True, total_refs, milestone_unlocked

    async def get_referral_stats(self, user_id: int) -> dict[str, Any]:
        """Get referral statistics, invite count, and rewards earned for a user."""
        async with self._get_connection() as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) as cnt FROM referrals WHERE inviter_id = ?",
                (user_id,)
            )
            row = await cur.fetchone()
            total_invited = row["cnt"] if row else 0

            cur = await conn.execute(
                "SELECT SUM(reward_days) as total_days FROM referral_rewards WHERE user_id = ?",
                (user_id,)
            )
            row = await cur.fetchone()
            total_days_earned = row["total_days"] if (row and row["total_days"]) else 0

        needed_for_next = 3 - (total_invited % 3)
        if needed_for_next == 0:
            needed_for_next = 3

        return {
            "total_invited": total_invited,
            "total_days_earned": total_days_earned,
            "needed_for_next": needed_for_next,
            "current_cycle_progress": total_invited % 3
        }


# Default singleton instance
db = Database()
