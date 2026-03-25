"""
watcher.py — полный хендлер сообщений для Март 7.

- Хендлит ВСЕ входящие и исходящие сообщения (лички + группы до 1000 чел)
- Каналы и чаты >1000 участников игнорирует
- Записывает контекст в context/messages.jsonl
- Триггер "клав" работает ТОЛЬКО от создателя (OWNER_ID)
- OWNER_ID задаётся через переменную окружения TELEGRAM_OWNER_ID
"""

import asyncio
import os
import subprocess
import re
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import (
    User, Chat, Channel,
    PeerUser, PeerChat, PeerChannel
)

load_dotenv()

API_ID = int(os.getenv("TELEGRAM_API_ID"))
API_HASH = os.getenv("TELEGRAM_API_HASH")
SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "my_session")
SESSION_STRING = os.getenv("TELEGRAM_SESSION_STRING")

# ═══ СОЗДАТЕЛЬ — единственный кто может управлять ═══
OWNER_ID = int(os.getenv("TELEGRAM_OWNER_ID", "0"))  # set in .env

TRIGGER = "клав"
MAX_CHAT_MEMBERS = 1000  # не хендлим чаты больше этого числа

# Семафор — только один запрос к openclaw одновременно
_openclaw_lock = asyncio.Semaphore(1)

# Кеш размеров чатов {chat_id: count}
_chat_size_cache: dict = {}

# Хранилище контекста
CONTEXT_DIR = Path(__file__).parent / "context"
CONTEXT_DIR.mkdir(exist_ok=True)
MESSAGES_FILE = CONTEXT_DIR / "messages.jsonl"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("watcher")

if SESSION_STRING:
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
else:
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)


# ═══ Утилиты ═══

def get_chat_type(entity) -> str:
    if isinstance(entity, User):
        return "private"
    if isinstance(entity, Chat):
        return "group"
    if isinstance(entity, Channel):
        if getattr(entity, "megagroup", False):
            return "group"
        return "channel"
    return "unknown"


async def get_members_count(entity) -> int:
    """Возвращает количество участников чата или 0 для личек."""
    try:
        if isinstance(entity, User):
            return 2  # личка — всегда пропускаем
        full = await client.get_entity(entity)
        return getattr(full, "participants_count", 0) or 0
    except Exception:
        return 0


def save_message(record: dict):
    """Сохраняет запись в JSONL файл."""
    with open(MESSAGES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


async def build_record(event, direction: str) -> dict | None:
    """Строит запись для сохранения."""
    try:
        msg = event.message
        chat = await event.get_chat()
        chat_type = get_chat_type(chat)

        # Пропускаем каналы
        if chat_type == "channel":
            return None

        # Проверяем размер чата (с кешем)
        if chat_type == "group":
            cid = getattr(chat, "id", None)
            if cid not in _chat_size_cache:
                count = getattr(chat, "participants_count", None)
                if not count:
                    try:
                        from telethon.tl.functions.channels import GetFullChannelRequest
                        from telethon.tl.functions.messages import GetFullChatRequest
                        if isinstance(chat, Channel):
                            full = await client(GetFullChannelRequest(chat))
                            count = full.full_chat.participants_count
                        elif isinstance(chat, Chat):
                            full = await client(GetFullChatRequest(chat.id))
                            count = getattr(full.full_chat, "participants_count", 0) or 0
                    except Exception as e:
                        log.warning(f"Не удалось получить размер чата {cid}: {e}")
                        count = 9999  # если ошибка — считаем большим, не пишем
                _chat_size_cache[cid] = count or 0
            if _chat_size_cache.get(cid, 0) > MAX_CHAT_MEMBERS:
                return None

        # Данные отправителя
        sender_id = None
        sender_name = ""
        sender_username = ""
        try:
            sender = await msg.get_sender()
            # Для исходящих sender может быть None — берём кешированные данные
            if sender is None and msg.out:
                sender = _me
            if sender:
                sender_id = sender.id
                sender_name = (getattr(sender, "first_name", "") or "") + \
                              (" " + getattr(sender, "last_name", "") if getattr(sender, "last_name", "") else "")
                sender_name = sender_name.strip()
                # username может быть строкой или None
                uname = getattr(sender, "username", None)
                usernames = getattr(sender, "usernames", None)  # Telegram Premium multiple usernames
                if uname:
                    sender_username = uname
                elif usernames:
                    sender_username = ",".join(u.username for u in usernames if u.username)
                else:
                    sender_username = ""
        except Exception:
            pass

        # Данные чата
        chat_id = msg.chat_id or msg.peer_id
        chat_title = getattr(chat, "title", None) or \
                     getattr(chat, "first_name", None) or str(chat_id)

        # Реплай
        reply_to_msg_id = None
        reply_to_text = ""
        reply_to_user = ""
        if msg.reply_to and getattr(msg.reply_to, "reply_to_msg_id", None):
            reply_to_msg_id = msg.reply_to.reply_to_msg_id
            try:
                replied = await msg.get_reply_message()
                if replied:
                    reply_to_text = (replied.message or "")[:300]
                    rs = await replied.get_sender()
                    if rs:
                        reply_to_user = (getattr(rs, "first_name", "") or "") + \
                                       (" " + getattr(rs, "last_name", "") if getattr(rs, "last_name", "") else "")
                        reply_to_user = reply_to_user.strip()
            except Exception:
                pass

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "direction": direction,  # "in" | "out"
            "chat_type": chat_type,  # "private" | "group"
            "chat_id": chat_id,
            "chat_title": chat_title,
            "msg_id": msg.id,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "sender_username": sender_username,
            "text": msg.message or "",
            "has_media": msg.media is not None,
            "reply_to_msg_id": reply_to_msg_id,
            "reply_to_text": reply_to_text,
            "reply_to_user": reply_to_user,
        }
        return record
    except Exception as e:
        log.error(f"build_record error: {e}")
        return None


