#!/bin/bash
# upgrade.sh - 一键无损升级与维护脚本
set -e

if [ "$EUID" -ne 0 ]; then
  echo "❌ 错误：请以 root 权限运行此脚本。"
  exit 1
fi

echo "=================================================="
echo "🔄 开始升级 X-TG 转发监控系统..."
echo "=================================================="

# 1. 停止运行中的服务 (允许服务处于未激活状态)
echo "⏱️ 正在暂停后台服务..."
systemctl stop taox || true

# 2. 备份核心数据
echo "💾 正在备份数据库与登录 Cookies..."
BACKUP_DIR="backups/backup_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

if [ -f "data.db" ]; then
  cp "data.db" "$BACKUP_DIR/"
  echo "   - 数据库已备份至: $BACKUP_DIR/data.db"
fi

if [ -d "cookies" ]; then
  cp -r "cookies" "$BACKUP_DIR/"
  echo "   - 账号 Cookies 已备份至: $BACKUP_DIR/cookies/"
fi

# 3. 拉取/覆盖最新代码 (如使用了 Git)
if [ -d ".git" ]; then
  echo "📥 检测到 Git 仓库，正在从 GitHub 拉取最新代码..."
  git pull
else
  echo "💡 提示：非 Git 部署，请确保你已经将最新的源码文件覆盖到了当前目录。"
fi

# 4. 升级 Python 依赖包 (清理冲突的 twikit/twifork 并全新拉取)
echo "📥 正在增量升级 Python 依赖包..."
if [ -d "venv" ]; then
  echo "🧹 正在彻底清理潜在冲突与残留的旧依赖包..."
  ./venv/bin/python -m pip uninstall -y twikit twifork || true
  rm -rf ./venv/lib/python3.*/site-packages/twikit
  rm -rf ./venv/lib/python3.*/site-packages/twikit-*.dist-info
  rm -rf ./venv/lib/python3.*/site-packages/twifork-*.dist-info
  
  ./venv/bin/python -m pip install --no-cache-dir -r requirements.txt --upgrade
else
  echo "⚠️ 未检测到虚拟环境，正在创建..."
  python3 -m venv venv
  ./venv/bin/python -m pip uninstall -y twikit twifork || true
  rm -rf ./venv/lib/python3.*/site-packages/twikit
  rm -rf ./venv/lib/python3.*/site-packages/twikit-*.dist-info
  rm -rf ./venv/lib/python3.*/site-packages/twifork-*.dist-info
  
  ./venv/bin/python -m pip install --no-cache-dir -r requirements.txt
fi

# 5. 验证依赖与应用程序导入情况，防止服务启动失败导致无限重启
echo "🔍 正在进行依赖与应用导入验证..."
if ! ./venv/bin/python -c "import main; print('main import ok, twikit patch loaded')"; then
  echo "❌ 错误：应用导入失败，请检查 Twikit 或热修复代码！"
  exit 1
fi

# 6. 重新载入系统服务并启动
echo "🚀 正在重新启动服务..."
systemctl daemon-reload
systemctl start taox

# 7. 验证服务是否成功运行
sleep 2
STATUS=$(systemctl is-active taox || true)
if [ "$STATUS" = "active" ]; then
  echo "=================================================="
  echo "🎉 升级完成！服务已重新上线，运行状态良好。"
  echo "=================================================="
else
  echo "❌ 警告：服务启动后检测到异常，请运行 'journalctl -u taox -n 50' 查看错误日志。"
fi
