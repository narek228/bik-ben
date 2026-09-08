"""
Хранение пользователей, баланса и истории транзакций.
SQLite достаточно для старта; при росте нагрузки — переезжайте на PostgreSQL,
структура запросов останется почти такой же.
"""
import aiosqlite
from config import DB_PATH, SIGNUP_BONUS_CREDITS, DEFAULT_MODEL

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS users (
    tg_id INTEGER PRIMARY KEY,
    username TEXT,
    balance REAL NOT NULL DEFAULT 0,
    model TEXT NOT NULL DEFAULT 'gpt-4o-mini',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER NOT NULL,
    amount REAL NOT NULL,           -- положительное = пополнение, отрицательное = списание
    reason TEXT,                    -- 'topup_crypto', 'topup_yookassa', 'generation'
    payment_id TEXT,                -- id платежа во внешней системе, для сверки
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tg_id) REFERENCES users (tg_id)
);

CREATE TABLE IF NOT EXISTS pending_payments (
    payment_id TEXT PRIMARY KEY,
    tg_id INTEGER NOT NULL,
    provider TEXT NOT NULL,         -- 'yookassa' | 'nowpayments'
    amount_credits REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | paid | failed
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_TABLES_SQL)
        await db.commit()


async def get_or_create_user(tg_id: int, username: str | None) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
        row = await cur.fetchone()
        if row:
            return dict(row)

        await db.execute(
            "INSERT INTO users (tg_id, username, balance, model) VALUES (?, ?, ?, ?)",
            (tg_id, username, SIGNUP_BONUS_CREDITS, DEFAULT_MODEL),
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
