# database/crud.py
from .models import get_db_connection
from datetime import datetime, timedelta

# --- Settings CRUD ---
def get_settings():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT key, value FROM settings")
    rows = cursor.fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}

def get_setting(key, default=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row["value"] if row else default

def update_settings(settings_dict):
    conn = get_db_connection()
    cursor = conn.cursor()
    for k, v in settings_dict.items():
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, str(v)))
    conn.commit()
    conn.close()

# --- Accounts CRUD ---
def get_accounts():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT username, email, password, proxy, remark, status, error_message, last_used FROM accounts")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_active_accounts():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT username, email, password, proxy, remark, status FROM accounts WHERE status = 'active'")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def add_account(username, password, email, proxy="", remark=""):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO accounts (username, password, email, proxy, remark, status, error_message) VALUES (?, ?, ?, ?, ?, 'active', '')",
        (username, password, email, proxy, remark)
    )
    conn.commit()
    conn.close()

def delete_account(username):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM accounts WHERE username = ?", (username,))
    conn.commit()
    conn.close()

def update_account_status(username, status, error_message="", last_used=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if last_used is None:
        last_used = datetime.now().isoformat()
    cursor.execute(
        "UPDATE accounts SET status = ?, error_message = ?, last_used = ? WHERE username = ?",
        (status, error_message, last_used, username)
    )
    conn.commit()
    conn.close()

# --- Remarks CRUD ---
def get_remarks():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT username, remark_name, is_highlight FROM remarks")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def add_remark(username, remark_name, is_highlight=0):
    # 标准化用户名，去除开头的 @ 符号
    clean_username = username.strip()
    if clean_username.startswith("@"):
        clean_username = clean_username[1:]
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO remarks (username, remark_name, is_highlight) VALUES (?, ?, ?)",
        (clean_username, remark_name, int(is_highlight))
    )
    conn.commit()
    conn.close()

def delete_remark(username):
    clean_username = username.strip()
    if clean_username.startswith("@"):
        clean_username = clean_username[1:]
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM remarks WHERE username = ?", (clean_username,))
    conn.commit()
    conn.close()

def clear_remarks():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM remarks")
    conn.commit()
    conn.close()

# --- Metrics CRUD ---
def add_metric(status, error_message="", account_used=""):
    conn = get_db_connection()
    cursor = conn.cursor()
    timestamp = datetime.now().isoformat()
    cursor.execute(
        "INSERT INTO metrics (timestamp, status, error_message, account_used) VALUES (?, ?, ?, ?)",
        (timestamp, status, error_message, account_used)
    )
    conn.commit()
    conn.close()

def get_metrics_24h():
    """
    获取过去24小时每小时的统计数据
    返回包含每小时成功和失败次数的列表，用于前端图表显示
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    time_limit = (datetime.now() - timedelta(hours=24)).isoformat()
    cursor.execute(
        "SELECT timestamp, status FROM metrics WHERE timestamp >= ? ORDER BY timestamp ASC",
        (time_limit,)
    )
    rows = cursor.fetchall()
    conn.close()
    
    # 按照 24 小时划分
    now = datetime.now()
    hourly_stats = {}
    for i in range(24):
        hour_dt = now - timedelta(hours=i)
        hour_str = hour_dt.strftime("%H:00")
        hourly_stats[hour_str] = {"success": 0, "failure": 0}
        
    for row in rows:
        try:
            row_time = datetime.fromisoformat(row["timestamp"])
            row_hour_str = row_time.strftime("%H:00")
            if row_hour_str in hourly_stats:
                if row["status"] == "success":
                    hourly_stats[row_hour_str]["success"] += 1
                else:
                    hourly_stats[row_hour_str]["failure"] += 1
        except Exception:
            continue
            
    # 转换为按时间正序排列的列表
    result_labels = []
    result_success = []
    result_failure = []
    
    for i in reversed(range(24)):
        hour_dt = now - timedelta(hours=i)
        hour_str = hour_dt.strftime("%H:00")
        result_labels.append(hour_str)
        result_success.append(hourly_stats[hour_str]["success"])
        result_failure.append(hourly_stats[hour_str]["failure"])
        
    return {
        "labels": result_labels,
        "success": result_success,
        "failure": result_failure
    }

# --- Duplicate Filter CRUD ---
def is_tweet_forwarded(tweet_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM forwarded_tweets WHERE tweet_id = ?", (tweet_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def mark_tweet_forwarded(tweet_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    timestamp = datetime.now().isoformat()
    cursor.execute(
        "INSERT OR IGNORE INTO forwarded_tweets (tweet_id, timestamp) VALUES (?, ?)",
        (tweet_id, timestamp)
    )
    conn.commit()
    conn.close()

# --- Pending Tweets CRUD ---
def add_pending_tweet(tweet_id, job_json):
    conn = get_db_connection()
    cursor = conn.cursor()
    timestamp = datetime.now().isoformat()
    cursor.execute(
        "INSERT OR REPLACE INTO pending_tweets (tweet_id, job_json, timestamp) VALUES (?, ?, ?)",
        (tweet_id, job_json, timestamp)
    )
    conn.commit()
    conn.close()

def get_all_pending_tweets():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT tweet_id, job_json FROM pending_tweets ORDER BY timestamp ASC")
    rows = cursor.fetchall()
    conn.close()
    return [{"tweet_id": row["tweet_id"], "job_json": row["job_json"]} for row in rows]

def remove_pending_tweet(tweet_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM pending_tweets WHERE tweet_id = ?", (tweet_id,))
    conn.commit()
    conn.close()

# --- 数据库自动清理历史数据 CRUD ---
def cleanup_old_data(days_threshold=3):
    """
    清理超过指定天数的历史去重数据和挂起数据，防止 SQLite 膨胀并加快查询速度
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 计算 3 天前截止时间的 ISO 8601 字符串
    cutoff_time = (datetime.now() - timedelta(days=days_threshold)).isoformat()
    
    # 1. 清理 3 天前的去重历史
    cursor.execute("DELETE FROM forwarded_tweets WHERE timestamp < ?", (cutoff_time,))
    deleted_forwarded = cursor.rowcount
    
    # 2. 清理 3 天前待发队列中意外残留的脏数据（防止死信任务无限期滞留在库中）
    cursor.execute("DELETE FROM pending_tweets WHERE timestamp < ?", (cutoff_time,))
    deleted_pending = cursor.rowcount
    
    conn.commit()
    conn.close()
    
    return deleted_forwarded, deleted_pending
