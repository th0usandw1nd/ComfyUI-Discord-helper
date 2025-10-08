import discord
from discord.ext import commands
from discord import app_commands
import io
import os
import json
import asyncio
from dotenv import load_dotenv
from api import get_image
from scp_uploader import *
from collections import deque
import paramiko
import posixpath
from datetime import datetime

# --- 設定 ---
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
COMFYUI_SERVER_ADDRESS = os.getenv("COMFYUI_SERVER_ADDRESS")

# --- 可選: SCP 設定 --- 
SCP_ENABLED = os.getenv("SCP_ENABLED", "false").lower() == "true"
#SCP_HOST = os.getenv("SCP_HOST")
#SCP_PORT = int(os.getenv("SCP_PORT"))
#SCP_USERNAME = os.getenv("SCP_USERNAME")
#SCP_PASSWORD = os.getenv("SCP_PASSWORD")
#SCP_REMOTE_PATH = os.getenv("SCP_REMOTE_PATH")

# --- 提示詞檔案處理---
PROMPTS_FILE = "user_prompts.json"
def load_prompts(file_path):
    if not os.path.exists(file_path):
        return {}
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return {int(k): v for k, v in data.items()}
    except (json.JSONDecodeError, IOError) as e:
        print(f"[提示詞] 載入 {file_path} 失敗: {e}，將使用空設定")
        return {}

def _save_prompts_sync(file_path, data):
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

async def save_prompts(file_path, data):
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _save_prompts_sync, file_path, data)
        print(f"[提示詞] 已成功儲存提示詞到 {file_path}")
    except Exception as e:
        print(f"[提示詞] 儲存提示詞失敗: {e}")

# 用於儲存每個使用者的提示詞
user_prompts = load_prompts(PROMPTS_FILE)

# 圖片尺寸選項
IMAGE_SIZES = {
    'square': (1024, 1024),
    'vertical': (832, 1216),
    'horizontal': (1216, 832)
}

DEFAULT_POSITIVE_PROMPT = """Hatsune Miku,limited palette,black background,colorful,vibrant,glowing outline,neon,blacklight,looking at viewer, masterpiece, very aesthetic"""

DEFAULT_NEGATIVE_PROMPT = """worst quality,bad quality,bad hands,very displeasing,extra digit,fewer digits,jpeg artifacts,signature,username,reference,mutated,lineup,manga,comic,disembodied,turnaround,2koma,4koma,monster,text,bad foreshortening,,logo,bad anatomy,bad perspective,bad proportions,artistic error,anatomical nonsense,amateur,out of frame,multiple views,"""

MAX_BATCH_SIZE = 4  # 最大批次生成數量

# --- 佇列系統 ---
class GenerationQueue:
    def __init__(self):
        self.queue = deque()
        self.processing = False
        self.current_task = None
    
    def add_request(self, interaction, positive, negative, batch_count, size):
        """將請求加入佇列"""
        request = {
            'interaction': interaction,
            'positive': positive,
            'negative': negative,
            'batch_count': batch_count,
            'size': size,
            'user_id': interaction.user.id,
            'user_name': interaction.user.display_name
        }
        self.queue.append(request)
        return len(self.queue)  # 返回佇列位置
    
    def get_queue_position(self, user_id):
        """取得特定用戶在佇列中的位置"""
        for idx, req in enumerate(self.queue):
            if req['user_id'] == user_id:
                return idx + 1
        return 0
    
    def get_queue_info(self):
        """取得佇列資訊"""
        if self.processing and self.current_task:
            current_user = self.current_task.get('user_name', 'Unknown')
            batch_info = self.current_task.get('batch_count', 1)
            waiting = len(self.queue)
            return f"正在處理: {current_user} (x{batch_info}) | 等待中: {waiting} 個請求"
        elif len(self.queue) > 0:
            return f"等待中: {len(self.queue)} 個請求"
        else:
            return "佇列空閒"

# --- 建立全域佇列 ---
generation_queue = GenerationQueue()

# --- Discord Bot 設定 ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="unused_prefix_", intents=intents) # <--- 新的

