import asyncio
import io
import paramiko
import posixpath

def _upload_sync(image_bytes, filename, host, port, username, password, remote_path):
    """
    同步執行 SCP 上傳操作。
    此函式設計為在 asyncio 的背景執行緒中執行。
    """
    try:
        # 建立 SSH 客戶端
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # 連接選項
        connect_kwargs = {
            'hostname': host,
            'port': port,
            'username': username,
        }
        
        # 優先使用密碼認證
        if password:
            connect_kwargs['password'] = password
            print(f"[SCP] 使用密碼認證")
        else:
            # 你也可以在這裡添加 SSH key 的邏輯
            return False, "未設定 SCP 認證方式 (需要密碼或 SSH key)"
        
        ssh.connect(**connect_kwargs)
        print(f"[SCP] 已連接到 {host}:{port}")
        
        # 建立 SFTP 客戶端
        sftp = ssh.open_sftp()
        
        # 確保遠端目錄存在
        try:
            sftp.stat(remote_path)
        except FileNotFoundError:
            print(f"[SCP] 遠端路徑 {remote_path} 不存在，嘗試建立...")
            sftp.mkdir(remote_path)

        # 組合完整的遠端檔案路徑
        remote_file = posixpath.join(remote_path, filename)
        print(f"[SCP] 準備上傳至: {remote_file}")
        
        # 將 bytes 上傳到遠端檔案
        with io.BytesIO(image_bytes) as file_obj:
            sftp.putfo(file_obj, remote_file)
        
        print(f"[SCP] 已成功上傳: {remote_file}")
        
        # 關閉連接
        sftp.close()
        ssh.close()
        
        return True, remote_file
        
    except paramiko.AuthenticationException:
        print("[SCP] 錯誤：SSH 認證失敗，請檢查用戶名和密碼。")
        return False, "SSH 認證失敗"
    except paramiko.SSHException as e:
        print(f"[SCP] 錯誤：SSH 連接失敗: {e}")
        return False, f"SSH 錯誤: {e}"
    except Exception as e:
        print(f"[SCP] 錯誤：上傳過程中發生未知錯誤: {e}")
        return False, f"上傳錯誤: {e}"


async def upload_image(image_bytes, filename, host, port, username, password, remote_path):
    """
    非同步地將圖片上傳到遠端伺服器。
    這是外部呼叫的主要函式。
    """
    try:
        loop = asyncio.get_event_loop()
        # 在背景執行緒中執行同步的 I/O 操作，避免阻塞主程式
        success, result = await loop.run_in_executor(
            None, 
            _upload_sync, 
            image_bytes, filename, host, port, username, password, remote_path
        )
        return success, result
    except Exception as e:
        print(f"[SCP] 呼叫上傳任務時發生錯誤: {e}")
        return False, str(e)