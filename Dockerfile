# Dockerfile for VVVVVV (Vertex AI Proxy)
FROM python:3.11-slim

WORKDIR /app

# 1. 安装系统依赖（Playwright Chromium 所需）
RUN apt-get update && apt-get install -y \
    curl \
    wget \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libxss1 \
    libxtst6 \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

# 2. 拷贝 requirements.txt 并安装 Python 依赖
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir playwright

# 3. 安装 Playwright 浏览器（这是最大的层，应该缓存）
# 注意：必须在设置环境变量之前安装，或者不设置 PLAYWRIGHT_BROWSERS_PATH
RUN playwright install chromium

# 4. 拷贝应用代码（放在后面以利用缓存）
COPY main.py ./
COPY src/ ./src/
COPY static/ ./static/

# 5. 拷贝配置模板（用于初始化）
COPY config/ ./config-template/

# 6. 创建必要的目录并设置权限
RUN mkdir -p ./config && \
    chmod -R 777 ./config && \
    chmod -R 755 /app

# 7. 拷贝并设置 entrypoint 脚本
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# 暴露服务端口
EXPOSE 7860
EXPOSE 7861

# 设置环境变量
ENV PYTHONUNBUFFERED=1
# 不设置 PLAYWRIGHT_BROWSERS_PATH，让 Playwright 使用默认路径

# 设置 entrypoint 和默认命令
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "main.py"]