from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    api_key=os.getenv("VILAO_KEY"),
    base_url=os.getenv("VILAO_URL", "https://api.vilao.ai/v1")
)

model = os.getenv("BRAIN_MODEL", "px/glm-5.1")
response = client.chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": "Can u speak Vietnames!"}]
)
print(response.choices[0].message.content)