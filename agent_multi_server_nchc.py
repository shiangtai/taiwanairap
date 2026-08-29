
import asyncio
import json
from mcp.client.streamable_http import streamable_http_client
from mcp.client.session import ClientSession
from openai import AsyncOpenAI
import sys
import os
from contextlib import asynccontextmanager, AsyncExitStack

# 設定主控台編碼
try:
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass




# NCHC 服務入口(OpenAI 相容 API)
NCHC_BASE_URL = "https://portal.genai.nchc.org.tw/api/v1"

# 使用的模型，須為 NCHC 帳號下可用的模型名稱
NCHC_MODEL = "Llama-3.3-70B-Instruct"

# 每次呼叫的輸出上限
MAX_OUTPUT_TOKENS = 4096

# 系統提示(角色設定)。留空字串就等於完全不送這個參數。
# 計算工具一次只吃兩個數字，模型偶爾會在多步驟算式時自創呼叫格式
# (例如傳 {"function_name":..., "args":[...]} 而非工具實際要求的欄位)，
# 因此明講規則讓它老實拆成一串兩數運算。
SYSTEM_PROMPT = (
    "你可以使用的計算工具(add/subtract/multiply/divide)一次只能處理兩個數字。"
    "遇到需要多個步驟才能算出結果的算式時，請依照運算優先順序"
    "(先乘除、後加減，相同優先順序由左到右)，把算式拆成多次兩數運算，"
    "一次只呼叫一個工具、只能傳入實際數字(不能傳運算式、字串或其他鍵值)，"
    "並用上一步驟算出的結果當作下一步的輸入，直到得出最終答案。"
)

# 要連線的 MCP 伺服器清單
MCP_SERVERS = {
    "server1": "http://127.0.0.1:8001/mcp",

}

# 連續多少「輪」工具呼叫全部失敗就停止
MAX_CONSECUTIVE_TOOL_ERRORS = 2

# 單輪問答最多幾次工具呼叫
MAX_TURNS = 15


# =============================================================================
# 第 1 節　環境準備：讀取 .env、檢查 API 金鑰
# =============================================================================

def load_dotenv():
    """
    讀取專案資料夾裡的 .env，把裡面的設定填進 os.environ。
    """
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return

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


load_dotenv()
API_KEY = os.environ.get("NCHC_API_KEY", "").strip()
if not API_KEY:
    print("找不到 NCHC_API_KEY，請在 .env 設定，或於執行前用環境變數帶入。")


# =============================================================================
# 第 2 節　連線：接上 MCP 伺服器，數量不定，用 AsyncExitStack 管理
# =============================================================================

def _describe_error(exc):
    """
    從 anyio 的例外群組裡挖出真正有意義的錯誤訊息。
    """
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            described = _describe_error(sub)
            if described:
                return described
        return None

    if isinstance(exc, asyncio.CancelledError):
        return None

    return f"{type(exc).__name__}: {exc}"


@asynccontextmanager
async def connect_to_mcp_servers(server_urls):
    """
    同時連上多台 MCP 伺服器，yield 出 {設定檔代號: (伺服器自報名稱, session)} 的字典。

    單獨一台連不上則印出原因後跳過，用連得上的那幾台繼續跑。
    全部都連不上時 yield 一個空字典，交給 chat_loop 走純文字模式。
    """
    sessions = {}

    async with AsyncExitStack() as outer_stack:
        for name, url in server_urls.items():
            server_stack = AsyncExitStack()
            try:
                read, write = await server_stack.enter_async_context(
                    streamable_http_client(url)
                )
                session = await server_stack.enter_async_context(
                    ClientSession(read, write)
                )

                init_result = await session.initialize()
            # 連線失敗時印出原因後跳過
            except BaseException as e:
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise

                reason = _describe_error(e)

                try:
                    await server_stack.aclose()
                except BaseException as close_exc:
                    reason = _describe_error(close_exc) or reason

                print(f" [{name}] 連線失敗({url})：{reason or e}")
                continue

            # server_info.name:伺服器自報名稱
            # name:MCP_SERVERS裡寫的名稱
            display_name = init_result.server_info.name or name

            # 連線成功，才把這一台的 stack 交給外層保管，跟著整段結束時一起關閉
            outer_stack.push_async_exit(server_stack)
            sessions[name] = (display_name, session)
            print(f" [{name}] 連線成功：{display_name} ({url})")

        yield sessions


# =============================================================================
# 第 3 節　工具：整理成 NCHC(OpenAI 相容)看得懂的格式，合併與重名處理
# =============================================================================

