"""
Administrative Control Panel and Monitoring Handlers
Restricted strictly to configured ADMIN_IDS.
Uses HTML parse mode with escaping to avoid entity parsing issues.
"""
import io
import html
import asyncio
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from config import ADMIN_IDS
from database import db
from handlers.states import AdminStates

logger = logging.getLogger(__name__)
router = Router(name="admin_router")


def is_admin(user_id: int) -> bool:
    """Check if Telegram user ID is an authorized administrator."""
    return user_id in ADMIN_IDS


def get_admin_dashboard_keyboard() -> InlineKeyboardMarkup:
    """Generate admin dashboard action buttons."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Full Statistics", callback_data="admin:stats"),
                InlineKeyboardButton(text="👥 View Users", callback_data="admin:users")
            ],
            [
                InlineKeyboardButton(text="🔍 Search Logs", callback_data="admin:searchlogs"),
                InlineKeyboardButton(text="📦 Download Logs", callback_data="admin:downloadlogs")
            ],
            [
                InlineKeyboardButton(text="📢 Broadcast Message", callback_data="admin:broadcast"),
                InlineKeyboardButton(text="🔎 Lookup User", callback_data="admin:lookup")
            ],
            [
                InlineKeyboardButton(text="💎 Plan Subscribers", callback_data="admin:plan_subscribers"),
                InlineKeyboardButton(text="💳 Payment Orders", callback_data="admin:payment_orders")
            ],
            [
                InlineKeyboardButton(text="🎟️ Manage Coupons", callback_data="admin:coupons")
            ],
            [
                InlineKeyboardButton(text="🔙 Main Menu", callback_data="menu:main")
            ],
            [
                InlineKeyboardButton(text="📥 CSV Users", callback_data="admin:export:users"),
                InlineKeyboardButton(text="📥 CSV Searches", callback_data="admin:export:search_history"),
                InlineKeyboardButton(text="📥 CSV Downloads", callback_data="admin:export:downloads")
            ]
        ]
    )


async def render_admin_dashboard_text() -> str:
    """Compile aggregated administrative metrics safely formatted in HTML."""
    stats = await db.get_statistics()
    top_searches_str = (
        "\n".join([f"  • <code>{html.escape(s['search_query'])}</code> ({s['count']}x)" for s in stats["top_searches"]])
        if stats["top_searches"] else "  <i>None yet</i>"
    )

    recent_users_str = (
        "\n".join([
            f"  • <code>{u['telegram_id']}</code> (@{html.escape(u.get('username') or 'N/A')}) - {html.escape(u.get('first_name') or '')}"
            for u in stats["recent_users"]
        ])
        if stats["recent_users"] else "  <i>None yet</i>"
    )

    from utils.disk_cleaner import get_disk_usage_stats
    from config import get_cookie_file, INSTAGRAM_SESSION_ID, YTDLP_PROXY

    disk = get_disk_usage_stats()
    cookie_f = get_cookie_file()
    yt_cookie_status = f"✅ Active ({cookie_f.stat().st_size / 1024:.1f} KB)" if (cookie_f and cookie_f.exists()) else "⚪ None"
    ig_cookie_status = "✅ Active" if INSTAGRAM_SESSION_ID else "⚪ None"
    proxy_status = "✅ Connected (WARP)" if YTDLP_PROXY else "⚪ Direct"

    return (
        "🛡️ <b>Administrator Control Dashboard</b>\n\n"
        f"👥 <b>Total Registered Users:</b> <code>{stats['total_users']}</code>\n"
        f"🆕 <b>New Users Today:</b> <code>{stats['new_users_today']}</code>\n"
        f"🔍 <b>Total Searches Performed:</b> <code>{stats['total_searches']}</code>\n"
        f"✅ <b>Successful Downloads:</b> <code>{stats['successful_downloads']}</code>\n"
        f"❌ <b>Failed Downloads:</b> <code>{stats['failed_downloads']}</code>\n\n"
        "🖥️ <b>Server & Storage Diagnostics:</b>\n"
        f"  • <b>Disk Storage:</b> <code>{disk['free_gb']:.1f} GB / {disk['total_gb']:.1f} GB Free ({disk['free_percent']:.0f}%)</code>\n"
        f"  • <b>YouTube Cookies:</b> <code>{yt_cookie_status}</code>\n"
        f"  • <b>Instagram Session:</b> <code>{ig_cookie_status}</code>\n"
        f"  • <b>Proxy / WARP:</b> <code>{proxy_status}</code>\n\n"
        f"🔥 <b>Top Searched Songs:</b>\n{top_searches_str}\n\n"
        f"👤 <b>Recently Joined Users:</b>\n{recent_users_str}\n\n"
        "⚡ <i>Use the buttons below or commands for detailed operations.</i>"
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    """Admin dashboard command."""
    user = message.from_user
    if not user or not is_admin(user.id):
        await message.answer("⛔ <b>Access Denied:</b> You are not authorized to use admin features.", parse_mode="HTML")
        return

    await state.clear()
    dashboard_text = await render_admin_dashboard_text()
    await message.answer(
        text=dashboard_text,
        reply_markup=get_admin_dashboard_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "admin:dashboard")
async def cb_admin_dashboard(callback: CallbackQuery, state: FSMContext) -> None:
    """Return to admin dashboard."""
    user = callback.from_user
    if not user or not is_admin(user.id):
        await callback.answer("Access Denied.", show_alert=True)
        return

    await state.clear()
    try:
        dashboard_text = await render_admin_dashboard_text()
        if callback.message:
            try:
                await callback.message.edit_text(
                    text=dashboard_text,
                    reply_markup=get_admin_dashboard_keyboard(),
                    parse_mode="HTML"
                )
            except Exception:
                await callback.message.answer(
                    text=dashboard_text,
                    reply_markup=get_admin_dashboard_keyboard(),
                    parse_mode="HTML"
                )
    finally:
        await callback.answer()


@router.message(Command("stats"))
@router.callback_query(F.data == "admin:stats")
async def cmd_or_cb_stats(event: Message | CallbackQuery) -> None:
    """Display real-time statistics."""
    user = event.from_user
    if not user or not is_admin(user.id):
        if isinstance(event, CallbackQuery):
            await event.answer("Access Denied.", show_alert=True)
        else:
            await event.answer("⛔ Access Denied.")
        return

    text = await render_admin_dashboard_text()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="admin:dashboard")]]
    )

    if isinstance(event, CallbackQuery):
        if event.message:
            await event.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.message(Command("users"))
@router.callback_query(F.data == "admin:users")
async def cmd_or_cb_users(event: Message | CallbackQuery) -> None:
    """Display list of registered users."""
    user = event.from_user
    if not user or not is_admin(user.id):
        if isinstance(event, CallbackQuery):
            await event.answer("Access Denied.", show_alert=True)
        else:
            await event.answer("⛔ Access Denied.")
        return

    users_list = await db.get_all_users(limit=25, offset=0)
    lines = ["👥 <b>Registered Users (Recent 25):</b>\n"]
    for u in users_list:
        status = "🚫 Blocked" if u["is_blocked"] else "✅ Active"
        username_str = f"@{html.escape(u['username'])}" if u.get("username") else "No username"
        first_name_str = html.escape(u.get("first_name") or "")
        joined = u.get("joined_at", "").split(".")[0]
        lines.append(
            f"• ID: <code>{u['telegram_id']}</code> | {username_str} ({first_name_str})\n"
            f"  └ Status: {status} | Joined: <i>{joined}</i>"
        )

    if not users_list:
        lines.append("<i>No users registered yet.</i>")

    lines.append("\n💡 <i>To inspect or block:</i>\n<code>/user &lt;telegram_id&gt;</code> | <code>/block &lt;id&gt;</code> | <code>/unblock &lt;id&gt;</code>")
    text = "\n".join(lines)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="admin:dashboard")]]
    )

    if isinstance(event, CallbackQuery):
        await event.answer()
        if event.message:
            try:
                await event.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            except Exception:
                pass
    else:
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.message(Command("searchlogs"))
@router.callback_query(F.data == "admin:searchlogs")
async def cmd_or_cb_searchlogs(event: Message | CallbackQuery) -> None:
    """Display recent search queries."""
    user = event.from_user
    if not user or not is_admin(user.id):
        if isinstance(event, CallbackQuery):
            await event.answer("Access Denied.", show_alert=True)
        else:
            await event.answer("⛔ Access Denied.")
        return

    if isinstance(event, CallbackQuery):
        await event.answer()

    logs = await db.get_recent_searches(limit=20)
    lines = ["🔍 <b>Recent Search Logs (Last 20):</b>\n"]
    for item in logs:
        time_str = item.get("searched_at", "").split(".")[0]
        uname = f"@{html.escape(item['username'])}" if item.get("username") else f"ID:<code>{item['telegram_id']}</code>"
        q_str = html.escape(item.get("search_query", ""))
        lines.append(f"• <code>{q_str}</code> by {uname} at <i>{time_str}</i>")

    if not logs:
        lines.append("<i>No searches recorded.</i>")

    text = "\n".join(lines)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="admin:dashboard")]]
    )

    if isinstance(event, CallbackQuery):
        if event.message:
            try:
                await event.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            except Exception:
                pass
    else:
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.message(Command("downloadlogs"))
@router.callback_query(F.data == "admin:downloadlogs")
async def cmd_or_cb_downloadlogs(event: Message | CallbackQuery) -> None:
    """Display recent download activities."""
    user = event.from_user
    if not user or not is_admin(user.id):
        if isinstance(event, CallbackQuery):
            await event.answer("Access Denied.", show_alert=True)
        else:
            await event.answer("⛔ Access Denied.")
        return

    if isinstance(event, CallbackQuery):
        await event.answer()

    logs = await db.get_recent_downloads(limit=20)
    lines = ["📦 <b>Recent Download Logs (Last 20):</b>\n"]
    for item in logs:
        time_str = item.get("created_at", "").split(".")[0]
        uname = f"@{html.escape(item['username'])}" if item.get("username") else f"ID:<code>{item['telegram_id']}</code>"
        status_icon = "✅" if item["status"] == "completed" else ("❌" if item["status"] == "failed" else "⏳")
        title_str = html.escape(item.get("media_title", "Unknown"))
        lines.append(
            f"• {status_icon} <b>{title_str}</b>\n"
            f"  └ By {uname} | {item['media_type']} ({item['selected_format']} {item['selected_quality']}) | <i>{time_str}</i>"
        )

    if not logs:
        lines.append("<i>No download activity recorded.</i>")

    text = "\n".join(lines)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="admin:dashboard")]]
    )

    if isinstance(event, CallbackQuery):
        if event.message:
            try:
                await event.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            except Exception:
                pass
    else:
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.message(Command("user"))
async def cmd_lookup_user(message: Message) -> None:
    """Inspect user profile, search logs, and download records by Telegram ID."""
    user = message.from_user
    if not user or not is_admin(user.id):
        await message.answer("⛔ Access Denied.")
        return

    args = message.text.partition(" ")[2].strip() if message.text else ""
    if not args or not args.isdigit():
        await message.answer("💡 <b>Usage:</b> <code>/user &lt;telegram_id&gt;</code>", parse_mode="HTML")
        return

    target_id = int(args)
    target_user = await db.get_user(target_id)
    if not target_user:
        await message.answer(f"❌ User with Telegram ID <code>{target_id}</code> was not found.", parse_mode="HTML")
        return

    searches = await db.get_user_search_history(target_id, limit=5)
    downloads = await db.get_user_downloads(target_id, limit=5)

    status_str = "🚫 Blocked" if target_user["is_blocked"] else "✅ Active"
    u_name = html.escape(target_user.get("username") or "N/A")
    f_name = html.escape(target_user.get("first_name") or "")
    lines = [
        f"👤 <b>User Profile: <code>{target_id}</code></b>\n",
        f"• <b>Username:</b> @{u_name}",
        f"• <b>First Name:</b> {f_name}",
        f"• <b>Status:</b> {status_str}",
        f"• <b>Joined:</b> <code>{target_user.get('joined_at')}</code>",
        f"• <b>Last Active:</b> <code>{target_user.get('last_active')}</code>\n",
        "🔍 <b>Recent Searches:</b>"
    ]

    if searches:
        for s in searches:
            q_clean = html.escape(s.get("search_query", ""))
            lines.append(f"  • <code>{q_clean}</code> ({s['searched_at']})")
    else:
        lines.append("  <i>No searches</i>")

    lines.append("\n📦 <b>Recent Downloads:</b>")
    if downloads:
        for d in downloads:
            st = "✅" if d["status"] == "completed" else "❌"
            dt_clean = html.escape(d.get("media_title", ""))
            lines.append(f"  • {st} {dt_clean} [{d['media_type']} - {d['selected_quality']}]")
    else:
        lines.append("  <i>No downloads</i>")

    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("block"))
async def cmd_block_user(message: Message) -> None:
    """Block a user by Telegram ID."""
    user = message.from_user
    if not user or not is_admin(user.id):
        await message.answer("⛔ Access Denied.")
        return

    args = message.text.partition(" ")[2].strip() if message.text else ""
    if not args or not args.isdigit():
        await message.answer("💡 <b>Usage:</b> <code>/block &lt;telegram_id&gt;</code>", parse_mode="HTML")
        return

    target_id = int(args)
    if target_id in ADMIN_IDS:
        await message.answer("❌ You cannot block an administrator.")
        return

    success = await db.set_user_blocked(target_id, is_blocked=True)
    if success:
        await message.answer(f"🚫 User <code>{target_id}</code> has been <b>blocked</b>.", parse_mode="HTML")
    else:
        await message.answer(f"❌ User <code>{target_id}</code> not found in database.", parse_mode="HTML")


@router.message(Command("unblock"))
async def cmd_unblock_user(message: Message) -> None:
    """Unblock a user by Telegram ID."""
    user = message.from_user
    if not user or not is_admin(user.id):
        await message.answer("⛔ Access Denied.")
        return

    args = message.text.partition(" ")[2].strip() if message.text else ""
    if not args or not args.isdigit():
        await message.answer("💡 <b>Usage:</b> <code>/unblock &lt;telegram_id&gt;</code>", parse_mode="HTML")
        return

    target_id = int(args)
    success = await db.set_user_blocked(target_id, is_blocked=False)
    if success:
        await message.answer(f"✅ User <code>{target_id}</code> has been <b>unblocked</b>.", parse_mode="HTML")
    else:
        await message.answer(f"❌ User <code>{target_id}</code> not found in database.", parse_mode="HTML")


@router.callback_query(F.data == "admin:lookup")
async def cb_admin_lookup(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt admin for Telegram ID lookup."""
    user = callback.from_user
    if not user or not is_admin(user.id):
        await callback.answer("Access Denied.", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_user_lookup_id)
    if callback.message:
        await callback.message.answer(
            "🔎 <b>Please enter the Telegram ID of the user you want to lookup:</b>",
            parse_mode="HTML"
        )
    await callback.answer()


