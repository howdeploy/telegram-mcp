## Telegram (MCP: userbot через Telethon)

У тебя есть доступ к Telegram через MCP-тулы. Это userbot-доступ (личный аккаунт владельца), не бот.

### Правила доставки

- Если в запросе есть маркер `[source:watcher]` — **НЕ используй send_message и reply_to_message**. Watcher сам доставит твой ответ в чат. Просто верни текст.
- Если запрос пришёл НЕ из watcher (нет маркера) — можешь использовать send_message для отправки.

### Доступные тулы

**Чтение:**
- `get_chats` / `list_chats` — список чатов
- `get_messages` / `list_messages` / `get_history` — сообщения
- `search_contacts` / `list_contacts` — контакты
- `get_participants` / `get_admins` — участники групп
- `get_message_context` — контекст вокруг сообщения
- `get_pinned_messages` — закреплённые
- `get_direct_chat_by_contact` — найти личку по контакту

**Управление:**
- `forward_message` — переслать
- `pin_message` / `unpin_message` — закрепить/открепить
- `send_reaction` — поставить реакцию
- `create_poll` — создать опрос
- `edit_message` / `delete_message` — редактировать/удалить
- `promote_admin` / `ban_user` / `unban_user` — модерация

### ID чатов
- Группы/супергруппы: отрицательные (например `-100123456789`)
- Личные чаты: положительный user_id собеседника
- Для поиска чата по имени: `list_chats` + фильтр по названию

### Ограничения
- Максимум 100 сообщений за один запрос get_messages
- В личных чатах get_messages может возвращать 0 — используй get_history
- HTML-форматирование: `<br>` не работает, используй `\n`
