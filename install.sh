#!/bin/bash
# install.sh - Debian 一键交互式安装脚本

# 强制以 root 用户运行
if [ "$EUID" -ne 0 ]; then
  echo "❌ 错误：请以 root 权限运行此脚本 (使用 sudo 或切换至 root 用户)。"
  exit 1
fi

echo "=================================================="
echo "🚀 欢迎使用 Twitter-to-TG 监控转发系统安装向导"
echo "=================================================="
echo ""

# 1. 交互式询问端口和管理员设置
read -p "1. 请输入 Web 后台控制台运行端口 (默认 8080): " WEB_PORT
WEB_PORT=${WEB_PORT:-8080}

read -p "2. 请输入后台管理员用户名 (默认 admin): " ADMIN_USER
ADMIN_USER=${ADMIN_USER:-admin}

# 循环提示输入密码，不允许为空
while true; do
  read -sp "3. 请输入后台管理员密码 (不能为空): " ADMIN_PASS
  echo ""
  if [ -n "$ADMIN_PASS" ]; then
    break
  else
    echo "❌ 密码不能为空，请重新输入！"
  fi
done

echo ""
echo "⚙️ 正在检测并安装系统依赖 (Python3, pip, venv)..."
# 2. 安装系统依赖
apt-get update
apt-get install -y python3 python3-pip python3-venv curl

# 3. 创建虚拟环境并安装 Python 依赖
PROJECT_DIR=$(pwd)
echo "📦 正在在当前目录 $PROJECT_DIR 创建 Python 虚拟环境..."
python3 -m venv venv
if [ $? -ne 0 ]; then
  echo "❌ 错误：创建 Python 虚拟环境失败，请检查 python3-venv 软件包。"
  exit 1
fi

echo "📥 正在安装 Python 依赖包 (twikit, fastapi, uvicorn)..."
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
if [ $? -ne 0 ]; then
  echo "❌ 错误：安装 Python 依赖包失败，请检查网络或代理设置。"
  exit 1
fi

# 4. 调用虚拟环境中的 Python 计算密码 Hash 并写入临时配置文件
echo "🔒 正在加密保存管理员账号信息..."
PASS_HASH=$(./venv/bin/python -c "import hashlib; print(hashlib.sha256('$ADMIN_PASS'.encode('utf-8')).hexdigest())")

# 写入临时初始化配置文件
cat <<EOF > init_config.json
{
  "admin_username": "$ADMIN_USER",
  "admin_password_hash": "$PASS_HASH"
}
EOF
chmod 600 init_config.json

# 5. 配置 systemd 系统服务以实现开机自启
echo "🛠️ 正在配置 systemd 开机自启服务 (taox.service)..."
SERVICE_FILE="/etc/systemd/system/taox.service"

cat <<EOF > $SERVICE_FILE
[Unit]
Description=Twitter to Telegram Monitoring and Forwarding System
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port $WEB_PORT
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 加载并启动服务
systemctl daemon-reload
systemctl enable taox.service
systemctl restart taox.service

# 6. 安装结果输出
echo ""
echo "=================================================="
echo "🎉 安装完成！系统已成功在后台运行。"
echo "=================================================="
echo "🌐 控制台地址: http://你的服务器IP:$WEB_PORT"
echo "👤 管理员账号: $ADMIN_USER"
echo "🔑 管理员密码: (你刚刚输入的密码)"
echo "--------------------------------------------------"
echo "💡 常用管理命令:"
echo "   - 查看运行状态: systemctl status taox"
echo "   - 重启监控服务: systemctl restart taox"
echo "   - 停止监控服务: systemctl stop taox"
echo "   - 查看实时日志: journalctl -u taox -f"
echo "=================================================="