async def collect_tools(sessions):
    """
    把每一台伺服器的工具清單合併成一份，並建立「工具名稱 -> (來源, session)」的路由表。

    回傳 (openai_tools, tool_routes)。
    """
    merged_tools = []
    tool_routes = {}

    for name, (display_name, session) in sessions.items():
        try:
            tools_menu = await session.list_tools()
        except BaseException as e:

            if isinstance(e, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                raise
            print(f"[{display_name}] 取得工具清單失敗：{_describe_error(e) or e}")
            continue

        accepted = []
        skipped = []
        for tool in tools_menu.tools:
            # 名稱已經被先連上的伺服器用掉了就略過。
            if tool.name in tool_routes:
                skipped.append(tool.name)
                continue

            # 路由表：這支工具來自哪一台，要用哪條連線呼叫
            tool_routes[tool.name] = (display_name, session)

            merged_tools.append(tool)
            accepted.append(tool.name)

        print(f"[{display_name}] 載入 {len(accepted)} 個工具：{'、'.join(accepted) or '(無)'}")
        if skipped:
            first_owner = tool_routes[skipped[0]][0]
            print(f"   另有 {len(skipped)} 個工具因名稱與 [{first_owner}] 重複而略過："
                  f"{'、'.join(skipped)}")

    openai_tools = mcp_tools_for_openai(merged_tools) if merged_tools else None
    return openai_tools, tool_routes


def mcp_tools_for_openai(tools_list):
    """
    把 MCP 工具清單轉成 OpenAI 相容 API(NCHC)認得的 function-calling 格式。
    """
    declarations = []
    for tool in tools_list:
        declarations.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.input_schema or {"type": "object", "properties": {}},
            },
        })

    return declarations


# =============================================================================
# 第 4 節　呼叫 NCHC LLM
# =============================================================================

async def call_nchc(client, messages, tools, tool_choice=None):
    """
    model/max_tokens/tool_choice為請求參數
    tools/messages為呼叫前綴
    """
    kwargs = dict(
        model=NCHC_MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        messages=messages,
    )

    if tools:
        kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice

    response = await client.chat.completions.create(**kwargs)

    u = response.usage
    if u is not None:
        print(f"[Tokens] 輸入={u.prompt_tokens} 輸出={u.completion_tokens}")

    return response


# =============================================================================
# 第 5 節　處理回覆、執行工具
# =============================================================================

def _assistant_message_dict(message):
    """
    把 OpenAI 回傳的 assistant message 物件轉成可以放回 messages 歷史的 dict。
    """
    msg = {"role": "assistant", "content": message.content}
    if message.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in message.tool_calls
        ]
    return msg


async def process_ai_response(response, messages, tool_routes, client, tools):
    """
    處理 NCHC 的回覆，並在需要時執行工具、把結果餵回去。
    """
    current_response = response

    # 這一輪問答已經用掉的工具呼叫次數
    turn_count = 0
    # 連續全部失敗的輪數，以及最後一次的錯誤內容(停止時要告訴模型)
    consecutive_errors = 0
    last_error_text = ""

    while True:
        message = current_response.choices[0].message

        # 把文字部分印出
        text_content = (message.content or "").strip()
        if text_content:
            print(f"AI：{text_content}")

        # 把這輪的完整回覆(可能同時包含文字與工具請求)記錄進對話歷史
        messages.append(_assistant_message_dict(message))

        # 撈出這輪回覆中所有的工具請求
        tool_calls = message.tool_calls or []

        # 不再需要呼叫工具，直接結束
        if not tool_calls:
            break

        # 判斷是否需要停止
        turn_count += 1
        stop_reason = None

        if consecutive_errors >= MAX_CONSECUTIVE_TOOL_ERRORS:
            stop_reason = (
                f"工具已經連續 {consecutive_errors} 輪全部執行失敗，"
                f"最後一次的錯誤是：{last_error_text}"
            )
        elif turn_count > MAX_TURNS:
            stop_reason = f"這一輪問答的工具呼叫次數已經達到上限（{MAX_TURNS} 次）"

        if stop_reason:
            print(f"[System] 停止呼叫工具：{stop_reason}")

            # 每個 tool_call 都必須有對應的 tool 訊息回覆
            # 這裡用停止原因作為 tool 訊息內容回傳
            for tc in tool_calls:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": (
                        f"[系統中止] {stop_reason}。"
                        "請不要再嘗試呼叫任何工具，直接用目前已知的資訊回答使用者，"
                        "並簡短說明為什麼沒有繼續查下去。"
                    ),
                })

            # tool_choice="none" 禁止模型繼續調用工具
            final_response = await call_nchc(
                client, messages, tools, tool_choice="none"
            )

            final_message = final_response.choices[0].message
            final_text = (final_message.content or "").strip()
            # 將回覆的文字部分印出
            if final_text:
                messages.append({"role": "assistant", "content": final_message.content})
                print(f"AI（已停止使用工具）：{final_text}\n")
            else:
                # 若模型沒給出文字回覆(可能達到輸出上限)則補上一句有意義的文字
                messages.append({
                    "role": "assistant",
                    "content": "（已停止呼叫工具，本輪結束。）",
                })
                print("AI（已停止使用工具）：（沒有產生說明文字。）\n")
            break

        # 正常情況，平行執行所有工具請求
        for tc in tool_calls:
            route = tool_routes.get(tc.function.name)
            origin = route[0] if route else "未知來源"
            print(f"[System] 呼叫工具：{tc.function.name}（來自 {origin}）...")

        tasks = [execute_single_tool(tc, tool_routes) for tc in tool_calls]
        tool_result_messages = await asyncio.gather(*tasks)

        # 若單輪內所有工具都失敗，連續失敗計數+1
        failed = [m for m in tool_result_messages if m.get("is_error")]
        if failed and len(failed) == len(tool_result_messages):
            consecutive_errors += 1
            last_error_text = str(failed[-1]["content"])[:200]
        else:
            consecutive_errors = 0

        # 打包並餵回給 NCHC，進入下一輪思考(is_error 只是內部標記，不能放進訊息)
        for m in tool_result_messages:
            m.pop("is_error", None)
            messages.append(m)

        current_response = await call_nchc(client, messages, tools)


