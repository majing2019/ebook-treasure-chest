"""科普视频追踪数据库 — 初始化与操作工具。

供 /science-video 技能调用，也提供命令行入口。
使用方式：python3 .claude/science_video_schema.py <命令> [参数...]
"""

import sqlite3
import json
import sys
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "science-video-tracker.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    source_path TEXT NOT NULL,
    source_type TEXT DEFAULT '',
    issue TEXT DEFAULT '',
    processed_date TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id),
    title TEXT NOT NULL,
    author TEXT DEFAULT '',
    section TEXT DEFAULT '',
    word_count INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'evaluated',
    eval_score INTEGER DEFAULT 0,
    eval_tags TEXT DEFAULT '[]',
    eval_best_styles TEXT DEFAULT '[]',
    eval_notes TEXT DEFAULT '',
    UNIQUE(book_id, title)
);

CREATE TABLE IF NOT EXISTS video_scripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id INTEGER NOT NULL REFERENCES stories(id),
    style TEXT NOT NULL,
    path TEXT NOT NULL,
    generated_date TEXT NOT NULL,
    notion_page_id TEXT DEFAULT '',
    notion_page_url TEXT DEFAULT '',
    notion_uploaded INTEGER DEFAULT 0,
    UNIQUE(story_id, style)
);

-- 统计视图
CREATE VIEW IF NOT EXISTS v_stats AS
SELECT
    (SELECT COUNT(*) FROM books) AS total_books,
    (SELECT COUNT(*) FROM stories) AS total_stories,
    (SELECT COUNT(*) FROM stories WHERE status = 'selected') AS selected,
    (SELECT COUNT(*) FROM stories WHERE status = 'rejected') AS rejected;

-- 每个故事已生成的脚本数
CREATE VIEW IF NOT EXISTS v_story_scripts AS
SELECT
    s.id AS story_id,
    s.title,
    s.status,
    b.name AS book_name,
    COUNT(vs.id) AS script_count
FROM stories s
LEFT JOIN video_scripts vs ON vs.story_id = s.id
JOIN books b ON b.id = s.book_id
GROUP BY s.id;

-- 书的处理进度
CREATE VIEW IF NOT EXISTS v_book_progress AS
SELECT
    b.id AS book_id,
    b.name,
    b.processed_date,
    COUNT(s.id) AS total,
    SUM(CASE WHEN s.status = 'selected' THEN 1 ELSE 0 END) AS selected,
    SUM(CASE WHEN s.status = 'rejected' THEN 1 ELSE 0 END) AS rejected,
    SUM(CASE WHEN s.status = 'evaluated' THEN 1 ELSE 0 END) AS pending
FROM books b
LEFT JOIN stories s ON s.book_id = b.id
GROUP BY b.id;
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

def add_book(name: str, source_path: str, source_type: str = "",
             issue: str = "", date: str = "") -> int:
    """添加书籍记录，返回 book_id。已存在则返回已有 id。"""
    db = get_db()
    cur = db.execute("SELECT id FROM books WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        db.close()
        return row["id"]
    cur = db.execute(
        "INSERT INTO books (name, source_path, source_type, issue, processed_date) "
        "VALUES (?, ?, ?, ?, ?)",
        (name, source_path, source_type, issue, date))
    db.commit()
    bid: int = cur.lastrowid or 0
    db.close()
    return bid


def get_processed_titles(book_name: str) -> set:
    """获取某本书已处理的故事标题集合。"""
    db = get_db()
    rows = db.execute("""
        SELECT s.title FROM stories s
        JOIN books b ON b.id = s.book_id
        WHERE b.name = ?
    """, (book_name,)).fetchall()
    db.close()
    return {r["title"] for r in rows}


# ──── 故事操作 ────

def add_story(book_id: int, title: str, author: str = "",
              section: str = "", word_count: int = 0) -> int:
    """添加故事记录，返回 story_id。已存在则返回已有 id。"""
    db = get_db()
    existing = db.execute(
        "SELECT id FROM stories WHERE book_id=? AND title=?",
        (book_id, title)).fetchone()
    if existing:
        db.close()
        return existing["id"]
    cur = db.execute(
        "INSERT INTO stories (book_id, title, author, section, word_count) "
        "VALUES (?, ?, ?, ?, ?)",
        (book_id, title, author, section, word_count))
    db.commit()
    sid: int = cur.lastrowid or 0
    db.close()
    return sid


def update_evaluation(story_id: int, score: int, tags: list,
                      best_styles: list, notes: str = ""):
    """写入评估结果，状态设为 evaluated。"""
    db = get_db()
    db.execute(
        "UPDATE stories SET eval_score=?, eval_tags=?, eval_best_styles=?, "
        "eval_notes=?, status='evaluated' WHERE id=?",
        (score, json.dumps(tags, ensure_ascii=False),
         json.dumps(best_styles, ensure_ascii=False), notes, story_id))
    db.commit()
    db.close()


def mark_selected(story_id: int):
    """标记故事为已选中。"""
    db = get_db()
    db.execute("UPDATE stories SET status='selected' WHERE id=?", (story_id,))
    db.commit()
    db.close()


def mark_rejected(story_id: int):
    """标记故事为已筛掉。"""
    db = get_db()
    db.execute("UPDATE stories SET status='rejected' WHERE id=?", (story_id,))
    db.commit()
    db.close()


# ──── 视频脚本操作 ────

def add_video_script(story_id: int, style: str, path: str,
                     date: str = "") -> int:
    """记录生成的视频脚本。"""
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO video_scripts "
        "(story_id, style, path, generated_date) "
        "VALUES (?, ?, ?, ?)",
        (story_id, style, path, date))
    db.commit()
    db.close()
    return story_id


