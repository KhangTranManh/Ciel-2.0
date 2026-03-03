Ciel_Project/
│
├── core/                       # LÕI XỬ LÝ (Không thay đổi thường xuyên)
│   ├── __init__.py
│   ├── agent_loop.py           # Vòng lặp suy luận chính (Nhận lệnh -> Suy nghĩ -> Hành động)
│   ├── llm_connector.py        # Kết nối với Ollama thông qua Langchain
│   └── memory_manager.py       # Quản lý ChromaDB (RAG) và trí nhớ ngắn hạn
│
├── skills/                     # KHO KỸ NĂNG (Nơi Ciel học thêm công cụ mới)
│   ├── __init__.py
│   ├── os_operations.py        # Kỹ năng đọc/ghi file, quản lý thư mục
│   ├── code_executor.py        # Kỹ năng tự động chạy code Python để test
│   └── web_research.py         # Kỹ năng tìm kiếm thông tin (thêm vào sau)
│
├── persona/                    # ĐỊNH HÌNH CÁI TÔI (Ego & Persona)
│   └── system_prompt.txt       # File text chứa toàn bộ "Điều răn" và tính cách của Ciel
│
├── ciel_data/                  # DỮ LIỆU ĐỘNG (Thư mục này nên đưa vào .gitignore)
│   ├── memory_bank/            # File database của ChromaDB
│   └── logs/                   # Chứa file log ghi lại mọi quyết định của Ciel
│
├── .env                        # Chứa cấu hình (như đã viết ở trên)
├── requirements.txt            # Danh sách thư viện Python
└── main.py                     # Cánh cửa khởi động hệ thống