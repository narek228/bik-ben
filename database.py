"""
Хранение пользователей, баланса, истории транзакций, истории диалога и
реферальных связей.
SQLite достаточно для старта; при росте нагрузки — переезжайте на PostgreSQL,
структура запросов останется почти такой же.
"""
import aiosqlite
from config import DB_PATH, SIGNUP_BONUS_CREDITS, DEFAULT_MODEL, MAX_HISTORY_MESSAGES

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS users (
    tg_id INTEGER PRIMARY KEY,
    username TEXT,
    balance REAL NOT NULL DEFAULT 0,
    model TEXT NOT NULL DEFAULT 'gpt-4o-mini',
    mode TEXT NOT NULL DEFAULT 'default',
    referred_by INTEGER,            -- tg_id того, кто пригласил (NULL если пришёл сам)
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER NOT NULL,
    amount REAL NOT NULL,           -- положительное = пополнение, отрицательное = списание
    reason TEXT,                    -- 'topup_crypto', 'topup_yookassa', 'generation', 'referral_bonus'...
    payment_id TEXT,                -- id платежа во внешней системе, для сверки
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tg_id) REFERENCES users (tg_id)
);

CREATE TABLE IF NOT EXISTS pending_payments (
    payment_id TEXT PRIMARY KEY,
    tg_id INTEGER NOT NULL,
    provider TEXT NOT NULL,         -- 'yookassa' | 'nowpayments' | 'yoomoney'
    amount_credits REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | paid | failed
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER NOT NULL,
    role TEXT NOT NULL,             -- 'user' | 'assistant'
    content TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tg_id) REFERENCES users (tg_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_tg_id ON messages (tg_id, id);
"""


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_TABLES_SQL)

        # Миграция старой SQLite-базы: CREATE TABLE IF NOT EXISTS
        # не добавляет новые колонки в уже существующую таблицу.
        cur = await db.execute("PRAGMA table_info(users)")
        columns = {row[1] for row in await cur.fetchall()}

        if "mode" not in columns:
            await db.execute(
                "ALTER TABLE users ADD COLUMN mode TEXT NOT NULL DEFAULT 'default'"
            )
        if "referred_by" not in columns:
            await db.execute(
                "ALTER TABLE users ADD COLUMN referred_by INTEGER"
            )

        await db.commit()


async def user_exists(tg_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM users WHERE tg_id = ?", (tg_id,))
        return (await cur.fetchone()) is not None


async def get_or_create_user(tg_id: int, username: str | None, referred_by: int | None = None) -> dict:
    """
    referred_by передаётся только при первой регистрации (из /start ref_XXXXX).
    Если пользователь уже существует — параметр игнорируется, повторно
    привязать реферера нельзя (защита от накрутки).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
        row = await cur.fetchone()
        if row:
            return dict(row)

        # referred_by не может указывать на самого себя и должен существовать
        valid_referrer = None
        if referred_by and referred_by != tg_id:
            cur = await db.execute("SELECT tg_id FROM users WHERE tg_id = ?", (referred_by,))
            if await cur.fetchone():
                valid_referrer = referred_by

        await db.execute(
            "INSERT INTO users (tg_id, username, balance, model, referred_by) VALUES (?, ?, ?, ?, ?)",
            (tg_id, username, SIGNUP_BONUS_CREDITS, DEFAULT_MODEL, valid_referrer),
        )
        await db.execute(
            "INSERT INTO transactions (tg_id, amount, reason) VALUES (?, ?, ?)",
            (tg_id, SIGNUP_BONUS_CREDITS, "signup_bonus"),
        )
        await db.commit()

        cur = await db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
        row = await cur.fetchone()
        return dict(row)


async def get_balance(tg_id: int) -> float:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT balance FROM users WHERE tg_id = ?", (tg_id,))
        row = await cur.fetchone()
        return row[0] if row else 0.0


async def set_model(tg_id: int, model: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET model = ? WHERE tg_id = ?", (model, tg_id))
        await db.commit()


async def get_model(tg_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT model FROM users WHERE tg_id = ?", (tg_id,))
        row = await cur.fetchone()
        return row[0] if row else DEFAULT_MODEL


async def try_charge(tg_id: int, amount: float, reason: str = "generation") -> bool:
    """
    Атомарно списывает amount кредитов, если баланса хватает.
    Возвращает True, если списание прошло, False — если баланс недостаточен.
    ВАЖНО: списание идёт ДО отправки запроса в LLM (см. bot.py) — так пользователь
    не может потратить больше, чем есть, даже при параллельных запросах.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT balance FROM users WHERE tg_id = ?", (tg_id,))
        row = await cur.fetchone()
        if not row or row[0] < amount:
            await db.rollback()
            return False

        await db.execute(
            "UPDATE users SET balance = balance - ? WHERE tg_id = ?", (amount, tg_id)
        )
        await db.execute(
            "INSERT INTO transactions (tg_id, amount, reason) VALUES (?, ?, ?)",
            (tg_id, -amount, reason),
        )
        await db.commit()
        return True


async def refund(tg_id: int, amount: float, reason: str = "refund_api_error"):
    """Возврат кредитов, если запрос к LLM упал с ошибкой уже после списания."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE tg_id = ?", (amount, tg_id)
        )
        await db.execute(
            "INSERT INTO transactions (tg_id, amount, reason) VALUES (?, ?, ?)",
            (tg_id, amount, reason),
        )
        await db.commit()


async def add_credits(tg_id: int, amount: float, reason: str, payment_id: str | None = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE tg_id = ?", (amount, tg_id)
        )
        await db.execute(
            "INSERT INTO transactions (tg_id, amount, reason, payment_id) VALUES (?, ?, ?, ?)",
            (tg_id, amount, reason, payment_id),
        )
        await db.commit()


async def create_pending_payment(payment_id: str, tg_id: int, provider: str, amount_credits: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO pending_payments (payment_id, tg_id, provider, amount_credits) VALUES (?, ?, ?, ?)",
            (payment_id, tg_id, provider, amount_credits),
        )
        await db.commit()


async def get_pending_payment(payment_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM pending_payments WHERE payment_id = ?", (payment_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def mark_payment_paid(payment_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE pending_payments SET status = 'paid' WHERE payment_id = ?", (payment_id,)
        )
        await db.commit()


async def complete_pending_payment(
    payment_id: str,
    reason: str,
) -> dict | None:
    """
    Атомарно завершает pending-платёж и начисляет кредиты ровно один раз.

    Webhook и фоновый polling могут прийти одновременно, поэтому проверка
    статуса и начисление должны выполняться внутри одной транзакции.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")

        cur = await db.execute(
            "SELECT * FROM pending_payments WHERE payment_id = ?",
            (payment_id,),
        )
        row = await cur.fetchone()

        if not row or row["status"] != "pending":
            await db.rollback()
            return None

        await db.execute(
            "UPDATE users SET balance = balance + ? WHERE tg_id = ?",
            (row["amount_credits"], row["tg_id"]),
        )
        await db.execute(
            "INSERT INTO transactions (tg_id, amount, reason, payment_id) "
            "VALUES (?, ?, ?, ?)",
            (
                row["tg_id"],
                row["amount_credits"],
                reason,
                payment_id,
            ),
        )
        await db.execute(
            "UPDATE pending_payments SET status = 'paid' WHERE payment_id = ?",
            (payment_id,),
        )
        await db.commit()

        return dict(row)


# ---------- Память диалога ----------

async def add_message(tg_id: int, role: str, content: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO messages (tg_id, role, content) VALUES (?, ?, ?)",
            (tg_id, role, content),
        )
        await db.commit()
        # Подчищаем старые сообщения сверх лимита, чтобы таблица не росла бесконечно
        # и чтобы контекст, отправляемый в LLM, не раздувал стоимость запроса.
        await db.execute(
            """
            DELETE FROM messages WHERE tg_id = ? AND id NOT IN (
                SELECT id FROM messages WHERE tg_id = ? ORDER BY id DESC LIMIT ?
            )
            """,
            (tg_id, tg_id, MAX_HISTORY_MESSAGES),
        )
        await db.commit()


async def get_recent_messages(tg_id: int, limit: int = MAX_HISTORY_MESSAGES) -> list[dict]:
    """Возвращает последние сообщения в хронологическом порядке (старые → новые)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT role, content FROM messages WHERE tg_id = ? ORDER BY id DESC LIMIT ?",
            (tg_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in reversed(rows)]


async def clear_history(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM messages WHERE tg_id = ?", (tg_id,))
        await db.commit()


# ---------- Режимы (промпт-шаблоны) ----------

async def set_mode(tg_id: int, mode: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET mode = ? WHERE tg_id = ?", (mode, tg_id))
        await db.commit()


async def get_mode(tg_id: int) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT mode FROM users WHERE tg_id = ?", (tg_id,))
        row = await cur.fetchone()
        return row[0] if row else "default"


# ---------- История транзакций ----------

async def get_transactions(tg_id: int, limit: int = 15) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT amount, reason, created_at FROM transactions "
            "WHERE tg_id = ? ORDER BY id DESC LIMIT ?",
            (tg_id, limit),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


# ---------- Реферальная программа ----------

async def get_referrer(tg_id: int) -> int | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT referred_by FROM users WHERE tg_id = ?", (tg_id,))
        row = await cur.fetchone()
        return row[0] if row and row[0] else None


async def count_referrals(tg_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (tg_id,))
        row = await cur.fetchone()
        return row[0] if row else 0