@router.message(AdminStates.waiting_for_user_lookup_id)
async def process_user_lookup_id(message: Message, state: FSMContext) -> None:
    """Process user ID sent in response to lookup prompt."""
    await state.clear()
    user = message.from_user
    if not user or not is_admin(user.id):
        return

    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("❌ Invalid ID. Please send numbers only.")
        return

    # Delegate to lookup
    message.text = f"/user {text}"
    await cmd_lookup_user(message)


@router.callback_query(F.data == "admin:broadcast")
async def cb_admin_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt admin to enter broadcast text."""
    user = callback.from_user
    if not user or not is_admin(user.id):
        await callback.answer("Access Denied.", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_broadcast_text)
    if callback.message:
        await callback.message.answer(
            "📢 <b>Broadcast Mode</b>\n\n"
            "Please send the message you wish to broadcast to all registered users.\n\n"
            "Type <code>/admin</code> to cancel.",
            parse_mode="HTML"
        )
    await callback.answer()


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    """Broadcast via command parameter: /broadcast <message>."""
    user = message.from_user
    if not user or not is_admin(user.id):
        await message.answer("⛔ Access Denied.")
        return

    broadcast_content = message.text.partition(" ")[2].strip() if message.text else ""
    if not broadcast_content:
        await state.set_state(AdminStates.waiting_for_broadcast_text)
        await message.answer(
            "📢 <i>Please send the message text you wish to broadcast to all users:</i>",
            parse_mode="HTML"
        )
        return

    await execute_broadcast(message, broadcast_content, state)


@router.message(AdminStates.waiting_for_broadcast_text)
async def process_broadcast_text(message: Message, state: FSMContext) -> None:
    """Handle broadcast message content from admin."""
    content = (message.text or "").strip()
    await execute_broadcast(message, content, state)


async def execute_broadcast(message: Message, content: str, state: FSMContext) -> None:
    """Deliver broadcast message to all non-blocked registered users."""
    await state.clear()
    user = message.from_user
    if not user or not is_admin(user.id):
        return

    status_msg = await message.answer("📢 <i>Initiating broadcast...</i>", parse_mode="HTML")

    users_list = await db.get_all_users(limit=10000, offset=0)
    sent_count = 0
    fail_count = 0

    escaped_content = html.escape(content)

    for u in users_list:
        if u.get("is_blocked"):
            continue
        try:
            await message.bot.send_message(
                chat_id=u["telegram_id"],
                text=f"📢 <b>Announcement from Admin:</b>\n\n{escaped_content}",
                parse_mode="HTML"
            )
            sent_count += 1
            await asyncio.sleep(0.05)  # Respect Telegram flood limits
        except Exception as e:
            fail_count += 1
            logger.warning(f"Broadcast failed for user {u['telegram_id']}: {e}")

    await status_msg.edit_text(
        f"✅ <b>Broadcast Completed!</b>\n\n"
        f"• Successfully delivered: <code>{sent_count}</code>\n"
        f"• Failed/Blocked bot: <code>{fail_count}</code>\n"
        f"• Total reached: <code>{sent_count + fail_count}</code>",
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("admin:export:"))
async def cb_export_csv(callback: CallbackQuery) -> None:
    """Generate and send CSV export of specified database table."""
    user = callback.from_user
    if not user or not is_admin(user.id):
        await callback.answer("Access Denied.", show_alert=True)
        return

    table_name = callback.data.split(":")[2]
    await callback.answer("Generating CSV export...")

    try:
        csv_data = await db.export_table_to_csv(table_name)
        if not csv_data:
            await callback.message.answer(f"⚠️ Table <code>{table_name}</code> is currently empty.", parse_mode="HTML")
            return

        csv_bytes = csv_data.encode("utf-8")
        document = BufferedInputFile(csv_bytes, filename=f"{table_name}_export.csv")

        await callback.message.answer_document(
            document=document,
            caption=f"📁 <b>Export for table:</b> <code>{table_name}</code>",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.exception(f"Failed exporting CSV for table {table_name}: {e}")
        await callback.message.answer(f"❌ Export failed: {e}")


@router.message(Command("subscribers"))
@router.callback_query(F.data == "admin:subscriptions")
@router.callback_query(F.data == "admin:plan_subscribers")
async def cmd_or_cb_plan_subscribers(event: Message | CallbackQuery) -> None:
    """View all subscribers and their respective plans with username and user ID."""
    user = event.from_user
    if not user or not is_admin(user.id):
        if isinstance(event, CallbackQuery):
            await event.answer("Access Denied.", show_alert=True)
        else:
            await event.answer("⛔ Access Denied.")
        return

    if isinstance(event, CallbackQuery):
        await event.answer()

    async with db._get_connection() as conn:
        cur = await conn.execute(
            """
            SELECT 
                s.telegram_id, 
                s.plan_tier, 
                s.daily_video_limit, 
                s.today_video_used, 
                s.start_date,
                s.expiry_date, 
                u.username, 
                u.first_name,
                CASE 
                    WHEN datetime(s.expiry_date) > datetime('now') THEN 1 
                    ELSE 0 
                END as is_active
            FROM user_subscriptions s
            LEFT JOIN users u ON s.telegram_id = u.telegram_id
            ORDER BY 
                CASE s.plan_tier
                    WHEN 'max' THEN 1
                    WHEN 'ultra' THEN 2
                    WHEN 'pro' THEN 3
                    WHEN 'go' THEN 4
                    WHEN 'weekly' THEN 4
                    ELSE 5
                END,
                s.expiry_date DESC
            LIMIT 30
            """
        )
        subscribers = [dict(r) for r in await cur.fetchall()]

        # Count active users per plan
        cur = await conn.execute(
            """
            SELECT plan_tier, COUNT(*) as count 
            FROM user_subscriptions 
            WHERE datetime(expiry_date) > datetime('now')
            GROUP BY plan_tier
            """
        )
        counts = {row["plan_tier"]: row["count"] for row in await cur.fetchall()}

    tier_badges = {
        "max": "👑 MAX VIP",
        "ultra": "⚡ ULTRA PASS",
        "pro": "🚀 PRO PASS",
        "go": "⚡ GO PASS",
        "weekly": "⚡ GO PASS",
        "normal": "🟢 NORMAL (Free)"
    }

    lines = [
        "💎 <b>Plan Subscribers Directory &amp; Breakdown</b>\n",
        "📊 <b>Active Subscribers per Plan:</b>",
        f"• 👑 Max VIP: <b>{counts.get('max', 0)}</b> user(s)",
        f"• ⚡ Ultra Pass: <b>{counts.get('ultra', 0)}</b> user(s)",
        f"• 🚀 Pro Pass: <b>{counts.get('pro', 0)}</b> user(s)",
        f"• ⚡ Go Pass: <b>{counts.get('go', 0) + counts.get('weekly', 0)}</b> user(s)\n",
        "👥 <b>Subscribers List (Username &amp; User ID):</b>"
    ]

    displayed_subs = subscribers[:12]
    if displayed_subs:
        for idx, sub in enumerate(displayed_subs, start=1):
            uname = f"@{html.escape(sub['username'])}" if sub.get("username") else "<i>No username</i>"
            name = html.escape(sub.get("first_name") or "User")
            plan_raw = (sub.get("plan_tier") or "normal").lower()
            plan_name = tier_badges.get(plan_raw, plan_raw.upper())
            status = "🟢 <b>ACTIVE</b>" if sub.get("is_active") else "🔴 <b>EXPIRED</b>"
            raw_exp = sub.get("expiry_date") or ""
            exp = raw_exp.split(".")[0] if raw_exp else "Never"
            lines.append(
                f"{idx}. 👤 <b>{name}</b> ({uname})\n"
                f"   ├ 🆔 <b>User ID:</b> <code>{sub['telegram_id']}</code>\n"
                f"   ├ 💎 <b>Plan:</b> <b>{plan_name}</b>\n"
                f"   ├ ⚡ <b>Daily Quota:</b> <code>{sub['today_video_used']}/{sub['daily_video_limit']}</code> used\n"
                f"   ├ ⏰ <b>Expires:</b> <code>{exp}</code>\n"
                f"   └ 📊 <b>Status:</b> {status}\n"
            )
        if len(subscribers) > 12:
            lines.append(f"<i>...and {len(subscribers) - 12} more. Click CSV Subscriptions for full data.</i>\n")
    else:
        lines.append("<i>No active or recorded subscriptions found.</i>\n")

    lines.append("<b>Admin Commands:</b>")
    lines.append("• <code>/sub_activate &lt;user_id&gt; &lt;plan&gt; [days]</code>")
    lines.append("• <code>/sub_deactivate &lt;user_id&gt;</code>")
    lines.append("• <code>/sub_deactivate_all</code>")
    lines.append("• <code>/sub_status &lt;user_id&gt;</code>")

    text = "\n".join(lines)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Refresh", callback_data="admin:plan_subscribers"),
                InlineKeyboardButton(text="💳 Payment Orders", callback_data="admin:payment_orders")
            ],
            [
                InlineKeyboardButton(text="📥 CSV Subscriptions", callback_data="admin:export:user_subscriptions"),
                InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="admin:dashboard")
            ]
        ]
    )

    if isinstance(event, CallbackQuery):
        if event.message:
            try:
                await event.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            except Exception as e:
                if "message is not modified" in str(e).lower():
                    pass
                else:
                    logger.warning(f"edit_text failed in plan_subscribers, replying with answer: {e}")
                    try:
                        await event.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
                    except Exception:
                        pass
    else:
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.message(Command("orders"))
@router.callback_query(F.data == "admin:payment_orders")
async def cmd_or_cb_payment_orders(event: Message | CallbackQuery) -> None:
    """View recent payment orders with user IDs, usernames, and plans."""
    user = event.from_user
    if not user or not is_admin(user.id):
        if isinstance(event, CallbackQuery):
            await event.answer("Access Denied.", show_alert=True)
        else:
            await event.answer("⛔ Access Denied.")
        return

    if isinstance(event, CallbackQuery):
        await event.answer()

    async with db._get_connection() as conn:
        cur = await conn.execute(
            """
            SELECT 
                o.order_id,
                o.telegram_id,
                o.order_type,
                o.amount_inr,
                o.quality,
                o.media_title,
                o.utr_number,
                o.status,
                o.paid_at,
                o.created_at,
                u.username,
                u.first_name
            FROM orders o
            LEFT JOIN users u ON o.telegram_id = u.telegram_id
            WHERE o.status = 'paid'
            ORDER BY o.id DESC
            LIMIT 15
            """
        )
        orders = [dict(r) for r in await cur.fetchall()]

    lines = [
        "💳 <b>Recent Completed Payment Orders</b>\n",
        "Showing last 15 verified transactions:\n"
    ]

    if orders:
        for idx, o in enumerate(orders, start=1):
            uname = f"@{html.escape(o['username'])}" if o.get("username") else "<i>No username</i>"
            name = html.escape(o.get("first_name") or "User")
            utr = o.get("utr_number") or "N/A"
            paid_time = (o.get("paid_at") or o.get("created_at") or "").split(".")[0]
            
            # Format order type description
            otype = o.get("order_type", "")
            if otype.startswith("sub_"):
                plan_str = f"Pass: {otype.replace('sub_', '').upper()}"
            elif o.get("quality"):
                plan_str = f"Single {o['quality']} HD"
            else:
                plan_str = otype.upper()

            lines.append(
                f"{idx}. 🧾 <code>{o['order_id']}</code>\n"
                f"   ├ 👤 <b>{name}</b> ({uname})\n"
                f"   ├ 🆔 <b>User ID:</b> <code>{o['telegram_id']}</code>\n"
                f"   ├ 💎 <b>Plan / Item:</b> <b>{plan_str}</b> (₹{o['amount_inr']:.2f})\n"
                f"   ├ 🏦 <b>UTR:</b> <code>{utr}</code>\n"
                f"   └ 📅 <b>Date:</b> <code>{paid_time}</code>\n"
            )
    else:
        lines.append("<i>No completed paid orders found yet.</i>")

    text = "\n".join(lines)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Refresh", callback_data="admin:payment_orders"),
                InlineKeyboardButton(text="💎 Plan Subscribers", callback_data="admin:plan_subscribers")
            ],
            [
                InlineKeyboardButton(text="📥 CSV Orders", callback_data="admin:export:orders"),
                InlineKeyboardButton(text="🔙 Back to Dashboard", callback_data="admin:dashboard")
            ]
        ]
    )

    if isinstance(event, CallbackQuery):
        if event.message:
            try:
                await event.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            except Exception as e:
                if "message is not modified" in str(e).lower():
                    pass
                else:
                    logger.warning(f"edit_text failed in payment_orders, replying with answer: {e}")
                    try:
                        await event.message.answer(text, reply_markup=keyboard, parse_mode="HTML")
                    except Exception:
                        pass
    else:
        await event.answer(text, reply_markup=keyboard, parse_mode="HTML")


@router.message(Command("sub_activate"))
async def cmd_admin_activate_sub(message: Message) -> None:
    """Admin command to grant or override a subscription: /sub_activate <user_id> <plan> [days]"""
    user = message.from_user
    if not user or not is_admin(user.id):
        await message.answer("🚫 Access Denied.")
        return

    args = (message.text or "").split()[1:]
    if len(args) < 2:
        await message.answer(
            "📖 <b>Usage:</b> <code>/sub_activate &lt;user_id&gt; &lt;plan&gt; [days]</code>\n\n"
            "• <b>Plans:</b> <code>go</code>, <code>pro</code>, <code>ultra</code>, <code>max</code>\n"
            "• <i>Examples:</i>\n"
            "  <code>/sub_activate 123456789 pro</code>\n"
            "  <code>/sub_activate 123456789 max 365</code>",
            parse_mode="HTML"
        )
        return

    try:
        target_uid = int(args[0])
    except ValueError:
        await message.answer("❌ Invalid User ID. Must be numeric Telegram ID.")
        return

    plan_key = args[1].lower()
    custom_days = int(args[2]) if len(args) > 2 and args[2].isdigit() else None

    await db.add_or_update_user(target_uid, username=None, first_name=None)
    result = await db.activate_subscription(target_uid, plan_key, custom_days)

    await message.answer(
        f"✅ <b>Subscription Activated!</b>\n\n"
        f"• <b>User ID:</b> <code>{target_uid}</code>\n"
        f"• <b>Plan:</b> <b>{result['plan_tier'].capitalize()} Pass</b>\n"
        f"• <b>Daily HD Quota:</b> <code>{result['daily_video_limit']} videos/day</code>\n"
        f"• <b>Valid Duration:</b> <code>{result['days']} days</code>\n"
        f"• <b>Expires At:</b> <code>{result['expiry_date']} UTC</code>",
        parse_mode="HTML"
    )

    try:
        await message.bot.send_message(
            chat_id=target_uid,
            text=(
                f"🎉 <b>Subscription Activated by Administrator!</b>\n\n"
                f"You have been upgraded to the <b>{result['plan_tier'].capitalize()} Pass</b>!\n"
                f"• Daily HD Video Quota: <b>{result['daily_video_limit']} videos/day</b>\n"
                f"• Valid Until: <code>{result['expiry_date']} UTC</code>\n\n"
                f"Enjoy unlimited high-speed downloads!"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.warning(f"Could not alert user {target_uid}: {e}")


@router.message(Command("sub_deactivate"))
@router.message(Command("sub_revoke"))
async def cmd_admin_deactivate_sub(message: Message) -> None:
    """Admin command to revoke a subscription: /sub_deactivate <user_id>"""
    user = message.from_user
    if not user or not is_admin(user.id):
        await message.answer("🚫 Access Denied.")
        return

    args = (message.text or "").split()[1:]
    if not args:
        await message.answer("📖 <b>Usage:</b> <code>/sub_deactivate &lt;user_id&gt;</code>", parse_mode="HTML")
        return

    try:
        target_uid = int(args[0])
    except ValueError:
        await message.answer("❌ Invalid User ID.")
        return

    await db.deactivate_subscription(target_uid)
    await message.answer(
        f"✅ <b>Subscription Revoked!</b>\n"
        f"User <code>{target_uid}</code> has been returned to the Normal (Free) tier.",
        parse_mode="HTML"
    )

    try:
        await message.bot.send_message(
            chat_id=target_uid,
            text="⚠️ Your premium subscription has been ended by the administrator.",
            parse_mode="HTML"
        )
    except Exception:
        pass


@router.message(Command("sub_deactivate_all"))
@router.message(Command("sub_revoke_all"))
async def cmd_admin_deactivate_all_subs(message: Message) -> None:
    """Admin command to revoke all user subscriptions: /sub_deactivate_all"""
    user = message.from_user
    if not user or not is_admin(user.id):
        await message.answer("🚫 Access Denied.")
        return

    count = await db.deactivate_all_subscriptions()
    await message.answer(
        f"✅ <b>All Subscriptions Revoked!</b>\n\n"
        f"• Total users reset to Normal (Free) tier: <code>{count}</code>",
        parse_mode="HTML"
    )


@router.message(Command("sub_status"))
async def cmd_admin_sub_status(message: Message) -> None:
    """Admin command to check user's subscription: /sub_status <user_id>"""
    user = message.from_user
    if not user or not is_admin(user.id):
        await message.answer("🚫 Access Denied.")
        return

    args = (message.text or "").split()[1:]
    if not args:
        await message.answer("📖 <b>Usage:</b> <code>/sub_status &lt;user_id&gt;</code>", parse_mode="HTML")
        return

    try:
        target_uid = int(args[0])
    except ValueError:
        await message.answer("❌ Invalid User ID.")
        return

    sub = await db.get_user_subscription(target_uid)
    if not sub or not sub.get("is_active"):
        await message.answer(
            f"ℹ️ User <code>{target_uid}</code> has <b>no active subscription</b> (Normal Free Tier).",
            parse_mode="HTML"
        )
        return

    await message.answer(
        f"👑 <b>Subscription Status for User <code>{target_uid}</code></b>\n\n"
        f"• <b>Tier:</b> <code>{sub['plan_tier'].capitalize()} Pass</code>\n"
        f"• <b>Daily HD Limit:</b> <code>{sub['daily_limit']} videos/day</code>\n"
        f"• <b>Used Today:</b> <code>{sub['today_used']} videos</code>\n"
        f"• <b>Remaining Today:</b> <code>{sub['today_remaining']} videos</code>\n"
        f"• <b>Expires At:</b> <code>{sub['expiry_date']} UTC</code>\n"
        f"• <b>Status:</b> 🟢 Active",
        parse_mode="HTML"
    )
