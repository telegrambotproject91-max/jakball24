#!/usr/bin/env python3
"""
JakBall 24h Score Tracker - Telegram Bot
Production-ready bot with SQLite storage, JobQueue reminders and FSM conversations.
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
    ReplyKeyboardRemove,
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

# Logging
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
    c.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            match_id TEXT NOT NULL,
            match_home TEXT,
            match_away TEXT,
            note_text TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
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
# In production replace with a real football API client.
def get_today_matches() -> List[Dict]:
    """
    Returns a list of match dicts for today.
    Structure: {id, home_team, away_team, kickoff_utc (datetime)}
    """
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    # Create a few sample matches with kickoff times spread over the day
    matches = [
        {
            "id": "match_1",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "kickoff_utc": today + timedelta(hours=14, minutes=30),
        },
        {
            "id": "match_2",
            "home_team": "Barcelona",
            "away_team": "Real Madrid",
            "kickoff_utc": today + timedelta(hours=17, minutes=0),
        },
        {
            "id": "match_3",
            "home_team": "Bayern Munich",
            "away_team": "Dortmund",
            "kickoff_utc": today + timedelta(hours=20, minutes=0),
        },
    ]
    # Keep only future matches (for realistic reminders)
    now = datetime.now(timezone.utc)
    return [m for m in matches if m["kickoff_utc"] > now]

def get_match_by_id(match_id: str) -> Optional[Dict]:
    matches = get_today_matches()
    for m in matches:
        if m["id"] == match_id:
            return m
    return None

# ----------------------------- Reminder Scheduling -----------------------------
async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Job callback that sends the reminder message."""
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
    """
    Schedules a JobQueue job for a reminder.
    reminder_row: sqlite3.Row or dict with keys id, chat_id, match_id,
                  match_home, match_away, kickoff_time, offset_minutes, note.
    """
    kickoff = datetime.fromisoformat(reminder_row["kickoff_time"])
    # Calculate when to fire: kickoff - offset_minutes
    remind_at = kickoff - timedelta(minutes=reminder_row["offset_minutes"])
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # naive, same as DB
    if remind_at <= now:
        # Already passed – do not schedule
        return
    delay = (remind_at - now).total_seconds()
    job_name = f"reminder_{reminder_row['id']}"
    # Remove any existing job with the same name (idempotent restart)
    current_jobs = application.job_queue.get_jobs_by_name(job_name)
    for j in current_jobs:
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
    logger.info(f"Scheduled reminder {reminder_row['id']} in {delay:.0f}s")

async def reschedule_all_reminders(application: Application):
    """On bot startup, load all future reminders from DB and schedule them."""
    rows = db_execute("SELECT * FROM reminders", fetch_all=True)
    for row in rows:
        schedule_reminder_job(application, row)

# ----------------------------- Keyboard & Helper Messages -----------------------------
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["⚽ Today's Matches", "⏰ Set Reminders"],
        ["📝 My Notes", "📖 Quick Guide"],
        ["⚙️ Settings / Help"],
    ],
    resize_keyboard=True,
)

WELCOME_MESSAGE = (
    "👋 **Welcome to JakBall 24h Score Tracker!**\n\n"
    "I keep you in the loop with today's football matches, help you set kick‑off reminders, "
    "and let you save personal notes for any game – all inside Telegram.\n\n"
    "📌 **Quick Guide**\n"
    "• *Today's Matches* – see all fixtures and quickly add a reminder or note.\n"
    "• *Set Reminders* – pick a match and choose when to be alerted before kick‑off.\n"
    "• *My Notes* – view and manage your private match notes.\n"
    "• *Quick Guide* – show this guide again.\n"
    "• *Settings / Help* – additional info.\n\n"
    "Let's get started! Use the buttons below 👇"
)

QUICK_GUIDE_TEXT = WELCOME_MESSAGE  # same content, reused

HELP_TEXT = (
    "🆘 **Help & Commands**\n\n"
    "/start – Restart the bot and see the main menu\n"
    "/help – Show this help message\n"
    "/cancel – Exit any ongoing conversation (reminder/note setup)\n\n"
    "Just tap the buttons below to navigate!"
)

