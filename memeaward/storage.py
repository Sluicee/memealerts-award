import json
import sqlite3
from datetime import datetime, timezone


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS redemptions (
                id TEXT PRIMARY KEY, reward_id TEXT NOT NULL, nickname TEXT NOT NULL,
                amount INTEGER NOT NULL, state TEXT NOT NULL, note TEXT NOT NULL,
                updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS notifications (
                redemption_id TEXT PRIMARY KEY, login TEXT NOT NULL, state TEXT NOT NULL);
        """)
        # A crash during an outbound credit must never lead to an automatic retry.
        self.db.execute("UPDATE redemptions SET state='uncertain', note='Приложение завершилось во время начисления. Проверьте баланс вручную.' WHERE state='sending'")
        self.db.execute("""UPDATE redemptions SET note=note || ' Доставка сообщения в чат неизвестна после завершения приложения.'
            WHERE id IN (SELECT redemption_id FROM notifications WHERE state='sending')""")
        self.db.execute("UPDATE notifications SET state='failed' WHERE state='sending'")
        self.db.commit()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))
        self.db.commit()

    def row(self, redemption_id):
        return self.db.execute("SELECT * FROM redemptions WHERE id=?", (redemption_id,)).fetchone()

    def record(self, redemption_id, reward_id, nickname, amount, state, note=""):
        self.db.execute("INSERT OR IGNORE INTO redemptions VALUES (?,?,?,?,?,?,?)", (
            redemption_id, reward_id, nickname, amount, state, note, datetime.now(timezone.utc).isoformat()))
        self.db.commit()

    def update(self, redemption_id, state, note=""):
        self.db.execute("UPDATE redemptions SET state=?,note=?,updated=? WHERE id=?",
                        (state, note, datetime.now(timezone.utc).isoformat(), redemption_id))
        self.db.commit()

    def recent(self):
        # Never hide an unresolved operation behind the recent-history limit.
        return self.db.execute("""SELECT * FROM redemptions WHERE state='uncertain' OR id IN
            (SELECT id FROM redemptions ORDER BY updated DESC LIMIT 200)
            ORDER BY updated DESC""").fetchall()

    def uncertain(self):
        return self.db.execute("SELECT * FROM redemptions WHERE state='uncertain'").fetchall()

    def queue_notification(self, redemption_id, login):
        self.db.execute("INSERT OR IGNORE INTO notifications VALUES (?,?,'pending')", (redemption_id, login))
        self.db.commit()

    def ready_notifications(self, reward_id):
        return self.db.execute("""SELECT n.redemption_id, n.login, r.note FROM notifications n
            JOIN redemptions r ON r.id=n.redemption_id
            WHERE r.reward_id=? AND r.state='canceled' AND n.state='pending'""", (reward_id,)).fetchall()

    def notification_state(self, redemption_id, state):
        self.db.execute("UPDATE notifications SET state=? WHERE redemption_id=?", (state, redemption_id))
        self.db.commit()

    def close(self):
        self.db.close()
