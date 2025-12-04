import os
from dotenv import load_dotenv
import asyncio
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

load_dotenv()

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
USER_ID = os.getenv("USER_ID")
RD_TOKEN = os.getenv("RD_TOKEN")
PAGE_SIZE = 50  # torrents per page
RD_API = "https://api.real-debrid.com/rest/1.0"
POLL_INTERVAL = 10  # seconds between progress updates

# ---------------- HELPERS ----------------
def get_torrents(status_filter=None):
    headers = {"Authorization": f"Bearer {RD_TOKEN}"}
    torrents = requests.get(f"{RD_API}/torrents", headers=headers).json()
    if status_filter:
        torrents = [t for t in torrents if t["status"] in status_filter]
    return torrents

def get_downloads():
    headers = {"Authorization": f"Bearer {RD_TOKEN}"}
    return requests.get(f"{RD_API}/downloads", headers=headers).json()

def get_media_info(file_id):
    headers = {"Authorization": f"Bearer {RD_TOKEN}"}
    return requests.get(f"{RD_API}/streaming/mediaInfos/{file_id}", headers=headers).json()

def format_progress_bar(progress):
    """Return an ASCII progress bar"""
    total_blocks = 20
    filled = int(progress / 100 * total_blocks)
    bar = "█" * filled + "░" * (total_blocks - filled)
    return f"[{bar}] {progress:.1f}%"

# ---------------- COMMANDS ----------------
async def downloads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    finished = get_torrents(status_filter=["downloaded", "finished"])
    if not finished:
        await update.message.reply_text("❗ No completed torrents found.")
        return

    context.user_data["downloads"] = finished
    context.user_data["page"] = 0
    await show_downloads_page(update, context)

# ---------------- DISPLAY PAGE ----------------
async def show_downloads_page(update, context):
    page = context.user_data.get("page", 0)
    downloads_list = context.user_data.get("downloads", [])
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    page_items = downloads_list[start:end]

    buttons = []
    context.user_data["current_files"] = []
    for idx, t in enumerate(page_items):
        context.user_data["current_files"].append({"filename": t["filename"]})
        buttons.append([InlineKeyboardButton(t["filename"][:28], callback_data=f"file:{idx}")])

    nav_buttons = []
    if start > 0:
        nav_buttons.append(InlineKeyboardButton("⬅ Prev", callback_data="prev"))
    if end < len(downloads_list):
        nav_buttons.append(InlineKeyboardButton("Next ➡", callback_data="next"))
    if nav_buttons:
        buttons.append(nav_buttons)

    text = f"📁 Completed Torrents (Page {page+1}/{(len(downloads_list)-1)//PAGE_SIZE +1})"
    if update.callback_query:
        await update.callback_query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await update.message.reply_text(text=text, reply_markup=InlineKeyboardMarkup(buttons))

# ---------------- BUTTON HANDLER ----------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "next":
        context.user_data["page"] += 1
        await show_downloads_page(update, context)
        return
    elif data == "prev":
        context.user_data["page"] -= 1
        await show_downloads_page(update, context)
        return
    elif data.startswith("file:"):
        idx = int(data.split(":")[1])
        file_info = context.user_data["current_files"][idx]
        filename = file_info["filename"]

        downloads_data = get_downloads()
        match = next((f for f in downloads_data if f["filename"] == filename), None)
        if not match:
            await query.edit_message_text("❗ File not found / not ready yet.")
            return

        size_gb = round(match.get("filesize", 0) / (1024**3), 2)
        download_link = match.get("download", "N/A")

        media_info = get_media_info(match["id"])
        resolution = "N/A"
        if "details" in media_info and "video" in media_info["details"]:
            video_streams = media_info["details"]["video"]
            if video_streams:
                first_stream = list(video_streams.values())[0]
                resolution = f"{first_stream.get('width','?')}x{first_stream.get('height','?')}"

        msg = (
            f"📄 *{filename}*\n"
            f"💾 Size: `{size_gb} GB`\n"
            f"📺 Resolution: {resolution}\n"
            f"🔗 [Download Link]({download_link})"
        )
        await query.edit_message_text(msg, parse_mode="Markdown", disable_web_page_preview=False)

# ---------------- DOWNLOAD WATCHER ----------------
async def download_watcher(app):
    """Continuously watch active torrents and send progress updates"""
    last_status = {}  # store last known progress
    while True:
        active_torrents = get_torrents(status_filter=["waiting", "downloading"])
        for t in active_torrents:
            tid = t["id"]
            filename = t["filename"]
            progress = t.get("progress", 0)

            msg_text = f"⬇️ Downloading: *{filename}*\n{format_progress_bar(progress)}"

            # Only edit if progress actually changed
            if tid not in last_status:
                sent_msg = await app.bot.send_message(chat_id=USER_ID, text=msg_text, parse_mode="Markdown")
                last_status[tid] = {"msg_id": sent_msg.message_id, "progress": progress}
            else:
                if last_status[tid]["progress"] != progress:
                    msg_id = last_status[tid]["msg_id"]
                    await app.bot.edit_message_text(chat_id=USER_ID, message_id=msg_id, text=msg_text, parse_mode="Markdown")
                    last_status[tid]["progress"] = progress
            # If finished, update message and remove from tracking
            if t["status"] in ["downloaded", "finished"]:
                msg_id = last_status[tid]["msg_id"]
                try:
                    await app.bot.edit_message_text(
                        chat_id=USER_ID,
                        message_id=msg_id,
                        text=f"✅ Completed: *{filename}*",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    print(f"Failed to update completion message for {filename}: {e}")

                # Remove from tracking
                del last_status[tid]


        await asyncio.sleep(POLL_INTERVAL)

# ---------------- STARTUP ----------------
async def on_startup(app):
    await app.bot.send_message(chat_id=USER_ID, text="🟢 RD Bot Active — Type /downloads to view completed files")
    asyncio.create_task(download_watcher(app))  # PTB warning usually disappears

# ---------------- RUN APP ----------------
app = ApplicationBuilder().token(BOT_TOKEN).post_init(on_startup).build()
app.add_handler(CommandHandler("downloads", downloads))
app.add_handler(CallbackQueryHandler(button_handler))

app.run_polling()