@bot.event
async def on_ready():
    print(f'[DEBUG] Bot 已登入為 {bot.user}')

    try:
        synced = await bot.tree.sync()
        print(f"[DEBUG] 已同步 {len(synced)} 個指令")
    except Exception as e:
        print(f"[DEBUG] 同步指令失敗: {e}")
    
    if SCP_ENABLED:
        print(f"[SCP] 功能已啟用")
        #print(f"[SCP] 目標: {SCP_USERNAME}@{SCP_HOST}:{SCP_PORT}")
        #print(f"[SCP] 遠端路徑: {SCP_REMOTE_PATH}")
    else:
        print(f"[SCP] 功能未啟用")
    
    bot.loop.create_task(process_queue())


async def process_queue():
    print("[佇列系統] 已啟動")
    while True:
        if len(generation_queue.queue) > 0 and not generation_queue.processing:
            generation_queue.processing = True
            request = generation_queue.queue.popleft()
            generation_queue.current_task = request
            
            batch_info = f" (批次: {request['batch_count']} 張)" if request['batch_count'] > 1 else ""
            size_info = f" [{request['size']}]"
            print(f"[佇列系統] 開始處理 {request['user_name']} 的請求{batch_info}{size_info}")
            
            try:
                await execute_generation(request)
            except Exception as e:
                print(f"[佇列系統] 處理請求時發生錯誤: {e}")
                try:
                    await request['interaction'].response.send_message(f"❌ 處理請求時發生錯誤: {str(e)}")
                except:
                    pass
            
            generation_queue.processing = False
            generation_queue.current_task = None
            print(f"[佇列系統] 完成處理 {request['user_name']} 的請求")
        
        await asyncio.sleep(0.5)  # 每 0.5 秒檢查一次佇列


async def execute_generation(request):
    interaction = request['interaction']
    positive = request['positive']
    negative = request['negative']
    batch_count = request['batch_count']
    size = request['size']
    
    # 判斷是否使用預設值
    user_settings = user_prompts.get(request['user_id'], {})
    is_default_pos = "(預設)" if 'positive' not in user_settings else ""
    is_default_neg = "(預設)" if 'negative' not in user_settings else ""
    
    # 構建提示詞顯示文字
    batch_info = f" (共 {batch_count} 張)" if batch_count > 1 else ""
    size_display = f"**尺寸**: {size}\n"
    prompt_display = (
        f"{size_display}"
        f"**正向 {is_default_pos}**:\n```{positive}```\n"
        f"**負向 {is_default_neg}**:\n```{negative}```"
    )
    
    # 初始訊息
    initial_text = f"⏳ 開始生成圖片{batch_info}...\n\n{prompt_display}"
    message = await interaction.followup.send(initial_text)
    
    # 創建停止事件
    stop_event = asyncio.Event()
    
    # 啟動背景動畫任務
    animation_task = asyncio.create_task(
        update_status_message(message, prompt_display, stop_event, batch_count)
    )
    
    generated_images = []
    
    try:
        # 循環生成多張圖片
        for i in range(batch_count):
            if batch_count > 1:
                print(f"[生成] 正在生成第 {i+1}/{batch_count} 張圖片...")
            
            image_bytes, error_message = await get_image(positive, negative, COMFYUI_SERVER_ADDRESS, size)
            
            if error_message:
                stop_event.set()
                await animation_task
                await message.edit(content=f"{interaction.user.mention} ❌ 生成失敗（第 {i+1}/{batch_count} 張）：{error_message}\n\n{prompt_display}")
                return
            
            if image_bytes:
                generated_images.append(image_bytes)
                
                # 如果啟用 SCP，立即上傳圖片
                if SCP_ENABLED:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"gen_{interaction.user.id}_{timestamp}_{i+1}.png"
                    #success, result = await upload_image(image_bytes, filename, SCP_HOST, SCP_PORT, SCP_USERNAME, SCP_PASSWORD, SCP_REMOTE_PATH)
                    #if success:
                        #print(f"[SCP] 圖片已上傳: {result}")
                    #else:
                        #print(f"[SCP] 上傳失敗: {result}")
            else:
                stop_event.set()
                await animation_task
                await message.edit(content=f"{interaction.user.mention} ❌ 生成失敗（第 {i+1}/{batch_count} 張），無法從 ComfyUI 獲取圖片數據。\n\n{prompt_display}")
                return
        
        # 停止動畫
        stop_event.set()
        await animation_task
        
        # 發送所有生成的圖片
        if generated_images:
            user_mention = interaction.user.mention
            
            if len(generated_images) == 1:
                picture = discord.File(io.BytesIO(generated_images[0]), filename='generated_image.png')
                await message.edit(content=f"{user_mention} ✅ 圖片生成完畢！\n\n{prompt_display}", attachments=[picture])
            else:
                files = [
                    discord.File(io.BytesIO(img), filename=f'generated_image_{i+1}.png')
                    for i, img in enumerate(generated_images)
                ]
                await message.edit(content=f"{user_mention} ✅ 圖片生成完畢！(共 {len(generated_images)} 張)\n\n{prompt_display}", attachments=files)
        else:
            await message.edit(content=f"{interaction.user.mention} ❌ 生成失敗，沒有獲取到任何圖片。\n\n{prompt_display}")
    
    except Exception as e:
        stop_event.set()
        try:
            await animation_task
        except:
            pass
        await message.edit(content=f"{interaction.user.mention} ❌ 發生錯誤：{str(e)}\n\n{prompt_display}")
        raise