# Build inline keyboard for a single match (Set Reminder / Add Note)
def match_inline_buttons(match: Dict) -> InlineKeyboardMarkup:
    match_id = match["id"]
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏰ Set Reminder", callback_data=f"reminder_select|{match_id}"),
            InlineKeyboardButton("📝 Add Note", callback_data=f"note_select|{match_id}"),
        ]
    ])

async def send_matches_list(update: Update, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Fetch today's matches and send them with inline buttons."""
    matches = get_today_matches()
    if not matches:
        await context.bot.send_message(chat_id=chat_id, text="No matches scheduled for today.")
        return

    lines = ["⚽ **Today's Matches**\n"]
    for m in matches:
        kickoff = m["kickoff_utc"].strftime("%H:%M UTC")
        lines.append(f"{m['home_team']} vs {m['away_team']} – {kickoff}")
    lines.append("\nSelect a match to set a reminder or add a note:")

    await context.bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{m['home_team']} vs {m['away_team']}", callback_data=f"match_info|{m['id']}")]
            for m in matches
        ] + [[InlineKeyboardButton("« Back to menu", callback_data="menu_back")]]),
        parse_mode="Markdown",
    )

async def send_match_detail(update: Update, match_id: str, context: ContextTypes.DEFAULT_TYPE):
    """Sends full info of a match with action buttons."""
    match = get_match_by_id(match_id)
    if not match:
        await update.callback_query.answer("Match not found.", show_alert=True)
        return
    kickoff = match["kickoff_utc"].strftime("%d %b %Y, %H:%M UTC")
    text = (
        f"⚽ *{match['home_team']}* vs *{match['away_team']}*\n"
        f"📅 {kickoff}\n\n"
        "What would you like to do?"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⏰ Set Reminder", callback_data=f"reminder_select|{match_id}"),
            InlineKeyboardButton("📝 Add Note", callback_data=f"note_select|{match_id}"),
        ],
        [InlineKeyboardButton("« Back to matches", callback_data="matches_list")],
    ])
    await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)

# ----------------------------- Conversation Handlers -----------------------------
# --- States for Set Reminder ---
REMINDER_OFFSET, CONFIRM_REMINDER = range(2)
# --- States for Add Note ---
NOTE_TEXT = 0

