# Vertex AI Proxy

通过本地 OpenAI 兼容接口调用 Google Vertex AI 模型，利用浏览器会话认证。

## 功能

- OpenAI 格式 API，兼容 NextChat、Chatbox、LobeChat 等客户端
- 三种凭证模式：headless（自动化浏览器）、headful（浏览器脚本）、manual（手动）
- 自动检测 Token 过期并刷新
- 思考模式（-low/-high 后缀）
- 图片生成（-1k/-2k/-4k 后缀）
- SD WebUI 兼容 API
- 可选 GUI 界面

## 快速开始

### 方式一：Docker 部署（推荐）
#### 使用 GitHub Container Registry 镜像（最简单）

直接拉取预构建的镜像：

```bash
# 拉取最新镜像
docker pull ghcr.io/YOUR_USERNAME/vvv:latest

# 运行容器
docker run -d \
  --name vvvvvv-proxy \
  -p 7860:7860 \
  -p 7861:7861 \
  -v $(pwd)/config:/app/config \
  ghcr.io/YOUR_USERNAME/vvv:latest

# 查看日志
docker logs -f vvvvvv-proxy
```

> **注意**：将 `YOUR_USERNAME` 替换为你的 GitHub 用户名。镜像会在每次推送到 main/master 分支时自动构建。


#### 使用 Docker Compose

```bash
# 构建并启动容器
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止容器
docker-compose down
```

#### 使用 Docker 命令

```bash
# 构建镜像
docker build -t vvvvvv-proxy .

# 运行容器
docker run -d \
  --name vvvvvv-proxy \
  -p 7860:7860 \
  -p 7861:7861 \
  -v $(pwd)/config:/app/config \
  vvvvvv-proxy

# 查看日志
docker logs -f vvvvvv-proxy

# 停止容器
docker stop vvvvvv-proxy
docker rm vvvvvv-proxy
```

服务地址：
- API: http://127.0.0.1:7860
- WebSocket: ws://127.0.0.1:7861（headful 模式）

### 方式二：本地安装

#### 安装

```bash
pip install -r requirements.txt
```

headless 模式需额外安装：

```bash
pip install playwright
playwright install chromium
```

#### 启动

```bash
python main.py
```

服务地址：
- API: http://127.0.0.1:7860
- WebSocket: ws://127.0.0.1:7861（headful 模式）

### 客户端配置

| 配置项 | 值 |
|--------|-----|
| Base URL | http://127.0.0.1:7860/v1 |
| API Key | 任意值 |
| Model | gemini-2.5-pro 等 |

## 凭证模式

### headless（推荐）

自动化浏览器获取凭证，无需手动操作。

配置 config/config.json：

```json
{
  "credential_mode": "headless",
  "headless": {
    "show_browser": false,
    "auto_refresh_interval": 180
  }
}
```

设置 show_browser 为 true 可查看浏览器窗口。

### headful

使用 Tampermonkey 脚本获取凭证。

1. 安装 Tampermonkey 扩展
2. 添加 scripts/vertex-ai-harvester.user.js 脚本
3. 打开 Vertex AI Studio 并发送一条消息

### manual

使用已保存的凭证文件 config/credentials.json。

## API 密钥验证

为了保护你的反代服务，可以启用 API 密钥验证。

### 配置密钥

通过环境变量 `API_KEYS` 设置密钥（逗号分隔多个密钥）：

#### Docker Compose 方式

编辑 `docker-compose.yml`：

```yaml
environment:
  - PYTHONUNBUFFERED=1
  - API_KEYS=sk-your-secret-key-1,sk-your-secret-key-2
```

#### Docker 命令方式

```bash
docker run -d \
  -e API_KEYS=sk-your-secret-key-1,sk-your-secret-key-2 \
  -p 7860:7860 \
  vvvvvv-proxy
```

#### 本地运行方式

```bash
export API_KEYS=sk-your-secret-key-1,sk-your-secret-key-2
python main.py
```

- **不设置**：不验证，任何人都可以访问
- **设置密钥**：只有使用正确密钥的请求才能访问

### 使用密钥

客户端配置时，在 API Key 字段填入你设置的密钥：

| 配置项 | 值 |
|--------|-----|
| Base URL | http://127.0.0.1:7860/v1 |
| API Key | sk-your-secret-key-1 |
| Model | gemini-2.5-pro 等 |

### 密钥格式

支持两种格式：
- `Authorization: Bearer sk-xxx`（推荐）
- `Authorization: sk-xxx`

### 跳过验证的端点

以下端点不需要密钥：
- `/health` - 健康检查
- `/` - 根路径
- `/v1/models` - 模型列表

## 配置说明

### config/config.json

| 参数 | 说明 | 默认值 |
|------|------|--------|
| credential_mode | 凭证模式 | headful |
| enable_sd_api | 启用 SD WebUI API | true |
| enable_gui | 启用 GUI 窗口 | false |
| headless.show_browser | 显示浏览器 | false |
| headless.auto_refresh_interval | 刷新间隔（秒） | 180 |

### config/models.json

配置可用模型列表和别名映射。

支持的模型：
- gemini-2.5-pro
- gemini-2.5-flash-image
- gemini-2.0-flash-exp
- gemini-1.5-pro / gemini-1.5-flash
- gemini-3-pro-preview（思考模式）
- gemini-3-pro-image-preview（图片生成）

## 高级用法

### 思考模式

在模型名后添加后缀：
- -low：8K token 预算
- -high：32K token 预算

示例：gemini-3-pro-preview-low

### 图片生成

在模型名后添加后缀：
- -1k：1024x1024
- -2k：2048x2048
- -4k：4096x4096

示例：gemini-3-pro-image-preview-2k

## 项目结构

```
├── main.py                 # 入口
├── config/
│   ├── config.json         # 主配置
│   ├── models.json         # 模型配置
│   └── credentials.json    # 凭证存储（自动生成）
├── scripts/
│   └── vertex-ai-harvester.user.js  # Tampermonkey 脚本
└── src/
    ├── api/                # API 路由
    ├── core/               # 核心模块
    ├── headless/           # 自动化浏览器
    └── stream/             # 流式处理
```

## Docker 部署说明

### 配置文件持久化

Docker 容器会将 [`config`](VVVVVV/config) 目录挂载为卷，确保配置和凭证持久化：

```yaml
volumes:
  - ./config:/app/config
```

### 环境变量

可以通过环境变量覆盖配置：

```bash
docker run -d \
  -e PYTHONUNBUFFERED=1 \
  -p 7860:7860 \
  vvvvvv-proxy
```

### 注意事项

1. **headless 模式**：Docker 容器默认支持 headless 模式，已预装 Playwright 和 Chromium
2. **配置文件**：首次运行前，确保 [`config/config.json`](VVVVVV/config/config.json:1) 已正确配置
3. **凭证管理**：凭证文件会自动保存到挂载的 config 目录
4. **网络访问**：容器内服务监听 0.0.0.0，可通过宿主机 IP 访问

## 常见问题

**Token 过期提示**

保持 Vertex AI Studio 页面打开（headful 模式）或等待自动刷新（headless 模式）。

**浏览器脚本无响应**

确认在 console.cloud.google.com/vertex-ai 页面，脚本已启用。

**局域网访问**

服务监听 0.0.0.0，可使用本机局域网 IP 访问。

**Docker 容器无法启动**

检查端口是否被占用，或查看容器日志：
```bash
docker logs vvvvvv-proxy
```

## 免责声明

仅供学习研究，请遵守 Google Cloud Platform 服务条款。