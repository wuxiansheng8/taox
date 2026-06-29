# main.py
import os
import json
import logging
import asyncio
import collections
from logging.handlers import RotatingFileHandler
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from database.models import init_db
from database.crud import get_setting
from routes import auth, dashboard, accounts, remarks, settings
from routes.auth import get_current_user, hash_password
from services.scheduler import start_scheduler, stop_scheduler

# 1. 初始化系统运行日志记录
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("taox")

# 高效读取文件末尾 N 行的辅助函数，避免将整个大文件读入内存
def read_last_lines(filepath: str, lines_count: int = 100) -> list:
    if not os.path.exists(filepath):
        return []
    
    block_size = 4096
    lines = []
    
    with open(filepath, "rb") as f:
        try:
            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            
            buffer = bytearray()
            pointer = file_size
            
            while pointer > 0 and len(lines) < lines_count + 1:
                move_to = max(0, pointer - block_size)
                read_size = pointer - move_to
                
                f.seek(move_to)
                chunk = f.read(read_size)
                
                buffer = chunk + buffer
                pointer = move_to
                
                lines = buffer.split(b'\n')
        except Exception:
            with open(filepath, "r", encoding="utf-8") as fallback_f:
                all_lines = fallback_f.readlines()
                result_lines = [line.strip() for line in all_lines if line.strip()]
                return result_lines[-lines_count:]
                
    result_lines = []
    start_idx = 0 if pointer == 0 else 1
    
    for line_bytes in lines[start_idx:]:
        try:
            decoded_line = line_bytes.decode('utf-8').strip()
            if decoded_line:
                result_lines.append(decoded_line)
        except UnicodeDecodeError:
            continue
            
    return result_lines[-lines_count:]

# 内存环形缓冲区日志 (保留最新 1000 条，完全线程安全)
LOG_RING_BUFFER = collections.deque(maxlen=1000)
active_log_listeners = set()

class RingBufferLogHandler(logging.Handler):
    def __init__(self, loop):
        super().__init__()
        self.loop = loop

    def emit(self, record):
        try:
            log_entry = self.format(record)
            LOG_RING_BUFFER.append(log_entry)
            # 通过 asyncio loop 线程安全地派发消息给各个活动的监听队列
            for queue in list(active_log_listeners):
                try:
                    self.loop.call_soon_threadsafe(queue.put_nowait, log_entry)
                except Exception:
                    pass
        except Exception:
            self.handleError(record)

app = FastAPI(title="Twitter to TG Forwarder", version="1.0.0")

# 允许跨域（本地调试使用）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. 挂载高度解耦的路由模块
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(accounts.router)
app.include_router(remarks.router)
app.include_router(settings.router)

# 3. 挂载运行日志专属接口
@app.get("/api/logs", dependencies=[Depends(get_current_user)])
async def get_system_logs(lines: int = Query(100, ge=1, le=1000)):
    """
    前端读取后台运行日志的接口
    """
    try:
        recent_logs = read_last_lines(LOG_FILE, lines)
        return {"logs": recent_logs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取日志失败: {e}")

@app.get("/api/logs/stream")
async def stream_logs(current_user: dict = Depends(get_current_user)):
    """
    使用 Server-Sent Events (SSE) 实时推送后台运行日志，完全零磁盘 I/O 损耗
    """
    async def log_generator():
        queue = asyncio.Queue()
        # 1. 瞬间先推送内存中缓存的历史日志
        for historical_log in list(LOG_RING_BUFFER):
            yield f"data: {json.dumps({'log': historical_log})}\n\n"
            
        # 2. 将当前客户端的接收队列注册进活跃监听集合
        active_log_listeners.add(queue)
        
        try:
            while True:
                # 3. 阻塞等待新日志到来并发送
                log_entry = await queue.get()
                yield f"data: {json.dumps({'log': log_entry})}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            active_log_listeners.discard(queue)

    return StreamingResponse(
        log_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no"
        }
    )

@app.delete("/api/logs", dependencies=[Depends(get_current_user)])
async def clear_system_logs():
    """
    一键清空日志文件和内存日志缓冲区
    """
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write("")
        LOG_RING_BUFFER.clear() # 同时清空内存环形缓冲区
        logger.info("[系统配置] 运行日志已被管理员手动清空。")
        return {"message": "日志已清空"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"清空日志失败: {e}")

# 4. 在系统启动时初始化数据库并开启定时轮询任务
@app.on_event("startup")
async def startup_event():
    # 注册环形缓冲区 Handler，挂载到 Root logger 上，捕获系统所有模块输出
    loop = asyncio.get_running_loop()
    rb_handler = RingBufferLogHandler(loop)
    rb_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logging.getLogger().addHandler(rb_handler)

    # 检查是否有安装引导生成的临时配置文件
    init_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "init_config.json")
    
    admin_user = "admin"
    admin_pw_hash = hash_password("admin123") # 默认初始密码为 admin123
    
    if os.path.exists(init_config_path):
        try:
            with open(init_config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            admin_user = config.get("admin_username", admin_user)
            admin_pw_hash = config.get("admin_password_hash", admin_pw_hash)
            logger.info("[初始化] 检测到安装引导配置，正在导入系统密码...")
            # 删除临时文件保障安全
            os.remove(init_config_path)
        except Exception as e:
            logger.error(f"[初始化警告] 读取安装向导配置失败: {e}")
            
    # 初始化数据库
    init_db(admin_username=admin_user, admin_password_hash=admin_pw_hash)
    logger.info(f"[数据库] SQLite 初始化完成。管理员用户名: {admin_user}")
    
    # 获取系统是否开启了自动任务，启动后台监控
    # 默认如果配置了 TG 通信就开启自动调度，或者如果之前状态是开启的
    # 为了体验更好，默认启动扫描任务
    await start_scheduler()
    logger.info("[调度器] 后台推文扫描轮询服务启动成功。")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("[系统停机] 正在优雅停止后台扫描与发送服务...")
    await stop_scheduler()
    logger.info("[系统停机] 所有后台服务已安全退出。")

# 5. 托管网页前端静态资源
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
else:
    @app.get("/")
    async def index_fallback():
        return JSONResponse(content={"message": "前端资源 static 目录不存在，请检查部署"}, status_code=404)
