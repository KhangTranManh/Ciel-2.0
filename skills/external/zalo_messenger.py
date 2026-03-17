import os
from langchain_core.tools import tool

@tool

def send_zalo_message(target_phone: str, message: str) -> str:
    """Send a direct message via Zalo API. 
    CRITICAL RULE: ONLY execute this tool if the Master EXPLICITLY provides BOTH the target phone number and the message content in their CURRENT request. 
    DO NOT scavenge, guess, or reuse phone numbers or messages from previous chat history. If details are missing, you MUST ask the Master for them first."""
    
    try:
        from zlapi import ZaloAPI
        from zlapi.models import Message, ThreadType
        
        imei = os.getenv("ZALO_IMEI")
        raw_cookie = os.getenv("ZALO_COOKIE")
        
        if not all([imei, raw_cookie]):
            return "System Error: ZALO_IMEI or ZALO_COOKIE missing in .env vault."

        # Tự động nghiền nát chuỗi Cookie thô thành Dictionary cho zlapi
        cookies_dict = {}
        for item in raw_cookie.split(';'):
            if '=' in item:
                key, value = item.split('=', 1)
                cookies_dict[key.strip()] = value.strip()

        # ÉP BUỘC KHỞI TẠO: Thử mọi cách để vượt qua sự cẩu thả của thư viện zlapi
        client = None
        try:
            # Cách 1: Theo tài liệu Master cung cấp (Bản mới)
            client = ZaloAPI("dummy", "dummy", imei=imei, session_cookies=cookies_dict)
        except TypeError:
            try:
                # Cách 2: Theo cấu trúc thư viện cũ
                client = ZaloAPI("dummy", "dummy", imei=imei, cookies=cookies_dict)
            except TypeError:
                # Cách 3: Truyền thẳng vị trí tham số tàn nhẫn nhất
                client = ZaloAPI("dummy", "dummy", imei, cookies_dict)
        
        client.login()
        
        # Tìm UID của mục tiêu
        user_info = client.fetchPhoneNumber(target_phone)
        if not user_info or 'uid' not in user_info:
            return f"Failed to acquire target. Cannot find Zalo UID for phone: {target_phone}"
            
        target_uid = str(user_info['uid'])
        
        # Gửi tin nhắn ngầm
        msg_obj = Message(text=message)
        client.send(msg_obj, thread_id=target_uid, thread_type=ThreadType.USER)
        
        return f"Target acquired. Message successfully dispatched to {target_phone} via stealth API."
        
    except ImportError:
        return "System Error: Missing library. Master must run 'pip install zlapi'."
    except Exception as e:
        return f"Mission failed. Error encountered during execution: {str(e)}"