LIVE_MESSAGES_LIMIT = 30  # последних сообщений из messages.jsonl

def load_live_messages(chat_id: int, limit: int = LIVE_MESSAGES_LIMIT) -> str:
    """Читает последние N сообщений этого чата прямо из messages.jsonl (live-данные)."""
    if not MESSAGES_FILE.exists():
        return ""
    msgs = []
    with open(MESSAGES_FILE, encoding="utf-8") as f:
        for line in f:
            try:
                m = json.loads(line)
                if m.get("chat_id") == chat_id and m.get("text","").strip():
                    msgs.append(m)
            except Exception:
                pass
    # берём последние N
    recent = msgs[-limit:]
    if not recent:
        return ""
    lines = []
    for m in recent:
        direction = "→" if m.get("sender_id") == OWNER_ID else "←"
        sender = m.get("sender", "?")
        text = m.get("text", "").replace("\n", " ")[:200]
        date = m.get("date", "")[:16]
        lines.append(f"[{date}] {direction} {sender}: {text}")
    return "## Последние сообщения (live)\n" + "\n".join(lines)


def load_chat_context(chat_id: int) -> str:
    """Загружает саммари чата, последние live-сообщения и профили участников."""
    parts = []
    chat_file = CONTEXT_DIR / "chats" / f"{chat_id}.md"
    if chat_file.exists():
        parts.append(f"[Контекст чата]\n{chat_file.read_text(encoding='utf-8')[:1500]}")

    # Live-сообщения из messages.jsonl (реальное время)
    live = load_live_messages(chat_id)
    if live:
        parts.append(live)

    # Профили людей из этого чата
    people_dir = CONTEXT_DIR / "people"
    if people_dir.exists():
        seen_uids = set()
        if MESSAGES_FILE.exists():
            with open(MESSAGES_FILE, encoding="utf-8") as f:
                for line in f:
                    try:
                        m = json.loads(line)
                        if m.get("chat_id") == chat_id and m.get("sender_id") and m["sender_id"] != OWNER_ID:
                            seen_uids.add(m["sender_id"])
                    except Exception:
                        pass
        for uid in seen_uids:
            pf = people_dir / f"{uid}.md"
            if pf.exists():
                parts.append(f"[Участник]\n{pf.read_text(encoding='utf-8')[:500]}")

    return "\n\n".join(parts)


# Кеш session_id живой main-сессии
_main_session_id: str | None = None

def _get_main_session_id() -> str | None:
    """Читает session_id живой main-сессии из sessions.json (без subprocess)."""
    global _main_session_id
    if _main_session_id:
        return _main_session_id
    try:
        import json as _j
        path = os.path.expanduser("~/.openclaw/agents/main/sessions/sessions.json")
        if os.path.exists(path):
            data = _j.loads(open(path).read())
            # Структура: {"agent:main:main": {"sessionId": "...", ...}, ...}
            for key, val in data.items():
                if key == "agent:main:main" and isinstance(val, dict):
                    sid = val.get("sessionId", "")
                    if sid:
                        _main_session_id = sid
                        log.info(f"[watcher] Найден session-id: {sid[:8]}...")
                        return sid
    except Exception as e:
        log.debug(f"[watcher] _get_main_session_id error: {e}")
    return None


