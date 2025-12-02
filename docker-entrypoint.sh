#!/bin/bash
set -e

# 初始化 config 目录
if [ ! -f "/app/config/config.json" ]; then
    echo "📝 初始化配置文件..."
    cp -n /app/config-template/* /app/config/ 2>/dev/null || true
fi

# 清理旧的备份文件（超过7天）
echo "🧹 清理旧备份文件..."
find /app/config -name "*.bak*" -type f -mtime +7 -delete 2>/dev/null || true

# 确保 config 目录有正确的权限
chmod -R 777 /app/config

echo "✅ 配置目录已就绪"

# 执行主命令
exec "$@"