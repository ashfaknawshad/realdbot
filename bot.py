import os
import time
import asyncio
import threading
import requests
import re
import io
import math
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

load_dotenv()

# ------------------ CONFIGURATION ------------------
API_ID = os.getenv("API_ID") 
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
RD_TOKEN = os.getenv("RD_TOKEN")
PORT = int(os.environ.get("PORT", 8080))
RD_API = "https://api.real-debrid.com/rest/1.0"

# ------------------ DUMMY WEB SERVER (FOR CHOREO) ------------------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.wfile.write(b"Bot is alive")
    def log_message(self, format, *args): pass

def start_web_server():
    HTTPServer(("0.0.0.0", PORT), HealthCheckHandler).serve_forever()

# ------------------ HELPERS ------------------
def progress_bar(pct, length=20):
    filled = int(length * pct / 100)
    return "[" + "█" * filled + "░" * (length - filled) + f"]"

def sanitize_filename(filename):
    clean = re.sub(r'[<>:"/\\|?*]', '', filename)
    clean = "".join(c for c in clean if c.isprintable())
    return clean.strip()

def human_size(size):
    if not size: return "0 B"
    power = 2**10
    n = 0
    units = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size > power:
        size /= power
        n += 1
    return f"{round(size, 2)} {units[n]}"

# ------------------ STREAMING CLASS (FIXED) ------------------
class RDStream(io.IOBase):
    def __init__(self, stream, name, size):
        self.stream = stream
        self.name = name
        self.total_size = int(size)
        self._pos = 0
        self.mode = 'rb'

    def read(self, size=-1):
        if size == -1: size = 1024 * 1024 
        data = self.stream.read(size)
        if data:
            self._pos += len(data)
        return data or b""

    def tell(self):
        return self._pos

    def seek(self, offset, whence=0):
        if whence == 0: self._pos = offset
        elif whence == 1: self._pos += offset
        elif whence == 2: self._pos = self.total_size + offset
        return self._pos

    def __len__(self):
        return self.total_size

# ------------------ PYROGRAM CLIENT ------------------
app = Client(
    "rd_bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=4,
    sleep_threshold=100
)

# ------------------ UI PROGRESS CALLBACK ------------------
async def upload_progress(current, total, message, start_time, filename):
    now = time.time()
    if now - getattr(message, "last_update", 0) > 5:
        pct = int(current / total * 100)
        elapsed = now - start_time
        speed = (current / 1024 / 1024) / elapsed if elapsed > 0 else 0
        
        text = (
            f"🚀 Uploading: `{filename}`\n"
            f"{progress_bar(pct)} {pct}%\n"
            f"⚡ {speed:.2f} MB/s\n"
            f"📦 {human_size(current)} / {human_size(total)}"
        )
        try:
            await message.edit_text(text)
            message.last_update = now
        except:
            pass

# ------------------ START COMMAND ------------------
@app.on_message(filters.command("start"))
async def start(client, message):
    await message.reply_text(
        "👋 **Real-Debrid Bot Online**\n\n"
        "📥 /mirror <magnet> - Get Direct Link\n"
        "🚀 /leech <magnet> - Stream to Telegram\n"
        "📂 /downloads - Manage Torrents"
    )

# ------------------ MIRROR COMMAND ------------------
@app.on_message(filters.command("mirror"))
async def mirror(client, message):
    if len(message.command) < 2: return await message.reply_text("⚠ Usage: `/mirror <magnet_link>`")
    magnet = message.text.split(maxsplit=1)[1]
    msg = await message.reply_text("✨ Adding Torrent...")
    
    # Add
    try:
        add = requests.post(f"{RD_API}/torrents/addMagnet", headers={"Authorization": f"Bearer {RD_TOKEN}"}, data={"magnet": magnet}).json()
        tid = add.get("id")
        if not tid: return await msg.edit_text("❌ Failed to add magnet.")
    except Exception as e: return await msg.edit_text(f"❌ Error: {e}")
    
    # Select Files
    requests.post(f"{RD_API}/torrents/selectFiles/{tid}", headers={"Authorization": f"Bearer {RD_TOKEN}"}, data={"files": "all"})
    
    # Wait Loop
    last_t = 0
    while True:
        info = requests.get(f"{RD_API}/torrents/info/{tid}", headers={"Authorization": f"Bearer {RD_TOKEN}"}).json()
        status = info.get('status')
        if status == 'downloaded': break
        if status == 'error': return await msg.edit_text("❌ RD Error: Torrent failed.")
        
        if time.time() - last_t > 3:
            await msg.edit_text(f"⏳ RD Downloading... {info.get('progress', 0)}%")
            last_t = time.time()
        await asyncio.sleep(2)

    # Get Link
    try:
        link = requests.post(f"{RD_API}/unrestrict/link", headers={"Authorization": f"Bearer {RD_TOKEN}"}, data={"link": info['links'][0]}).json().get("download")
        await msg.edit_text(f"✅ **Complete!**\n📂 {info['filename']}\n🔗 `{link}`")
    except:
        await msg.edit_text("❌ Error getting unrestrict link.")