async def update_status_message(message, prompt_text, stop_event, batch_count=1):
    """
    背景任務：定期更新訊息以顯示動畫效果（保留提示詞資訊）
    """
    animations = ["⏳", "⌛", "⏳", "⌛"]
    dots = ["", ".", "..", "..."]
    counter = 0
    
    batch_info = f" (共 {batch_count} 張)" if batch_count > 1 else ""
    
    try:
        while not stop_event.is_set():
            animation = animations[counter % len(animations)]
            dot = dots[counter % len(dots)]
            status_text = f"{animation} 正在生成圖片{batch_info}，請稍候{dot}\n\n{prompt_text}"
            await message.edit(content=status_text)
            counter += 1
            await asyncio.sleep(1.5)
    except discord.errors.NotFound:
        pass
    except Exception as e:
        print(f"更新狀態訊息時發生錯誤: {e}")


@bot.tree.command(name="positive", description="設定你的正向提示詞")
@app_commands.describe(prompt="你的正向提示詞，例如: masterpiece, 1girl")
async def set_positive(interaction: discord.Interaction, prompt: str):
    user_id = interaction.user.id
    if user_id not in user_prompts:
        user_prompts[user_id] = {}
    user_prompts[user_id]['positive'] = prompt
    await save_prompts(PROMPTS_FILE, user_prompts)
    await interaction.response.send_message(f"**{interaction.user.display_name}**的正向提示詞已設定為：\n```{prompt}```", ephemeral=True)

@bot.tree.command(name="positiveadd", description="加入提示詞到正向提示詞。若未設定，則會添加到預設值中。")
@app_commands.describe(prompt="你想新增的提示詞，例如: solo, full body,")
async def positive_add(interaction: discord.Interaction, prompt: str):
    user_id = interaction.user.id
    current_prompt = user_prompts.get(user_id, {}).get('positive')
    if current_prompt:
        base_prompt = current_prompt
    else:
        base_prompt = DEFAULT_POSITIVE_PROMPT
    clean_base = base_prompt.strip()
    clean_prompt = prompt.strip()
    if clean_base and not clean_base.endswith(','):
        new_prompt = f"{clean_base}, {clean_prompt}"
    else:
        new_prompt = f"{clean_base} {clean_prompt}"
    if user_id not in user_prompts:
        user_prompts[user_id] = {}
    user_prompts[user_id]['positive'] = new_prompt
    await save_prompts(PROMPTS_FILE, user_prompts)
    await interaction.response.send_message(f"**{interaction.user.display_name}**的正向提示詞已更新為：\n```{new_prompt}```", ephemeral=True)


@bot.tree.command(name="negative", description="設定你的負向提示詞")
@app_commands.describe(prompt="你的負向提示詞，例如: worst quality, ugly")
async def set_negative(interaction: discord.Interaction, prompt: str):
    user_id = interaction.user.id
    if user_id not in user_prompts:
        user_prompts[user_id] = {}
    user_prompts[user_id]['negative'] = prompt
    await save_prompts(PROMPTS_FILE, user_prompts)
    await interaction.response.send_message(f"**{interaction.user.display_name}**的負向提示詞已設定為：\n```{prompt}```", ephemeral=True)

