# database/connection.py
from .models import get_db_connection, init_db

# 该文件负责数据库连接和初始化暴露，保持业务解耦。
def check_connection():
    try:
        conn = get_db_connection()
        conn.execute("SELECT 1")
        conn.close()
        return True
    except Exception:
        return False
