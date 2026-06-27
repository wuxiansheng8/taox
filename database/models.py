# database/models.py
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(admin_username="admin", admin_password_hash=""):
    """
    初始化数据库表结构并插入默认配置
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. 账号与代理表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS accounts (
        username TEXT PRIMARY KEY,
        password TEXT NOT NULL,
        email TEXT NOT NULL,
        proxy TEXT,
        remark TEXT,
        status TEXT DEFAULT 'active',
        error_message TEXT,
        last_used TEXT
    )
    """)

    # 2. 监控备注表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS remarks (
        username TEXT PRIMARY KEY,
        remark_name TEXT NOT NULL,
        is_highlight INTEGER DEFAULT 0
    )
    """)

    # 3. 全局设置表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    # 4. 运行统计表 (用于24小时看板)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        status TEXT NOT NULL,
        error_message TEXT,
        account_used TEXT
    )
    """)

    # 5. 已发送推文去重表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS forwarded_tweets (
        tweet_id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_forwarded_tweets_timestamp ON forwarded_tweets (timestamp)")

    # 6. 待发送队列持久化表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pending_tweets (
        tweet_id TEXT PRIMARY KEY,
        job_json TEXT NOT NULL,
        timestamp TEXT NOT NULL
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pending_tweets_timestamp ON pending_tweets (timestamp)")

    # 插入默认配置
    default_settings = {
        "admin_username": admin_username,
        "admin_password_hash": admin_password_hash,
        "tg_token": "",
        "tg_chat_id": "",
        "polling_interval": "10",
        "twitter_list_id": "",
        
        "translate_enabled": "0",
        "translate_provider_primary": "google",
        "translate_provider_backup": "google",
        
        "openai_primary_api_key": "",
        "openai_primary_base_url": "https://api.openai.com/v1",
        "openai_primary_model": "gpt-3.5-turbo",
        
        "openai_backup_api_key": "",
        "openai_backup_base_url": "https://api.openai.com/v1",
        "openai_backup_model": "gpt-3.5-turbo"
    }

    for k, v in default_settings.items():
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    conn.commit()
    conn.close()