def ask_openclaw(query: str, chat_id: int) -> str:
    """Отправляет запрос в OpenClaw — переиспользует живую main-сессию (быстро)."""
    chat_context = load_chat_context(chat_id)
    ctx_prefix = f"\n[Контекст из базы данных]\n{chat_context}\n\n" if chat_context else ""
    context = f"{ctx_prefix}[source:watcher] [Запрос из Telegram чата {chat_id}, отвечай кратко как в чате, без лишних слов. НЕ используй send_message — доставкой займётся watcher]: {query}"

    # Пробуем переиспользовать живую сессию (без нового процесса)
    session_id = _get_main_session_id()
    if session_id:
        cmd = ["openclaw", "agent", "--session-id", session_id, "--message", context, "--json"]
        log.info(f"[watcher] Используем session-id={session_id}")
    else:
        # Фолбэк: создаём новый процесс (медленнее)
        cmd = ["openclaw", "agent", "--agent", "main", "--message", context, "--json"]
        log.info("[watcher] session-id не найден, фолбэк на --agent main")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        # Если session-id устарел — сбросить кеш и повторить
        global _main_session_id
        _main_session_id = None
        return f"Ошибка: {result.stderr.strip()}"

    import json as _json
    try:
        data = _json.loads(result.stdout)
        if "result" in data and "payloads" in data["result"]:
            return data["result"]["payloads"][0]["text"]
        elif "payloads" in data:
            return data["payloads"][0]["text"]
        elif "text" in data:
            return data["text"]
        else:
            def find_text(obj):
                if isinstance(obj, dict):
                    if "text" in obj and isinstance(obj["text"], str) and len(obj["text"]) > 0:
                        return obj["text"]
                    for v in obj.values():
                        r = find_text(v)
                        if r:
                            return r
                elif isinstance(obj, list):
                    for item in obj:
                        r = find_text(item)
                        if r:
                            return r
                return None
            return find_text(data) or result.stdout.strip()
    except Exception:
        return result.stdout.strip()


# ═══ Хендлеры ═══

@client.on(events.NewMessage(outgoing=True))
async def handle_outgoing(event):
    """Хендлит исходящие сообщения."""
    text = event.raw_text or ""

    # Сохраняем в контекст
    record = await build_record(event, "out")
    if record:
        save_message(record)

    # Триггер "клав" — только от создателя
    if not re.match(rf"^{TRIGGER}\b", text, re.IGNORECASE):
        return

    # Получаем chat_id
    chat_id = event.chat_id

    # Извлекаем запрос
    query = re.sub(rf"^{TRIGGER}[,\s]*", "", text, flags=re.IGNORECASE).strip()
    if not query:
        return

    log.info(f"[trigger] чат {chat_id}: {query}")

    # Добавляем контекст реплая
    reply_context = ""
    if event.reply_to and getattr(event.reply_to, "reply_to_msg_id", None):
        try:
            replied_msg = await event.get_reply_message()
            if replied_msg:
                sender_id = replied_msg.sender_id
                sender_name = ""
                try:
                    sender = await replied_msg.get_sender()
                    if sender:
                        sender_name = (getattr(sender, "first_name", "") or "").strip()
                except Exception:
                    sender_name = str(sender_id)
                msg_text = replied_msg.message or "[медиа]"
                reply_context = f"\n[Реплай на сообщение от {sender_name} (user_id: {sender_id}, msg_id: {replied_msg.id}): \"{msg_text[:300]}\"]"
        except Exception:
            pass

    full_query = query + reply_context

    # Отправляем ⏳ как реплай
    thinking_msg = await event.reply("⏳")

    # Запрос к OpenClaw — сериализуем через семафор
    async with _openclaw_lock:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, ask_openclaw, full_query, chat_id)

    # Отправляем ответ
    if response:
        MAX_LEN = 4096
        if len(response) <= MAX_LEN:
            await thinking_msg.edit(response)
        else:
            await thinking_msg.delete()
            for i in range(0, len(response), MAX_LEN):
                await client.send_message(chat_id, response[i:i + MAX_LEN])
    else:
        await thinking_msg.delete()


@client.on(events.NewMessage(incoming=True))
async def handle_incoming(event):
    """Хендлит входящие сообщения — только записывает контекст."""
    record = await build_record(event, "in")
    if record:
        save_message(record)


# Кеш данных о себе — загружается один раз при старте
_me = None

async def refresh_me():
    """Обновляет данные о себе через API. Вызывать по запросу."""
    global _me
    _me = await client.get_me()
    uname = getattr(_me, "username", None)
    usernames = getattr(_me, "usernames", None)
    if usernames:
        uname = ",".join(u.username for u in usernames if u.username)
    log.info(f"[me] Обновлено: {_me.first_name} id={_me.id} username={uname}")
    return _me

async def main():
    log.info(f"[watcher] Запуск... триггер: '{TRIGGER}', только для owner: {OWNER_ID}")
    log.info(f"[watcher] Контекст сохраняется в: {MESSAGES_FILE}")
    await client.start()
    me = await client.get_me()
    await refresh_me()  # загружаем данные о себе один раз при старте
    log.info(f"[watcher] Авторизован как {_me.first_name} (id: {_me.id}). Слушаю все сообщения...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