@bot.tree.command(name="negativeadd", description="加入提示詞到負向提示詞。若未設定，則會添加到預設值中。")
@app_commands.describe(prompt="你想新增的提示詞，例如: text, watermark,")
async def negative_add(interaction: discord.Interaction, prompt: str):
    user_id = interaction.user.id
    current_prompt = user_prompts.get(user_id, {}).get('negative')
    if current_prompt:
        base_prompt = current_prompt
    else:
        base_prompt = DEFAULT_NEGATIVE_PROMPT
    clean_base = base_prompt.strip()
    clean_prompt = prompt.strip()
    if clean_base and not clean_base.endswith(','):
        new_prompt = f"{clean_base}, {clean_prompt}"
    else:
        new_prompt = f"{clean_base} {clean_prompt}"
    if user_id not in user_prompts:
        user_prompts[user_id] = {}
    user_prompts[user_id]['negative'] = new_prompt
    await save_prompts(PROMPTS_FILE, user_prompts)
    await interaction.response.send_message(f"**{interaction.user.display_name}**的負向提示詞已更新為：\n```{new_prompt}```", ephemeral=True)


@bot.tree.command(name="checkpositive", description="檢查你目前設定的正向提示詞")
async def check_positive(interaction: discord.Interaction):
    user_id = interaction.user.id
    if user_id in user_prompts and 'positive' in user_prompts[user_id]:
        positive_prompt = user_prompts[user_id]['positive']
        await interaction.response.send_message(f"**{interaction.user.display_name}**目前自訂的正向提示詞是：\n```{positive_prompt}```", ephemeral=True)
    else:
        await interaction.response.send_message(f"**{interaction.user.display_name}**尚未使用 `/positive` 設定，將使用**預設**正向提示詞：\n```{DEFAULT_POSITIVE_PROMPT}```", ephemeral=True)

@bot.tree.command(name="checknegative", description="檢查你目前設定的負向提示詞")
async def check_negative(interaction: discord.Interaction):
    user_id = interaction.user.id
    if user_id in user_prompts and 'negative' in user_prompts[user_id]:
        negative_prompt = user_prompts[user_id]['negative']
        await interaction.response.send_message(f"**{interaction.user.display_name}**目前自訂的負向提示詞是：\n```{negative_prompt}```", ephemeral=True)
    else:
        await interaction.response.send_message(f"**{interaction.user.display_name}**尚未使用 `/negative` 設定，將使用**預設**負向提示詞：\n```{DEFAULT_NEGATIVE_PROMPT}```", ephemeral=True)


@bot.tree.command(name="gen", description="開始生成圖片")
@app_commands.describe(
    count="要生成的圖片數量 (1-4)",
    size="選擇圖片的尺寸"
)
@app_commands.choices(size=[
    discord.app_commands.Choice(name="直式 (vertical)", value="vertical"),
    discord.app_commands.Choice(name="方形 (square)", value="square"),
    discord.app_commands.Choice(name="橫式 (horizontal)", value="horizontal"),
])
async def generate(interaction: discord.Interaction, count: app_commands.Range[int, 1, 4], size: str = 'vertical'):
    user_id = interaction.user.id
    
    await interaction.response.defer()
    
    user_settings = user_prompts.get(user_id, {})
    positive = user_settings.get('positive', DEFAULT_POSITIVE_PROMPT)
    negative = user_settings.get('negative', DEFAULT_NEGATIVE_PROMPT)
    
    position = generation_queue.add_request(interaction, positive, negative, count, size)
    
    batch_info = f" (x{count} 張)" if count > 1 else ""
    size_info = f" [{size}]"
    
    if position == 1 and not generation_queue.processing:
        await interaction.followup.send(f"**{interaction.user.display_name}** 的請求已收到{batch_info}{size_info}，立即開始處理！")
    else:
        await interaction.followup.send(
            f"**{interaction.user.display_name}** 的請求已加入佇列{batch_info}{size_info}\n"
            f"你的位置：第 **{position}** 位\n"
            f"ℹ️ {generation_queue.get_queue_info()}"
        )


