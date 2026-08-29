# This is a simple interactive chat client for NCHC's OpenAI-compatible API endpoint.

import os
import sys
from openai import OpenAI

# 1. Read the API key from apikey.txt
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if not os.path.exists(env_path):
    print("Error: '.env' file not found.")
    sys.exit(1)

try:
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # 跳過空行與 # 開頭的註解行
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            # 去掉值前後可能被加上的引號
            value = value.strip().strip('"').strip("'")
            # 已經存在的環境變數優先，不覆蓋
            if key and key not in os.environ:
                os.environ[key] = value
except Exception as e:
    print(f"⚠️ 讀取 .env 失敗：{e}")

API_KEY = os.environ.get("NCHC_API_KEY", "").strip()
if not API_KEY:
    print("Error: 'NCHC_API_KEY' not found in .env.")
    sys.exit(1)


# 2. Initialize the OpenAI client pointing to NCHC's endpoint
client = OpenAI(
    base_url="https://portal.genai.nchc.org.tw/api/v1",
    api_key=API_KEY,
    default_headers={"x-api-key": API_KEY},
)

# Choose your model (matching your available models from NCHC)
MODEL_NAME = "Llama-3.3-70B-Instruct"

# 3. Initialize conversation history with a system prompt
messages = [{"role": "system", "content": "You are a helpful assistant."}]

print(f"--- NCHC OpenAI-Compatible Interactive Chat ---")
print(f"Model: {MODEL_NAME}")
print("Type 'exit' or 'quit' to end the session.\n")

# 4. Main interactive conversation loop
while True:
    try:
        user_input = input("You: ")
        if user_input.lower() in ["exit", "quit"]:
            print("Exiting chat. Goodbye!")
            break

        if not user_input.strip():
            continue

        # Append user message to history
        messages.append({"role": "user", "content": user_input})

        print("\nAssistant: ", end="", flush=True)

        # Send request with streaming enabled for a responsive chat experience
        stream = client.chat.completions.create(
            model=MODEL_NAME, messages=messages, stream=True
        )

        assistant_reply = ""
        for chunk in stream:
            content = chunk.choices[0].delta.content or ""
            print(content, end="", flush=True)
            assistant_reply += content

        print("\n")

        # Append assistant response to history for multi-turn context
        messages.append({"role": "assistant", "content": assistant_reply})

    except KeyboardInterrupt:
        print("\nSession interrupted. Goodbye!")
        break
    except Exception as e:
        print(f"\n[An error occurred]: {e}\n")