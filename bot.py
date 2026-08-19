#!/usr/bin/env python3
"""
JakBall 24h Score Tracker - Telegram Bot (v2 - Simplified)
Only three core features: Match List, Match Scores, Match Reminders.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
import sqlite3

# ----------------------------- Configuration -----------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
DB_PATH = "bot_data.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ----------------------------- Database Setup -----------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            match_id TEXT NOT NULL,
            match_home TEXT,
            match_away TEXT,
            kickoff_time TEXT NOT NULL,
            offset_minutes INTEGER NOT NULL,
            note TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # Notes table is no longer needed but kept for backward compatibility
    conn.commit()
    conn.close()

def db_execute(query, params=(), fetch_one=False, fetch_all=False):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(query, params)
    conn.commit()
    result = None
    if fetch_one:
        result = c.fetchone()
    elif fetch_all:
        result = c.fetchall()
    conn.close()
    return result

# ----------------------------- Mock Match Data -----------------------------
def get_today_matches():
    """Return all matches for today (with no scores)."""
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    matches = [
        {"id": "match_1", "home_team": "Arsenal",    "away_team": "Chelsea",      "kickoff_utc": today + timedelta(hours=14, minutes=30)},
        {"id": "match_2", "home_team": "Barcelona",  "away_team": "Real Madrid",  "kickoff_utc": today + timedelta(hours=17, minutes=0)},
        {"id": "match_3", "home_team": "Bayern",     "away_team": "Dortmund",     "kickoff_utc": today + timedelta(hours=20, minutes=0)},
    ]
    now = datetime.now(timezone.utc)
    return [m for m in matches if m["kickoff_utc"] > now]

def get_match_scores():
    """Return matches with mock scores (including past/live matches)."""
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    now = datetime.now(timezone.utc)
    matches = [
        {"id": "past_1", "home_team": "Liverpool", "away_team": "Man Utd", "kickoff_utc": today + timedelta(hours=11, minutes=0), "score": "3 - 1", "status": "FT"},
        {"id": "past_2", "home_team": "Inter",     "away_team": "Juventus", "kickoff_utc": today + timedelta(hours=12, minutes=30), "score": "2 - 2", "status": "FT"},
        {"id": "live_1", "home_team": "Arsenal",   "away_team": "Chelsea",  "kickoff_utc": today + timedelta(hours=14, minutes=30), "score": "1 - 0", "status": "LIVE 67'"},
        {"id": "upc_1",  "home_team": "Barcelona", "away_team": "Real Madrid", "kickoff_utc": today + timedelta(hours=17, minutes=0), "score": "TBD", "status": "Upcoming"},
    ]
    return matches

def get_match_by_id(match_id: str) -> Optional[Dict]:
    all_matches = get_today_matches() + get_match_scores()
    for m in all_matches:
        if m["id"] == match_id:
            return m
    return None

# ----------------------------- Reminder Scheduling -----------------------------
async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    data = job.data
    chat_id = data["chat_id"]
    home = data["home_team"]
    away = data["away_team"]
    note = data.get("note", "")
    offset = data["offset_minutes"]
    text = f"⏰ **Reminder!**\n\n⚽ {home} vs {away}\n📅 Kicks off in {offset} minutes!"
    if note:
        text += f"\n📝 Your note: _{note}_"
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")

def schedule_reminder_job(application: Application, reminder_row):
    kickoff = datetime.fromisoformat(reminder_row["kickoff_time"])
    remind_at = kickoff - timedelta(minutes=reminder_row["offset_minutes"])
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if remind_at <= now:
        return
    delay = (remind_at - now).total_seconds()
    job_name = f"reminder_{reminder_row['id']}"
    for j in application.job_queue.get_jobs_by_name(job_name):
        j.schedule_removal()
    application.job_queue.run_once(
        send_reminder,
        when=delay,
        name=job_name,
        data={
            "chat_id": reminder_row["chat_id"],
            "home_team": reminder_row["match_home"],
            "away_team": reminder_row["match_away"],
            "offset_minutes": reminder_row["offset_minutes"],
            "note": reminder_row["note"],
        },
    )

async def reschedule_all_reminders(application: Application):
    rows = db_execute("SELECT * FROM reminders", fetch_all=True)
    for row in rows:
        schedule_reminder_job(application, row)

# ----------------------------- Keyboards & Messages -----------------------------
WELCOME_MESSAGE = (
    "⚽ **Welcome to JakBall24h Score Tracker**\n\n"
    "Follow football matches with simple tools directly in Telegram.\n\n"
    "📋 **Match List**\n"
    "View available football matches.\n\n"
    "📊 **Match Scores**\n"
    "Check available score information for matches.\n\n"
    "🔔 **Match Reminders**\n"
    "Set a reminder for a selected match.\n\n"
    "**Quick guide:** Choose an option below, select a match when required, and follow the instructions.\n\n"
    "Choose a tool to get started."
)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📋 Match List"],
        ["📊 Match Scores"],
        ["🔔 Match Reminders"],
    ],
    resize_keyboard=True,
)

HELP_TEXT = (
    "🆘 **Help**\n\n"
    "/start – Restart and see the welcome message\n"
    "/help  – Show this help\n"
    "Use the buttons below to navigate!"
)

# ----------------------------- Match Display Helpers -----------------------------
async def send_matches_list(update: Update, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Display today's upcoming matches (no scores) with inline buttons for details."""
    matches = get_today_matches()
    if not matches:
        await context.bot.send_message(chat_id=chat_id, text="No matches scheduled for today.")
        return
    lines = ["📋 **Today's Match List**\n"]
    for m in matches:
        kickoff = m["kickoff_utc"].strftime("%H:%M UTC")
        lines.append(f"{m['home_team']} vs {m['away_team']} – {kickoff}")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{m['home_team']} vs {m['away_team']}", callback_data=f"match_info|{m['id']}")]
        for m in matches
    ])
    await context.bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        reply_markup=keyboard,
        parse_mode="Markdown",
    )