# ---- Set Reminder Conversation ----
async def reminder_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback entry: user clicked 'Set Reminder' on a match."""
    query = update.callback_query
    await query.answer()
    data = query.data  # "reminder_select|match_id"
    match_id = data.split("|")[1]
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
        [
            InlineKeyboardButton("15 min", callback_data="offset_15"),
            InlineKeyboardButton("30 min", callback_data="offset_30"),
            InlineKeyboardButton("60 min", callback_data="offset_60"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_reminder")],
    ])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    return REMINDER_OFFSET

async def reminder_offset_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data  # "offset_XX"
    if choice == "cancel_reminder":
        await query.edit_message_text("Reminder cancelled. Back to main menu.")
        await show_main_menu(update, context)
        return ConversationHandler.END
    offset_min = int(choice.split("_")[1])
    context.user_data["reminder_offset"] = offset_min
    match = context.user_data["reminder_match"]
    text = (
        f"🔔 Reminder for *{match['home_team']}* vs *{match['away_team']}* "
        f"will fire {offset_min} minutes before kick‑off.\n\n"
        "Would you like to add a personal note? (Optional)\n"
        "Tap *Confirm* to set the reminder, or *Cancel*."
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm", callback_data="confirm_reminder"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_reminder"),
        ]
    ])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    return CONFIRM_REMINDER

async def reminder_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "cancel_reminder":
        await query.edit_message_text("Reminder cancelled.")
        await show_main_menu(update, context)
        return ConversationHandler.END

    match = context.user_data["reminder_match"]
    offset_min = context.user_data["reminder_offset"]
    note = context.user_data.get("reminder_note", "")
    chat_id = update.effective_chat.id

    kickoff_str = match["kickoff_utc"].isoformat()
    db_execute(
        "INSERT INTO reminders (chat_id, match_id, match_home, match_away, kickoff_time, offset_minutes, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (chat_id, match["id"], match["home_team"], match["away_team"], kickoff_str, offset_min, note),
    )
    # Get the inserted row id
    row_id = db_execute("SELECT last_insert_rowid()", fetch_one=True)[0]
    # Schedule the job
    row = {
        "id": row_id,
        "chat_id": chat_id,
        "match_home": match["home_team"],
        "match_away": match["away_team"],
        "kickoff_time": kickoff_str,
        "offset_minutes": offset_min,
        "note": note,
    }
    schedule_reminder_job(context.application, row)

    text = f"✅ Reminder set for {match['home_team']} vs {match['away_team']} ({offset_min} min before kick‑off)!"
    if note:
        text += f"\n📝 Note: _{note}_"
    await query.edit_message_text(text, parse_mode="Markdown")
    # Clear user data
    context.user_data.pop("reminder_match", None)
    context.user_data.pop("reminder_offset", None)
    context.user_data.pop("reminder_note", None)
    await show_main_menu(update, context)
    return ConversationHandler.END

async def reminder_add_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Allow user to type a note during reminder confirmation (simple inline)."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📝 Type your note below (or send /skip to leave it empty).",
    )
    return CONFIRM_REMINDER  # Stay in same state, wait for text or /skip

async def reminder_skip_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reminder_note"] = ""
    return await reminder_confirm(update, context)

async def reminder_handle_note_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text when user is in CONFIRM_REMINDER and wants to add note."""
    note = update.message.text.strip()
    context.user_data["reminder_note"] = note
    # Go back to confirm step
    match = context.user_data["reminder_match"]
    offset_min = context.user_data["reminder_offset"]
    text = (
        f"🔔 Reminder for *{match['home_team']}* vs *{match['away_team']}* "
        f"will fire {offset_min} min before kick‑off.\n"
        f"📝 Note: _{note}_\n\n"
        "Tap *Confirm* to set the reminder, or *Cancel*."
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm", callback_data="confirm_reminder"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_reminder"),
        ]
    ])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)
    return CONFIRM_REMINDER

# ---- Add Note Conversation ----
async def note_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data  # "note_select|match_id"
    match_id = data.split("|")[1]
    match = get_match_by_id(match_id)
    if not match:
        await query.edit_message_text("Match not available.")
        return ConversationHandler.END
    context.user_data["note_match"] = match
    await query.edit_message_text(
        f"📝 Add a note for *{match['home_team']}* vs *{match['away_team']}*\n"
        "Please type your note below (or /skip to cancel).",
        parse_mode="Markdown",
    )
    return NOTE_TEXT

async def note_receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    note_text = update.message.text.strip()
    match = context.user_data["note_match"]
    chat_id = update.effective_chat.id

    db_execute(
        "INSERT INTO notes (chat_id, match_id, match_home, match_away, note_text) VALUES (?, ?, ?, ?, ?)",
        (chat_id, match["id"], match["home_team"], match["away_team"], note_text),
    )
    await update.message.reply_text(
        f"✅ Note saved for {match['home_team']} vs {match['away_team']}:\n_{note_text}_",
        parse_mode="Markdown",
    )
    context.user_data.pop("note_match", None)
    await show_main_menu(update, context)
    return ConversationHandler.END