def update_notion_upload(story_id: int, style: str,
                         page_id: str, page_url: str):
    """更新视频脚本的 Notion 上传信息。"""
    db = get_db()
    db.execute(
        "UPDATE video_scripts SET notion_page_id=?, notion_page_url=?, "
        "notion_uploaded=1 WHERE story_id=? AND style=?",
        (page_id, page_url, story_id, style))
    db.commit()
    db.close()


# ──── 查询操作 ────

def get_stats() -> dict:
    """获取全局统计。"""
    db = get_db()
    row = db.execute("SELECT * FROM v_stats").fetchone()
    db.close()
    return dict(row) if row else {}


def get_book_progress(book_name: Optional[str] = None) -> list:
    """获取书籍处理进度。不指定书名则返回全部。"""
    db = get_db()
    if book_name:
        rows = db.execute(
            "SELECT * FROM v_book_progress WHERE name = ?",
            (book_name,)).fetchall()
    else:
        rows = db.execute("SELECT * FROM v_book_progress").fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_unprocessed_stories(book_name: str) -> list:
    """获取某本书中状态仍为 evaluated 的故事。"""
    db = get_db()
    rows = db.execute("""
        SELECT s.* FROM stories s
        JOIN books b ON b.id = s.book_id
        WHERE b.name = ? AND s.status = 'evaluated'
        ORDER BY s.id
    """, (book_name,)).fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_selected_stories(book_name: Optional[str] = None) -> list:
    """获取已选中的故事，含脚本信息。"""
    db = get_db()
    if book_name:
        rows = db.execute("""
            SELECT s.*, b.name as book_name FROM stories s
            JOIN books b ON b.id = s.book_id
            WHERE s.status = 'selected' AND b.name = ?
            ORDER BY s.id
        """, (book_name,)).fetchall()
    else:
        rows = db.execute("""
            SELECT s.*, b.name as book_name FROM stories s
            JOIN books b ON b.id = s.book_id
            WHERE s.status = 'selected'
            ORDER BY s.id
        """, (book_name,)).fetchall()
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
        print("\n按书籍：")
        for b in get_book_progress():
            print(f"  {b['name']}: {b['total']}篇, 选中{b['selected']}, "
                  f"筛掉{b['rejected']}, 待定{b['pending']}")

    elif cmd == "selected":
        init_db()
        for s in get_selected_stories():
            print(f"  [{s['book_name']}] {s['title']} — 评分{s['eval_score']}")

    elif cmd == "dump":
        init_db()
        db = get_db()
        books = [dict(r) for r in db.execute("SELECT * FROM books").fetchall()]
        for b in books:
            b["stories"] = [
                dict(r) for r in db.execute(
                    "SELECT * FROM stories WHERE book_id=?", (b["id"],)).fetchall()]
            for s in b["stories"]:
                s["video_scripts"] = [
                    dict(r) for r in db.execute(
                        "SELECT * FROM video_scripts WHERE story_id=?",
                        (s["id"],)).fetchall()]
        db.close()
        print(json.dumps(books, ensure_ascii=False, indent=2))

    else:
        print(f"未知命令: {cmd}")
        print("可用: init | stats | selected | dump")