# ------------------ LEECH COMMAND ------------------
@app.on_message(filters.command("leech"))
async def leech(client, message):
    if len(message.command) < 2: return await message.reply_text("⚠ Usage: `/leech <magnet_link>`")
    magnet = message.text.split(maxsplit=1)[1]
    msg = await message.reply_text("✨ Processing...")
    
    # 1. Add Magnet
    try:
        add = requests.post(f"{RD_API}/torrents/addMagnet", headers={"Authorization": f"Bearer {RD_TOKEN}"}, data={"magnet": magnet}).json()
        tid = add.get("id")
        if not tid: return await msg.edit_text("❌ Invalid Magnet")
    except Exception as e: return await msg.edit_text(f"❌ API Error: {e}")

    requests.post(f"{RD_API}/torrents/selectFiles/{tid}", headers={"Authorization": f"Bearer {RD_TOKEN}"}, data={"files": "all"})

    # 2. Wait for RD
    last_t = 0
    while True:
        info = requests.get(f"{RD_API}/torrents/info/{tid}", headers={"Authorization": f"Bearer {RD_TOKEN}"}).json()
        status = info.get('status')
        if status == 'downloaded': break
        if status == 'error': return await msg.edit_text("❌ RD Torrent Error")
        
        if time.time() - last_t > 3:
            await msg.edit_text(f"⏳ RD Downloading... {info.get('progress', 0)}%")
            last_t = time.time()
        await asyncio.sleep(2)

    # 3. Get Info
    try:
        data = requests.post(f"{RD_API}/unrestrict/link", headers={"Authorization": f"Bearer {RD_TOKEN}"}, data={"link": info['links'][0]}).json()
        link = data.get('download')
        filesize = int(data.get('filesize', 0))
        filename = sanitize_filename(data.get('filename', 'video.mkv'))
        
        if filesize == 0:
            head = requests.head(link, allow_redirects=True)
            filesize = int(head.headers.get('content-length', 0))
        
        if filesize == 0: return await msg.edit_text("❌ Error: File size is 0.")

    except Exception as e:
        return await msg.edit_text(f"❌ Error info: {e}")

    await msg.edit_text(f"🚀 Init Stream...\n📄 {filename}\n📦 {human_size(filesize)}")

    # 4. Stream
    try:
        with requests.get(link, stream=True) as r:
            r.raise_for_status()
            stream_obj = RDStream(r.raw, filename, filesize)
            start_time = time.time()
            
            await app.send_document(
                chat_id=message.chat.id,
                document=stream_obj,
                file_name=filename,
                force_document=True,
                progress=upload_progress,
                progress_args=(msg, start_time, filename)
            )
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"❌ Upload Failed: {e}")

# ------------------ DOWNLOADS (PAGINATION) ------------------
@app.on_message(filters.command("downloads"))
async def downloads(client, message):
    await show_downloads_page(message, page=0)

async def show_downloads_page(message, page):
    limit = 10
    offset = page * limit
    
    try:
        torrents = requests.get(f"{RD_API}/torrents", headers={"Authorization": f"Bearer {RD_TOKEN}"}, params={"limit": 50}).json()
    except:
        return await message.edit_text("❌ Error fetching torrents.")
        
    if not torrents:
        text = "📭 No torrents found."
        if isinstance(message, CallbackQuery): await message.edit_message_text(text)
        else: await message.reply_text(text)
        return

    # Slice for current page
    current_list = torrents[offset : offset + limit]
    
    if not current_list and page > 0:
        return await show_downloads_page(message, 0) # Reset to 0 if out of bounds

    buttons = []
    for t in current_list:
        status_icon = "🟢" if t['status'] == 'downloaded' else "🟠"
        btn_text = f"{status_icon} {t['filename'][:25]}..."
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"INFO|{t['id']}")])

    # Navigation Buttons
    nav_btns = []
    if page > 0:
        nav_btns.append(InlineKeyboardButton("⬅ Prev", callback_data=f"PAGE|{page-1}"))
    if len(torrents) > offset + limit:
        nav_btns.append(InlineKeyboardButton("Next ➡", callback_data=f"PAGE|{page+1}"))
    
    if nav_btns: buttons.append(nav_btns)

    text = f"📂 **Your Real-Debrid Torrents** (Page {page+1})"
    
    if isinstance(message, CallbackQuery):
        await message.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

# ------------------ CALLBACK HANDLER ------------------
@app.on_callback_query()
async def cb_handler(client, query):
    data = query.data.split("|")
    action = data[0]
    
    if action == "PAGE":
        await show_downloads_page(query, int(data[1]))
    
    elif action == "INFO":
        tid = data[1]
        try:
            info = requests.get(f"{RD_API}/torrents/info/{tid}", headers={"Authorization": f"Bearer {RD_TOKEN}"}).json()
            
            link = "Unavailable"
            if info.get('links'):
                # Try to get unrestrict link
                try:
                    u = requests.post(f"{RD_API}/unrestrict/link", headers={"Authorization": f"Bearer {RD_TOKEN}"}, data={"link": info['links'][0]}).json()
                    link = u.get("download", "Unavailable")
                except: pass

            text = (
                f"🎬 **{info['filename']}**\n"
                f"📦 Size: {human_size(info['bytes'])}\n"
                f"📊 Status: {info['status']}\n"
                f"🔗 [Direct Link]({link})"
            )
            
            buttons = [
                [InlineKeyboardButton("🗑 Delete Torrent", callback_data=f"DEL|{tid}")],
                [InlineKeyboardButton("🔙 Back to List", callback_data="PAGE|0")]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), disable_web_page_preview=True)
        except Exception as e:
            await query.answer(f"Error: {e}", show_alert=True)

    elif action == "DEL":
        tid = data[1]
        resp = requests.delete(f"{RD_API}/torrents/delete/{tid}", headers={"Authorization": f"Bearer {RD_TOKEN}"})
        if resp.status_code == 204:
            await query.answer("✅ Torrent Deleted", show_alert=True)
            await show_downloads_page(query, 0)
        else:
            await query.answer("❌ Failed to delete", show_alert=True)

# ------------------ START SERVER ------------------
if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True).start()
    print("🚀 BOT RUNNING (PYROGRAM)")
    app.run()
