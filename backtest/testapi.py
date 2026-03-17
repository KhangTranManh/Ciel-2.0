import os
import google.generativeai as genai
from dotenv import load_dotenv

def test_ciel_connection():
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    print(f"--- Kiểm tra kết nối Manas Ciel ---")
    if not api_key:
        print("[LỖI] Không tìm thấy GEMINI_API_KEY trong file .env!")
        return

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        
        print(f"[1/2] Đang gửi tín hiệu thử nghiệm tới model: {model_name}...")
        response = model.generate_content("Say 'Ciel System Online' if you can hear me.")
        
        if response.text:
            print(f"[2/2] Phản hồi từ Google: {response.text}")
            print("\n[THÀNH CÔNG] Kết nối tới Google AI hoàn hảo. Lỗi nằm ở cấu trúc Prompt hoặc LangChain.")
        else:
            print("[LỖI] Google trả về phản hồi rỗng không rõ nguyên nhân.")
            
    except Exception as e:
        print(f"\n[THẤT BẠI] Lỗi kết nối API:")
        print(f"Chi tiết: {str(e)}")
        print("\nGợi ý: Kiểm tra lại mạng Internet, VPN hoặc xem API Key có bị giới hạn vùng không.")

if __name__ == "__main__":
    test_ciel_connection()