import websockets
import asyncio
import uuid
import json
import urllib.parse
import aiohttp
import sys
import random

# --- 設定 ---
CLIENT_ID = str(uuid.uuid4())
WORKFLOW_FILE = "default_workflow.json"

# 圖片尺寸配置
IMAGE_SIZES = {
    'square': (1024, 1024),
    'vertical': (832, 1216),
    'horizontal': (1216, 832)
}


# --- Progress Bar ---
def print_progress_bar(iteration, total, prefix='', suffix='', length=50, fill='█'):
    """
    顯示單行進度條（覆蓋上一行）
    """
    percent = f"{100 * (iteration / float(total)):.1f}"
    filled_length = int(length * iteration // total)
    bar = fill * filled_length + '-' * (length - filled_length)
    sys.stdout.write(f'\r{prefix} |{bar}| {percent}% {suffix}')
    sys.stdout.flush()
    if iteration >= total:
        print()  # 完成後換行


# --- 主任務函式 ---
async def get_image(positive_prompt, negative_prompt, server_address, size='vertical'):
    print("\n--- [DEBUG] 進入 get_image 函式 ---")
    print(f"[DEBUG] 圖片尺寸: {size} -> {IMAGE_SIZES.get(size, IMAGE_SIZES['vertical'])}")

    # === 讀取 workflow ===
    try:
        with open(WORKFLOW_FILE, 'r', encoding='utf-8') as f:
            prompt_workflow = json.load(f)
        print(f"[DEBUG] 工作流程檔案 '{WORKFLOW_FILE}' 已成功載入。")
    except Exception as e:
        return None, f"錯誤：讀取工作流程檔案失敗 - {e}"

    # === 尋找 prompt 節點和 empty latent 節點 ===
    pos_prompt_node_id = None
    neg_prompt_node_id = None
    empty_latent_node_id = None
    node_titles = {}

    for node_id, node_data in prompt_workflow.items():
        node_titles[node_id] = node_data.get("_meta", {}).get("title", f"Node {node_id}")
        title = node_data.get("_meta", {}).get("title", "")
        
        if title == "Positive Prompt Loader":
            pos_prompt_node_id = node_id
        elif title == "Negative Prompt Loader":
            neg_prompt_node_id = node_id
        elif title == "Empty latent":
            empty_latent_node_id = node_id

    if not pos_prompt_node_id or not neg_prompt_node_id:
        return None, "錯誤：找不到 'Positive Prompt Loader' 或 'Negative Prompt Loader' 節點。"
    
    if not empty_latent_node_id:
        return None, "錯誤：找不到 'Empty latent' 節點。"

    # === 更新提示詞 ===
    prompt_workflow[pos_prompt_node_id]["inputs"]["text"] = positive_prompt
    prompt_workflow[neg_prompt_node_id]["inputs"]["text"] = negative_prompt
    
    # === 更新圖片尺寸 ===
    width, height = IMAGE_SIZES.get(size, IMAGE_SIZES['vertical'])
    prompt_workflow[empty_latent_node_id]["inputs"]["width"] = width
    prompt_workflow[empty_latent_node_id]["inputs"]["height"] = height
    print(f"[DEBUG] 設定圖片尺寸: {width}x{height}")
    
    # === 設定隨機 seed（同步更新所有 seed 節點）===
    random_seed = random.randint(1, 4294967294)
    seed_nodes_updated = 0
    
    for node_id, node_data in prompt_workflow.items():
        if "seed" in node_data.get("inputs", {}):
            prompt_workflow[node_id]["inputs"]["seed"] = random_seed
            print(f"[DEBUG] 節點 '{node_titles.get(node_id, node_id)}' 設定 seed: {random_seed}")
            seed_nodes_updated += 1
    
    if seed_nodes_updated > 0:
        print(f"[DEBUG] 共更新了 {seed_nodes_updated} 個 seed 節點")
    else:
        print("[WARNING] 未找到任何 seed 節點，將使用工作流程中的預設值")
    
    payload = {"prompt": prompt_workflow, "client_id": CLIENT_ID}

    # === 先連接 WebSocket（在提交之前）===
    uri = f"ws://{server_address}/ws?clientId={CLIENT_ID}"
    print(f"[DEBUG] 連線到 WebSocket → {uri}")

    try:
        async with websockets.connect(uri) as websocket:
            # === 連接後再提交任務 ===
            submit_url = f"http://{server_address}/prompt"
            print(f"[DEBUG] 使用 HTTP POST 提交 prompt → {submit_url}")
            
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(submit_url, json=payload) as resp:
                        if resp.status != 200:
                            return None, f"ComfyUI 回傳錯誤狀態碼：{resp.status}"
                        response_data = await resp.json()
                        prompt_id = response_data.get("prompt_id")
                        print(f"[DEBUG] Prompt 已成功提交，prompt_id: {prompt_id}")
            except Exception as e:
                return None, f"錯誤：無法送出 prompt → {e}"

            # === 監聽 WebSocket 回應 ===
            current_node_title = ""
            last_node_title = None

            while True:
                msg = await websocket.recv()
                if isinstance(msg, str):
                    data = json.loads(msg)
                    msg_type = data.get("type")

                    # 處理 status 事件
                    if msg_type == "status":
                        status_data = data.get("data", {})
                        print(f"[DEBUG] 收到 status 事件: {status_data}")
                        continue

                    elif msg_type == "execution_start":
                        print("ComfyUI 任務開始執行。")

                    elif msg_type == "executing":
                        node_id = data["data"].get("node")
                        
                        # node 為 None 表示執行結束
                        if node_id is None:
                            print("\n[DEBUG] 收到 executing 事件，node=None，執行可能已結束")
                            continue
                            
                        current_node_title = node_titles.get(node_id, f"Node {node_id}")
                        if current_node_title != last_node_title:
                            print(f"\n正在執行節點: {current_node_title}")
                            last_node_title = current_node_title

                    elif msg_type == "progress":
                        d = data["data"]
                        print_progress_bar(
                            d["value"],
                            d["max"],
                            prefix=f"{current_node_title}",
                            suffix="完成"
                        )

                    elif msg_type == "executed":
                        node_id = data["data"].get("node")
                        output_data = data["data"].get("output", {})
                        
                        print(f"\n[DEBUG] 節點 {node_titles.get(node_id, node_id)} 執行完成")
                        
                        # 檢查是否有圖片輸出
                        if "images" in output_data:
                            print("\n圖片生成完畢！正在下載...")
                            img_info = output_data["images"][0]
                            img_bytes = await fetch_image(
                                img_info["filename"], 
                                img_info.get("subfolder", ""), 
                                img_info.get("type", "output"),
                                server_address
                            )
                            if img_bytes:
                                print("--- 任務結束 ---")
                                return img_bytes, None
                            else:
                                return None, "無法下載生成的圖片"

                    elif msg_type == "execution_error":
                        error_data = data.get("data", {})
                        print(f"[錯誤] ComfyUI 執行錯誤：{error_data}")
                        return None, f"ComfyUI 執行錯誤：{error_data}"

                    elif msg_type == "execution_cached":
                        print(f"[DEBUG] 某些節點使用快取")

                    else:
                        print(f"[DEBUG] 收到其他事件類型：{msg_type}")

    except websockets.exceptions.ConnectionClosed as e:
        return None, f"WebSocket 連接關閉：{e}"
    except Exception as e:
        return None, f"WebSocket 錯誤：{e}"


# --- 下載生成圖片 ---
async def fetch_image(filename, subfolder, file_type, server_address):
    """
    從 ComfyUI 下載生成的圖片
    """
    params = {"filename": filename, "type": file_type}
    if subfolder:
        params["subfolder"] = subfolder
    
    query_string = urllib.parse.urlencode(params)
    url = f"http://{server_address}/view?{query_string}"
    
    print(f"[DEBUG] 下載圖片：{url}")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.read()
                else:
                    print(f"[錯誤] 下載圖片失敗，狀態碼：{resp.status}")
                    return None
    except Exception as e:
        print(f"[錯誤] 下載圖片時發生例外：{e}")
        return None