async def note_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Note cancelled.")
    context.user_data.pop("note_match", None)
    await show_main_menu(update, context)
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fallback /cancel command."""
    await update.message.reply_text("Action cancelled.", reply_markup=MAIN_KEYBOARD)
    context.user_data.clear()
    return ConversationHandler.END

# ----------------------------- Main Command Handlers -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_MESSAGE, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)

async def quick_guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(QUICK_GUIDE_TEXT, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)

async def settings_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await help_command(update, context)  # same for now

async def todays_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_matches_list(update, update.effective_chat.id, context)

async def set_reminders_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Re‑use the same matches list with inline buttons
    await send_matches_list(update, update.effective_chat.id, context)

async def my_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display notes grouped by match for the current user."""
    chat_id = update.effective_chat.id
    rows = db_execute(
        "SELECT match_id, match_home, match_away, note_text FROM notes WHERE chat_id = ? ORDER BY created_at DESC",
        (chat_id,),
        fetch_all=True,
    )
    if not rows:
        await update.message.reply_text("📝 You have no notes yet. Use 'Add Note' on a match.")
        return

    # Group by match
    notes_by_match = {}
    for row in rows:
        key = (row["match_id"], row["match_home"], row["match_away"])
        notes_by_match.setdefault(key, []).append(row["note_text"])

    lines = ["📝 **Your Match Notes**\n"]
    for (mid, home, away), notes in notes_by_match.items():
        lines.append(f"⚽ *{home} vs {away}*")
        for n in notes:
            lines.append(f"  – {n}")
        lines.append("")
    text = "\n".join(lines)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add a note for today's match", callback_data="add_note_from_menu")],
        [InlineKeyboardButton("« Back to menu", callback_data="menu_back")],
    ])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resend the main keyboard (used after closing conversations)."""
    # If update has a message (command), use that, else if callback, edit message
    if update.message:
        await update.message.reply_text("What would you like to do?", reply_markup=MAIN_KEYBOARD)
    elif update.callback_query:
        await update.callback_query.edit_message_text("What would you like to do?", reply_markup=MAIN_KEYBOARD)

# ----------------------------- Inline Callback Handlers (non‑conversation) -----------------------------
async def match_info_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show match detail when user clicks on a match name."""
    query = update.callback_query
    data = query.data
    if data.startswith("match_info|"):
        match_id = data.split("|")[1]
        await send_match_detail(update, match_id, context)

async def matches_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to full match list."""
    query = update.callback_query
    await query.answer()
    await send_matches_list(update, query.message.chat_id, context)

async def menu_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to main menu."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Main menu:", reply_markup=MAIN_KEYBOARD)

async def add_note_from_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggered from My Notes menu -> redirect to choose a match."""
    query = update.callback_query
    await query.answer()
    await send_matches_list(update, query.message.chat_id, context)  # show matches, user can then click "Add Note"

# ----------------------------- Error Handler -----------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    # Notify user if possible
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

    # Reschedule all existing reminders from DB on startup
    application.job_queue.run_once(lambda ctx: reschedule_all_reminders(application), when=1)

    # Conversation handler: Set Reminder
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
                # Allow typing a note
                MessageHandler(filters.TEXT & ~filters.COMMAND, reminder_handle_note_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="set_reminder_conversation",
        per_message=False,
    )

    # Conversation handler: Add Note
    add_note_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(note_start, pattern=r"^note_select\|"),
            CallbackQueryHandler(add_note_from_menu, pattern="^add_note_from_menu$"),
        ],
        states={
            NOTE_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, note_receive_text),
                CommandHandler("skip", note_skip),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="add_note_conversation",
        per_message=False,
    )

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.Regex("^📖 Quick Guide$"), quick_guide))
    application.add_handler(MessageHandler(filters.Regex("^⚙️ Settings / Help$"), settings_help))
    application.add_handler(MessageHandler(filters.Regex("^⚽ Today's Matches$"), todays_matches))
    application.add_handler(MessageHandler(filters.Regex("^⏰ Set Reminders$"), set_reminders_menu))
    application.add_handler(MessageHandler(filters.Regex("^📝 My Notes$"), my_notes))
    application.add_handler(CallbackQueryHandler(match_info_callback, pattern=r"^match_info\|"))
    application.add_handler(CallbackQueryHandler(matches_list_callback, pattern="^matches_list$"))
    application.add_handler(CallbackQueryHandler(menu_back_callback, pattern="^menu_back$"))
    application.add_handler(set_reminder_conv)
    application.add_handler(add_note_conv)
    application.add_error_handler(error_handler)

    # Start bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
