# -*- coding: utf-8 -*-
"""
Created on Fri May 15 22:50:48 2026

@author: User
"""

import os
import sys
import importlib
import inspect
from mcp.server.mcpserver import MCPServer


# 強制轉成 UTF-8 輸出
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


SERVER_DIR = os.path.dirname(__file__)
TOOLS_DIR = os.path.join(SERVER_DIR, "tools")

# 埠號預設 8001(可自行修改)
# 啟動指令：
#     PowerShell    $env:MCP_SERVER_PORT = "8001"; python MCP_SERVER_SIMPLIFIED.py
#     cmd.exe       set MCP_SERVER_PORT=8001 && python MCP_SERVER_SIMPLIFIED.py
#     bash / macOS  MCP_SERVER_PORT=8001 python MCP_SERVER_SIMPLIFIED.py

PORT = int(os.environ.get("MCP_SERVER_PORT", 8001))



mcp_server = MCPServer("mcp_server_1")

def load_tools():
    
    sys.path.append(TOOLS_DIR)
      
    
    for file_name in os.listdir(TOOLS_DIR):
        if file_name.endswith(".py"):
            # 去掉副檔名，並將檔案名稱作為module_name
            module_name = file_name.replace(".py", "")
            
            # 把.py檔案存入my_file
            my_file = importlib.import_module(module_name)
            
            # 對my_file裡面所有function的item_name和item_value做檢查
            # item_name:函數名稱;item_value:函數本體
            for item_name, item_value in inspect.getmembers(my_file, inspect.isfunction):
                
                
                # item_value.__module__用來確認函式在哪個module被定義
                is_my_function = (item_value.__module__ == module_name)

                # 確認是否有docstring(manual)
                has_manual = bool(item_value.__doc__)

                # 底線開頭的函數不註冊成工具
                is_private = item_name.startswith("_")

                if is_my_function and has_manual and not is_private:
                    # 通過檢查，載入工具
                    mcp_server.add_tool(item_value)
                    print(f"[System]  成功載入工具：{item_name}")
                elif is_my_function and is_private:
                    print(f"[System]  略過內部函數：{item_name}")

if __name__ == "__main__":
    load_tools()

    print(f"[System] MCP 伺服器啟動中，位址：http://127.0.0.1:{PORT}/mcp")
    
    print("[System] 按 Ctrl+C 關閉伺服器。")

    # 啟動伺服器，使用 Streamable HTTP 模式連線
    # mcp 2.x 起，host/port 等傳輸參數改由 run() 傳入，而非建構子
    mcp_server.run(transport='streamable-http', host="127.0.0.1", port=PORT)