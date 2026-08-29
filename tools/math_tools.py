# -*- coding: utf-8 -*-
"""
四則運算工具：加、減、乘、除。

回傳格式統一成三個欄位，理由如下：

    {"status": "success", "result": 7.0, "expression": "3 + 4 = 7"}
    {"status": "error",   "message": "除數不可為 0，無法計算。"}

判斷一個欄位該不該存在，問一句就好：「模型是不是已經知道這件事了？」
早期版本還回傳過 operation、a、b，那三個都是多餘的——模型呼叫了哪支工具、
傳了什麼參數，本來就記錄在它自己送出的 tool_use 區塊裡，靠 tool_use_id 嚴格配對。
把它們拿掉之後，單次回傳從 73 個 token 降到約 40 個；而工具結果會進對話歷史、
之後每一輪都要重送，所以省下來的是每一輪都在省。

留下來的三個各有作用：
    result      答案本身。
    expression  唯一人看得懂的摘要，模型要說明計算過程時可以直接引用。
    status      只有加減乘的話其實用不到(永遠是 success)，但 divide 除以 0 時是
                「呼叫成功、業務上失敗」——那不會觸發 MCP 的 isError，對協定來說
                是一次正常的呼叫。status 是模型判斷「到底算出來沒有」的唯一
                in-band 訊號。四支工具共用同一個格式，模型只需要學一種形狀。

回傳型別刻意維持 bare dict 而不寫得更具體：成功與失敗是兩種不同形狀，
一旦宣告了 outputSchema 就會被強制驗證，失敗那條路徑會過不了。
"""


def _format(value: float) -> str:
    """
    把浮點數整理成適合放進訊息裡的字串：5.0 顯示成 "5"，2.5 維持 "2.5"。

    這是內部輔助函式(底線開頭)，不會被註冊成工具。
    """
    if value == int(value):
        return str(int(value))
    return str(value)


def add(a: float, b: float) -> dict:
    """
    計算兩個數字相加 (a + b)。

    Args:
        a: 被加數。
        b: 加數。

    Returns:
        {"status": "success", "result": 相加結果, "expression": 算式}
    """
    result = a + b
    return {
        "status": "success",
        "result": result,
        "expression": f"{_format(a)} + {_format(b)} = {_format(result)}",
    }


def subtract(a: float, b: float) -> dict:
    """
    計算兩個數字相減 (a - b)，順序是 a 減去 b。

    Args:
        a: 被減數。
        b: 減數。

    Returns:
        {"status": "success", "result": 相減結果, "expression": 算式}
    """
    result = a - b
    return {
        "status": "success",
        "result": result,
        "expression": f"{_format(a)} - {_format(b)} = {_format(result)}",
    }


def multiply(a: float, b: float) -> dict:
    """
    計算兩個數字相乘 (a × b)。

    Args:
        a: 乘數。
        b: 被乘數。

    Returns:
        {"status": "success", "result": 相乘結果, "expression": 算式}
    """
    result = a * b
    return {
        "status": "success",
        "result": result,
        "expression": f"{_format(a)} × {_format(b)} = {_format(result)}",
    }


def divide(a: float, b: float) -> dict:
    """
    計算兩個數字相除 (a ÷ b)，順序是 a 除以 b。除數為 0 時回傳錯誤而不會拋出例外。

    Args:
        a: 被除數。
        b: 除數，不可為 0。

    Returns:
        成功：{"status": "success", "result": 相除結果, "expression": 算式}
        失敗：{"status": "error", "message": 錯誤原因}
    """
    if b == 0:
        return {
            "status": "error",
            "message": "除數不可為 0，無法計算。請確認 b 的值或改用其他運算。",
        }

    result = a / b
    return {
        "status": "success",
        "result": result,
        "expression": f"{_format(a)} ÷ {_format(b)} = {_format(result)}",
    }