async def execute_single_tool(tool_call, tool_routes):
    """負責執行單個工具，並回傳 OpenAI 規定格式的 tool 訊息(role="tool")"""
    tool_name = tool_call.function.name

    try:
        tool_args = json.loads(tool_call.function.arguments or "{}")
    except json.JSONDecodeError as e:
        print(f"[System] 解析工具參數失敗：{tool_name} -> {e}")
        return {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": f"Error parsing tool arguments: {e}",
            "is_error": True,
        }

    try:
        # 將工具請求匹配到對應的連線
        route = tool_routes.get(tool_name)
        if route is None:
            raise Exception(f"找不到工具 '{tool_name}' 對應的伺服器連線")
        server_name, session = route

        # 設定等待時間上限避免卡死
        tool_result = await asyncio.wait_for(
            session.call_tool(tool_name, tool_args),
            timeout=30.0
        )
        # 將工具結果的文字部分取出
        extracted_text = ""
        for item in tool_result.content:
            if item.type == 'text':
                extracted_text += item.text

        # 用is_error標記失敗的結果
        if tool_result.is_error:
            print(f"[System] 工具回報失敗：{tool_name} -> {extracted_text[:120]}")
            return {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": str(extracted_text),
                "is_error": True,
            }
        # 正常執行，回傳結果
        return {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": str(extracted_text),
        }
    # 遇到其他錯誤也標記成工具執行失敗
    except BaseException as e:

        if isinstance(e, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise

        reason = _describe_error(e) or f"{type(e).__name__}: {e}"
        print(f"[System] 執行工具出錯：{tool_name} -> {reason}")
        return {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": f"Error executing tool: {reason}",
            "is_error": True,
        }


# =============================================================================
# 第 6 節　對話主迴圈
# =============================================================================

async def chat_loop(sessions=None):
    """
    獨立的對話循環。
    sessions 為 None 或空字典代表沒有任何 MCP 連線，不載入工具，走純文字對話模式。
    """
    openai_tools = None
    tool_routes = {}

    if sessions:
        openai_tools, tool_routes = await collect_tools(sessions)

    if openai_tools:
        print(f"共 {len(sessions)} 台伺服器、{len(tool_routes)} 個工具，可以開始對話了。\n")
    else:
        # 無工具時清空路由表
        tool_routes = {}
        print("未載入任何工具，以「純文字模式」啟動對話。\n")

    client = AsyncOpenAI(
        base_url=NCHC_BASE_URL,
        api_key=API_KEY,
        default_headers={"x-api-key": API_KEY},
    )

    messages = []
    if SYSTEM_PROMPT.strip():
        messages.append({"role": "system", "content": SYSTEM_PROMPT})

    while True:
        # 等待使用者輸入
        try:
            user_say = await asyncio.to_thread(input, "你：")
        except (KeyboardInterrupt, EOFError):
            print("\n準備關閉系統")
            break
        # 輸入exit關閉
        if user_say.strip().lower() == 'exit':
            print("準備關閉系統")
            break

        if not user_say.strip():
            continue

        # 記下這一輪開始前的歷史長度，萬一中途出錯要用來回檔
        history_checkpoint = len(messages)
        messages.append({"role": "user", "content": user_say})

        try:
            ai_reply = await call_nchc(client, messages, openai_tools)
            await process_ai_response(ai_reply, messages, tool_routes, client, openai_tools)
        except BaseException as e:

            if isinstance(e, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                raise

            # 出錯時刪除這輪新寫進去的，回到存檔點狀態
            del messages[history_checkpoint:]
            print(f"[出錯]：{_describe_error(e) or e}，請換個方式再問一次！\n")


# =============================================================================
# 第 7 節　進入點
# =============================================================================

async def run_chat():
    """
    連上 MCP_SERVERS 裡列出的所有伺服器並開始對話。
    """
    print(f"模型：{NCHC_MODEL}（NCHC，{NCHC_BASE_URL}）")
    print(f"準備連線 {len(MCP_SERVERS)} 台 MCP 伺服器...")

    async with connect_to_mcp_servers(MCP_SERVERS) as sessions:
        if not sessions:
            print("無伺服器連線，將以純文字模式啟動。")

        await chat_loop(sessions)


if __name__ == "__main__":
    try:
        asyncio.run(run_chat())
    except KeyboardInterrupt:
        print("\n已中斷。")
    except BaseException as e:
        print(f"\n連線中斷：{_describe_error(e) or type(e).__name__}")
        print("   MCP 伺服器可能已經關閉，請重新啟動伺服器後再執行這支程式。")