@bot.tree.command(name="queue", description="查看目前的佇列狀態")
async def check_queue(interaction: discord.Interaction):
    user_id = interaction.user.id
    position = generation_queue.get_queue_position(user_id)
    
    info = generation_queue.get_queue_info()
    
    if position > 0:
        await interaction.response.send_message(
            f"**佇列狀態**\n"
            f"你的位置：第 **{position}** 位\n"
            f"{info}",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(f"**佇列狀態**\n{info}\n\n你目前沒有請求在佇列中。", ephemeral=True)


@bot.tree.command(name="cancel", description="取消你在佇列中的請求")
async def cancel_request(interaction: discord.Interaction):
    user_id = interaction.user.id
    
    initial_length = len(generation_queue.queue)
    generation_queue.queue = deque([req for req in generation_queue.queue if req['user_id'] != user_id])
    removed = initial_length - len(generation_queue.queue)
    
    if removed > 0:
        await interaction.response.send_message(f"✅ 已取消你的 **{removed}** 個請求")
    else:
        if generation_queue.current_task and generation_queue.current_task['user_id'] == user_id:
            await interaction.response.send_message("⚠️ 你的請求正在處理中，無法取消")
        else:
            await interaction.response.send_message("ℹ️ 你沒有在佇列中的請求")


@bot.tree.command(name="help", description="顯示所有可用指令的說明")
async def comfy_help(interaction: discord.Interaction):
    help_embed = discord.Embed(
        title="ComfyUI小助手 指令說明",
        description="以下是所有可用的指令列表:",
        color=discord.Color.blue()
    )
    
    # 圖片生成相關
    help_embed.add_field(
        name="**圖片生成**",
        value=(
            "`/gen [數量] [尺寸]`\n"
            "生成圖片（預設 1 張 vertical）\n"
            "尺寸選項:\n"
            "  • `square` - 正方形 (1024x1024)\n"
            "  • `vertical` - 直式 (832x1216) [預設]\n"
            "  • `horizontal` - 橫式 (1216x832)\n"
            "範例：`!gen` 或 `!gen 3 square`\n\n"
        ),
        inline=False
    )
    
    # 提示詞設定
    help_embed.add_field(
        name="**提示詞設定**",
        value=(
            "`/positive <提示詞>`\n"
            "設定你的正向提示詞\n"
            "範例：`/positive masterpiece, 1girl, smile`\n\n"
            "`/negative <提示詞>`\n"
            "設定你的負向提示詞\n"
            "範例：`/negative bad quality, ugly`\n\n"
        ),
        inline=False
    )
    
    # 查看提示詞
    help_embed.add_field(
        name="**查看提示詞**",
        value=(
            "`/checkpositive`\n"
            "查看你目前的正向提示詞\n\n"
            "`/checknegative`\n"
            "查看你目前的負向提示詞\n\n"
        ),
        inline=False
    )
    
    # 佇列管理
    help_embed.add_field(
        name="**佇列管理**",
        value=(
            "`/queue`\n"
            "查看目前的佇列狀態和你的位置\n\n"
            "`/cancel`\n"
            "取消你在佇列中的請求\n\n"
        ),
        inline=False
    )
    
    # 其他資訊
    help_embed.add_field(
        name="**ℹ️ 重要提示**",
        value=(
            "• 每個用戶的提示詞設定是**獨立**的\n"
            "• 如果未設定提示詞，將使用預設值\n"
            f"• 批次生成上限為 **{MAX_BATCH_SIZE}** 張\n"
            "• 佇列系統會依序處理每個請求\n"
            "• 預設圖片尺寸為 vertical (832x1216)\n"
        ),
        inline=False
    )
    
    help_embed.set_footer(text="💡 使用 !comfyhelp 隨時查看此說明")
    
    await interaction.response.send_message(embed=help_embed)


# --- 運行 Bot ---
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("錯誤：找不到 Discord Bot Token。請確保你的 .env 檔案中已設定 DISCORD_TOKEN。")
    else:
        bot.run(DISCORD_TOKEN)