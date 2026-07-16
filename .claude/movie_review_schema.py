"""电影影评追踪数据库 — 初始化与操作工具。

供 /movie-review 技能调用，也提供命令行入口。
使用方式：python3 .claude/movie_review_schema.py <命令> [参数...]
"""

import sqlite3
import json
import sys
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "movie-review-tracker.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS movies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    director TEXT DEFAULT '',
    release_year TEXT DEFAULT '',
    genre TEXT DEFAULT '',
    douban_rating TEXT DEFAULT '',
    source TEXT DEFAULT '',
    processed_date TEXT NOT NULL,
    UNIQUE(title, director, release_year)
);

CREATE TABLE IF NOT EXISTS scripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    movie_id INTEGER NOT NULL REFERENCES movies(id),
    style TEXT NOT NULL,
    title TEXT NOT NULL,
    path TEXT NOT NULL,
    generated_date TEXT NOT NULL,
    notion_page_id TEXT DEFAULT '',
    notion_page_url TEXT DEFAULT '',
    notion_uploaded INTEGER DEFAULT 0,
    UNIQUE(movie_id, style)
);

-- 统计视图
CREATE VIEW IF NOT EXISTS v_stats AS
SELECT
    (SELECT COUNT(*) FROM movies) AS total_movies,
    (SELECT COUNT(*) FROM scripts) AS total_scripts,
    (SELECT COUNT(DISTINCT movie_id) FROM scripts) AS movies_with_scripts;

-- 电影的脚本列表
CREATE VIEW IF NOT EXISTS v_movie_scripts AS
SELECT
    m.id AS movie_id,
    m.title AS movie_title,
    m.director,
    m.release_year,
    m.genre,
    m.douban_rating,
    s.style,
    s.title AS script_title,
    s.path,
    s.generated_date,
    s.notion_uploaded
FROM movies m
LEFT JOIN scripts s ON s.movie_id = m.id
ORDER BY m.id, s.generated_date DESC;
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


# ──── 电影操作 ────

def add_movie(title: str, director: str = "", release_year: str = "",
              genre: str = "", douban_rating: str = "", source: str = "",
              date: str = "") -> int:
    """添加电影记录，返回 movie_id。已存在则返回已有 id。"""
    db = get_db()
    cur = db.execute(
        "SELECT id FROM movies WHERE title = ? AND director = ? AND release_year = ?",
        (title, director, release_year))
    row = cur.fetchone()
    if row:
        # 更新豆瓣评分和类型（可能搜索到更准确的信息）
        db.execute(
            "UPDATE movies SET genre = ?, douban_rating = ?, source = ? WHERE id = ?",
            (genre, douban_rating, source, row["id"]))
        db.commit()
        db.close()
        return row["id"]
    cur = db.execute(
        "INSERT INTO movies (title, director, release_year, genre, douban_rating, source, processed_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (title, director, release_year, genre, douban_rating, source, date))
    db.commit()
    mid: int = cur.lastrowid or 0
    db.close()
    return mid


def get_movie(movie_id: int) -> Optional[dict]:
    """获取单部电影信息。"""
    db = get_db()
    row = db.execute("SELECT * FROM movies WHERE id = ?", (movie_id,)).fetchone()
    db.close()
    return dict(row) if row else None


def get_processed_movies() -> list:
    """获取所有已处理的电影列表。"""
    db = get_db()
    rows = db.execute("SELECT * FROM movies ORDER BY processed_date DESC").fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_movie_scripts(movie_id: int) -> list:
    """获取某部电影已生成的所有脚本。"""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM scripts WHERE movie_id = ? ORDER BY generated_date DESC",
        (movie_id,)).fetchall()
    db.close()
    return [dict(r) for r in rows]


def is_movie_processed(title: str, director: str) -> bool:
    """检查电影是否已处理过。"""
    db = get_db()
    row = db.execute(
        "SELECT id FROM movies WHERE title = ? AND director = ?",
        (title, director)).fetchone()
    db.close()
    return row is not None


# ──── 脚本操作 ────

def add_script(movie_id: int, style: str, title: str, path: str,
               date: str = "") -> int:
    """记录生成的影评脚本。返回 script_id。"""
    db = get_db()
    existing = db.execute(
        "SELECT id FROM scripts WHERE movie_id = ? AND style = ?",
        (movie_id, style)).fetchone()
    if existing:
        # 更新已有记录
        db.execute(
            "UPDATE scripts SET title = ?, path = ?, generated_date = ? WHERE id = ?",
            (title, path, date, existing["id"]))
        db.commit()
        db.close()
        return existing["id"]
    cur = db.execute(
        "INSERT INTO scripts (movie_id, style, title, path, generated_date) "
        "VALUES (?, ?, ?, ?, ?)",
        (movie_id, style, title, path, date))
    db.commit()
    sid: int = cur.lastrowid or 0
    db.close()
    return sid


def update_notion_upload(movie_id: int, style: str,
                         page_id: str, page_url: str):
    """更新脚本的 Notion 上传信息。"""
    db = get_db()
    db.execute(
        "UPDATE scripts SET notion_page_id = ?, notion_page_url = ?, "
        "notion_uploaded = 1 WHERE movie_id = ? AND style = ?",
        (page_id, page_url, movie_id, style))
    db.commit()
    db.close()


# ──── 查询操作 ────

def get_stats() -> dict:
    """获取全局统计。"""
    db = get_db()
    row = db.execute("SELECT * FROM v_stats").fetchone()
    db.close()
    return dict(row) if row else {}


def get_all_movies_with_scripts() -> list:
    """获取所有电影及其脚本信息。"""
    db = get_db()
    rows = db.execute("SELECT * FROM v_movie_scripts").fetchall()
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
        print("\n所有电影：")
        for m in get_all_movies_with_scripts():
            print(f"  🎬《{m['movie_title']}》（{m['release_year']}）"
                  f"导演 {m['director']} [{m['genre']}] ⭐{m['douban_rating']}"
                  f"  → {m['style']}: {m['script_title']} "
                  f"{'📤' if m['notion_uploaded'] else '📝'}")

    elif cmd == "movies":
        init_db()
        for m in get_processed_movies():
            scripts = get_movie_scripts(m["id"])
            print(f"  [{m['id']}] 🎬《{m['title']}》（{m['release_year']}）"
                  f"导演 {m['director']} ({m['genre']}) ⭐{m['douban_rating']}"
                  f" — {len(scripts)} 个脚本")

    elif cmd == "dump":
        init_db()
        db = get_db()
        movies = [dict(r) for r in db.execute("SELECT * FROM movies").fetchall()]
        for m in movies:
            m["scripts"] = [
                dict(r) for r in db.execute(
                    "SELECT * FROM scripts WHERE movie_id = ?",
                    (m["id"],)).fetchall()]
        db.close()
        print(json.dumps(movies, ensure_ascii=False, indent=2))

    else:
        print(f"未知命令: {cmd}")
        print("可用: init | stats | movies | dump")
