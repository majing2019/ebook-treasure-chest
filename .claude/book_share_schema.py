"""读书分享追踪数据库 — 初始化与操作工具。

供 /book-share 技能调用，也提供命令行入口。
使用方式：python3 .claude/book_share_schema.py <命令> [参数...]
"""

import sqlite3
import json
import sys
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "book-share-tracker.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    author TEXT DEFAULT '',
    source_path TEXT NOT NULL,
    book_type TEXT DEFAULT '',
    word_count INTEGER DEFAULT 0,
    processed_date TEXT NOT NULL,
    UNIQUE(title, author)
);

CREATE TABLE IF NOT EXISTS scripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id),
    style TEXT NOT NULL,
    title TEXT NOT NULL,
    path TEXT NOT NULL,
    generated_date TEXT NOT NULL,
    notion_page_id TEXT DEFAULT '',
    notion_page_url TEXT DEFAULT '',
    notion_uploaded INTEGER DEFAULT 0,
    UNIQUE(book_id, style)
);

-- 统计视图
CREATE VIEW IF NOT EXISTS v_stats AS
SELECT
    (SELECT COUNT(*) FROM books) AS total_books,
    (SELECT COUNT(*) FROM scripts) AS total_scripts,
    (SELECT COUNT(DISTINCT book_id) FROM scripts) AS books_with_scripts;

-- 书的脚本列表
CREATE VIEW IF NOT EXISTS v_book_scripts AS
SELECT
    b.id AS book_id,
    b.title AS book_title,
    b.author,
    b.book_type,
    s.style,
    s.title AS script_title,
    s.path,
    s.generated_date,
    s.notion_uploaded
FROM books b
LEFT JOIN scripts s ON s.book_id = b.id
ORDER BY b.id, s.generated_date DESC;
"""


def init_db():
    """初始化数据库（幂等）。"""
    db = sqlite3.connect(str(DB_PATH))
    db.executescript(SCHEMA)
    db.commit()
    db.close()
    print(f"数据库已初始化：{DB_PATH}")


def get_db():
    """获取数据库连接（启用外键和行工厂）。"""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


# ──── 书籍操作 ────

def add_book(title: str, author: str = "", source_path: str = "",
             book_type: str = "", word_count: int = 0, date: str = "") -> int:
    """添加书籍记录，返回 book_id。已存在则返回已有 id。"""
    db = get_db()
    cur = db.execute(
        "SELECT id FROM books WHERE title = ? AND author = ?",
        (title, author))
    row = cur.fetchone()
    if row:
        db.close()
        return row["id"]
    cur = db.execute(
        "INSERT INTO books (title, author, source_path, book_type, word_count, processed_date) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (title, author, source_path, book_type, word_count, date))
    db.commit()
    bid: int = cur.lastrowid or 0
    db.close()
    return bid


def get_book(book_id: int) -> Optional[dict]:
    """获取单本书信息。"""
    db = get_db()
    row = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    db.close()
    return dict(row) if row else None


def get_processed_books() -> list:
    """获取所有已处理的书籍列表。"""
    db = get_db()
    rows = db.execute("SELECT * FROM books ORDER BY processed_date DESC").fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_book_scripts(book_id: int) -> list:
    """获取某本书已生成的所有脚本。"""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM scripts WHERE book_id = ? ORDER BY generated_date DESC",
        (book_id,)).fetchall()
    db.close()
    return [dict(r) for r in rows]


def is_book_processed(title: str, author: str) -> bool:
    """检查书籍是否已处理过。"""
    db = get_db()
    row = db.execute(
        "SELECT id FROM books WHERE title = ? AND author = ?",
        (title, author)).fetchone()
    db.close()
    return row is not None


# ──── 脚本操作 ────

def add_script(book_id: int, style: str, title: str, path: str,
               date: str = "") -> int:
    """记录生成的读书分享脚本。返回 script_id。"""
    db = get_db()
    existing = db.execute(
        "SELECT id FROM scripts WHERE book_id = ? AND style = ?",
        (book_id, style)).fetchone()
    if existing:
        # 更新已有记录
        db.execute(
            "UPDATE scripts SET title = ?, path = ?, generated_date = ? WHERE id = ?",
            (title, path, date, existing["id"]))
        db.commit()
        db.close()
        return existing["id"]
    cur = db.execute(
        "INSERT INTO scripts (book_id, style, title, path, generated_date) "
        "VALUES (?, ?, ?, ?, ?)",
        (book_id, style, title, path, date))
    db.commit()
    sid: int = cur.lastrowid or 0
    db.close()
    return sid


def update_notion_upload(book_id: int, style: str,
                         page_id: str, page_url: str):
    """更新脚本的 Notion 上传信息。"""
    db = get_db()
    db.execute(
        "UPDATE scripts SET notion_page_id = ?, notion_page_url = ?, "
        "notion_uploaded = 1 WHERE book_id = ? AND style = ?",
        (page_id, page_url, book_id, style))
    db.commit()
    db.close()


# ──── 查询操作 ────

def get_stats() -> dict:
    """获取全局统计。"""
    db = get_db()
    row = db.execute("SELECT * FROM v_stats").fetchone()
    db.close()
    return dict(row) if row else {}


def get_all_books_with_scripts() -> list:
    """获取所有书籍及其脚本信息。"""
    db = get_db()
    rows = db.execute("SELECT * FROM v_book_scripts").fetchall()
    db.close()
    return [dict(r) for r in rows]


# ──── CLI ────

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "init"

    if cmd == "init":
        init_db()

    elif cmd == "stats":
        init_db()
        stats = get_stats()
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        print("\n所有书籍：")
        for b in get_all_books_with_scripts():
            print(f"  《{b['book_title']}》{b['author']} [{b['book_type']}] "
                  f"→ {b['style']}: {b['script_title']} "
                  f"{'📤' if b['notion_uploaded'] else '📝'}")

    elif cmd == "books":
        init_db()
        for b in get_processed_books():
            scripts = get_book_scripts(b["id"])
            print(f"  [{b['id']}] 《{b['title']}》{b['author']} "
                  f"({b['book_type']}) — {len(scripts)} 个脚本")

    elif cmd == "dump":
        init_db()
        db = get_db()
        books = [dict(r) for r in db.execute("SELECT * FROM books").fetchall()]
        for b in books:
            b["scripts"] = [
                dict(r) for r in db.execute(
                    "SELECT * FROM scripts WHERE book_id = ?",
                    (b["id"],)).fetchall()]
        db.close()
        print(json.dumps(books, ensure_ascii=False, indent=2))

    else:
        print(f"未知命令: {cmd}")
        print("可用: init | stats | books | dump")
