# 使用 uv 管理專案

本專案已配置使用 `uv` 作為套件管理工具。

## 安裝 uv

如果你還沒有安裝 uv，請執行：

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# 或使用 Homebrew
brew install uv
```

## 專案設置

### 1. 同步依賴

```bash
uv sync
```

此命令會：
- 創建虛擬環境（如果不存在）
- 安裝 `pyproject.toml` 中定義的所有依賴

### 2. 配置環境變數

確保你有 `.env` 文件，包含以下內容：

```env
DISCORD_TOKEN=你的_Discord_Bot_Token
COMFYUI_SERVER_ADDRESS=你的_ComfyUI_伺服器地址
```

## 運行專案

### 方法 1：使用 uv run（推薦）

```bash
uv run bot
```

這會直接執行 `pyproject.toml` 中定義的 `bot` 腳本入口。

### 方法 2：使用 uv run python

```bash
uv run python bot.py
```

### 方法 3：激活虛擬環境後執行

```bash
# 激活虛擬環境
source .venv/bin/activate  # macOS/Linux

# 運行 bot
python bot.py
# 或
bot

# 退出虛擬環境
deactivate
```

## 添加新依賴

```bash
# 添加運行時依賴
uv add 套件名稱

# 添加開發依賴
uv add --dev 套件名稱
```

## 移除依賴

```bash
uv remove 套件名稱
```

## 更新依賴

```bash
# 更新所有依賴
uv sync --upgrade

# 更新特定套件
uv add 套件名稱@latest
```

## 鎖定依賴版本

uv 會自動生成 `uv.lock` 文件來鎖定精確的依賴版本，確保團隊成員使用相同的依賴版本。

## 其他有用的命令

```bash
# 查看已安裝的套件
uv pip list

# 檢查 pyproject.toml 的語法
uv --version

# 清理虛擬環境
rm -rf .venv
uv sync  # 重新創建
```

## 優勢

使用 uv 相比傳統的 pip + requirements.txt：

✅ **極速安裝** - 比 pip 快 10-100 倍
✅ **依賴解析** - 自動解決依賴衝突
✅ **一致性** - 通過 uv.lock 確保環境一致
✅ **簡單管理** - 統一的依賴管理介面
✅ **腳本入口** - 可定義多個命令入口點

## 疑難排解

如果遇到問題：

1. 確認 uv 已正確安裝：`uv --version`
2. 刪除虛擬環境重新安裝：`rm -rf .venv && uv sync`
3. 檢查 Python 版本：`python --version`（需要 >= 3.8）
4. 確認 `.env` 文件存在且配置正確