async def send_match_scores(update: Update, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Display match scores for today (past, live, upcoming)."""
    matches = get_match_scores()
    if not matches:
        await context.bot.send_message(chat_id=chat_id, text="No score information available yet.")
        return
    lines = ["⚽ **Today's Scores**\n"]
    for m in matches:
        status = m["status"]
        score = m["score"]
        if status == "FT":
            lines.append(f"{m['home_team']} {score} {m['away_team']}  (FT)")
        elif status.startswith("LIVE"):
            lines.append(f"{m['home_team']} {score} {m['away_team']}  (LIVE)")
        else:
            lines.append(f"{m['home_team']} vs {m['away_team']} – {score}")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("« Back to menu", callback_data="menu_back")]
    ])
    await context.bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        reply_markup=keyboard,
        parse_mode="Markdown",
    )

async def send_match_detail(update: Update, match_id: str, context: ContextTypes.DEFAULT_TYPE):
    """Show match detail with a 'Set Reminder' button."""
    match = get_match_by_id(match_id)
    if not match:
        await update.callback_query.answer("Match not found.", show_alert=True)
        return
    kickoff = match["kickoff_utc"].strftime("%d %b %Y, %H:%M UTC")
    text = (
        f"⚽ *{match['home_team']}* vs *{match['away_team']}*\n"
        f"📅 {kickoff}\n"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏰ Set Reminder", callback_data=f"reminder_select|{match_id}")],
        [InlineKeyboardButton("« Back to matches", callback_data="matches_list")],
    ])
    await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)

# ----------------------------- Reminder Conversation -----------------------------
REMINDER_OFFSET, CONFIRM_REMINDER = range(2)

async def reminder_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    match_id = query.data.split("|")[1]
    match = get_match_by_id(match_id)
    if not match:
        await query.edit_message_text("Sorry, match not available anymore.")
        return ConversationHandler.END
    context.user_data["reminder_match"] = match
    text = (
        f"⏰ Set reminder for *{match['home_team']}* vs *{match['away_team']}*\n"
        "How many minutes before kick‑off should I notify you?"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("15 min", callback_data="offset_15"),
         InlineKeyboardButton("30 min", callback_data="offset_30"),
         InlineKeyboardButton("60 min", callback_data="offset_60")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_reminder")],
    ])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    return REMINDER_OFFSET

async def reminder_offset_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data
    if choice == "cancel_reminder":
        await query.edit_message_text("Reminder cancelled.")
        return ConversationHandler.END
    offset_min = int(choice.split("_")[1])
    context.user_data["reminder_offset"] = offset_min
    match = context.user_data["reminder_match"]
    text = (
        f"🔔 Reminder for *{match['home_team']}* vs *{match['away_team']}* "
        f"will fire {offset_min} minutes before kick‑off.\n\n"
        "Tap *Confirm* to set the reminder, or *Cancel*."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm", callback_data="confirm_reminder"),
         InlineKeyboardButton("❌ Cancel", callback_data="cancel_reminder")]
    ])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    return CONFIRM_REMINDER

async def reminder_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel_reminder":
        await query.edit_message_text("Reminder cancelled.")
        return ConversationHandler.END
    match = context.user_data["reminder_match"]
    offset_min = context.user_data["reminder_offset"]
    chat_id = update.effective_chat.id
    kickoff_str = match["kickoff_utc"].isoformat()
    db_execute(
        "INSERT INTO reminders (chat_id, match_id, match_home, match_away, kickoff_time, offset_minutes, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (chat_id, match["id"], match["home_team"], match["away_team"], kickoff_str, offset_min, ""),
    )
    row_id = db_execute("SELECT last_insert_rowid()", fetch_one=True)[0]
    row = {
        "id": row_id, "chat_id": chat_id,
        "match_home": match["home_team"], "match_away": match["away_team"],
        "kickoff_time": kickoff_str, "offset_minutes": offset_min, "note": ""
    }
    schedule_reminder_job(context.application, row)
    text = f"✅ Reminder set for {match['home_team']} vs {match['away_team']} ({offset_min} min before kick‑off)!"
    await query.edit_message_text(text)
    context.user_data.pop("reminder_match", None)
    context.user_data.pop("reminder_offset", None)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.edit_message_text("Action cancelled.")
    else:
        await update.message.reply_text("Action cancelled.")
    context.user_data.clear()
    return ConversationHandler.END

# ----------------------------- Main Command Handlers -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_MESSAGE, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)

async def match_list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_matches_list(update, update.effective_chat.id, context)

async def match_scores_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_match_scores(update, update.effective_chat.id, context)

async def match_reminders_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Directly show match list to start reminder flow (user picks a match)
    await send_matches_list(update, update.effective_chat.id, context)

# ----------------------------- Inline Callback Handlers -----------------------------
async def match_info_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if data.startswith("match_info|"):
        match_id = data.split("|")[1]
        await send_match_detail(update, match_id, context)

async def matches_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await send_matches_list(update, query.message.chat_id, context)

async def menu_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.delete_message()
    # Re‑send the main keyboard
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="Main menu:",
        reply_markup=MAIN_KEYBOARD
    )

# ----------------------------- Error Handler -----------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ An unexpected error occurred. Please try again later.",
            )
        except Exception:
            pass

# ----------------------------- Main Application -----------------------------
def main():
    init_db()
    application = Application.builder().token(BOT_TOKEN).build()

    # Reschedule all existing reminders on startup
    application.job_queue.run_once(lambda ctx: reschedule_all_reminders(application), when=1)

    # Reminder conversation
    set_reminder_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(reminder_start, pattern=r"^reminder_select\|")],
        states={
            REMINDER_OFFSET: [
                CallbackQueryHandler(reminder_offset_chosen, pattern=r"^offset_\d+"),
                CallbackQueryHandler(cancel, pattern="^cancel_reminder$"),
            ],
            CONFIRM_REMINDER: [
                CallbackQueryHandler(reminder_confirm, pattern="^confirm_reminder$"),
                CallbackQueryHandler(cancel, pattern="^cancel_reminder$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="set_reminder_conversation",
        per_message=False,
    )

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.Regex("^📋 Match List$"), match_list_handler))
    application.add_handler(MessageHandler(filters.Regex("^📊 Match Scores$"), match_scores_handler))
    application.add_handler(MessageHandler(filters.Regex("^🔔 Match Reminders$"), match_reminders_handler))
    application.add_handler(CallbackQueryHandler(match_info_callback, pattern=r"^match_info\|"))
    application.add_handler(CallbackQueryHandler(matches_list_callback, pattern="^matches_list$"))
    application.add_handler(CallbackQueryHandler(menu_back_callback, pattern="^menu_back$"))
    application.add_handler(set_reminder_conv)
    application.add_error_handler(error_handler)

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
