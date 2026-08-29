# taiwanairap

Example of Chat/MCP/Agent using Taiwan AI RAP API.

Minimal examples of building an LLM chat agent with tool-calling over the
[Model Context Protocol (MCP)](https://modelcontextprotocol.io/), using
[NCHC's Taiwan AI Cloud GenAI Portal](https://portal.genai.nchc.org.tw/)
as the (OpenAI-compatible) model backend.

## Contents

| File | Description |
|---|---|
| [taiwanairap.py](taiwanairap.py) | Minimal interactive chat client that talks directly to NCHC's OpenAI-compatible endpoint. No tools, no MCP — just a streaming chat loop. Good starting point for understanding the API before adding tool-calling. |
| [agent_multi_server_nchc.py](agent_multi_server_nchc.py) | The full agent. Connects to one or more MCP servers, merges their tool listings, and runs a multi-turn chat loop where the NCHC model can call those tools (with retry/error limits and streaming-free tool-call handling). |
| [MCP_SERVER_SIMPLIFIED.py](MCP_SERVER_SIMPLIFIED.py) | A minimal MCP server. On startup it scans every `.py` file in `tools/` and auto-registers any public, documented function as a callable MCP tool. |
| [tools/math_tools.py](tools/math_tools.py) | Example tool module exposing `add` / `subtract` / `multiply` / `divide`, auto-discovered by the MCP server above. |

## How it fits together

```
agent_multi_server_nchc.py  <--MCP (HTTP)-->  MCP_SERVER_SIMPLIFIED.py  -->  tools/*.py
        |
        v
  NCHC GenAI Portal (OpenAI-compatible chat completions API)
```

1. `MCP_SERVER_SIMPLIFIED.py` starts an MCP server and loads every eligible
   function under `tools/` as a tool.
2. `agent_multi_server_nchc.py` connects to that server (and any others
   listed in its `MCP_SERVERS` dict), fetches the combined tool list, and
   exposes it to the NCHC model via OpenAI-style function calling.
3. When the model asks to call a tool, the agent routes the call to the
   right MCP server, executes it, and feeds the result back to the model.

## Requirements

- Python 3.11+
- An NCHC Taiwan AI Cloud API key: https://portal.genai.nchc.org.tw/

Install dependencies:

```bash
pip install openai mcp
```

## Setup

1. Copy the env template and fill in your key:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and set `NCHC_API_KEY=<your key>`.

2. Start the MCP server (defaults to port 8001; override with
   `MCP_SERVER_PORT`):
   ```bash
   python MCP_SERVER_SIMPLIFIED.py
   ```

3. In a second terminal, start the agent:
   ```bash
   python agent_multi_server_nchc.py
   ```
   Type a message and press Enter; type `exit` to quit. If the MCP server
   isn't running, the agent falls back to plain text-only chat.

   Alternatively, to try the raw chat API without any tools:
   ```bash
   python taiwanairap.py
   ```

## Adding a new tool

Drop a new `.py` file into `tools/` with public, documented functions, e.g.:

```python
def my_tool(x: int) -> dict:
    """
    One-line description of what this does.

    Args:
        x: what this parameter means.

    Returns:
        A short description of the return shape.
    """
    return {"status": "success", "result": x}
```

- The docstring is required — functions without one are skipped.
- Function names starting with `_` are treated as private helpers and are
  never registered as tools.
- Restart `MCP_SERVER_SIMPLIFIED.py` to pick up the new file; the agent
  discovers it automatically on its next connection.

## Configuration notes

- Default model is `Llama-3.3-70B-Instruct` — change `MODEL_NAME` in
  `taiwanairap.py` / `NCHC_MODEL` in `agent_multi_server_nchc.py` to use a
  different model available on your NCHC account.
- `agent_multi_server_nchc.py` connects to the servers listed in its
  `MCP_SERVERS` dict (default: a single local server at
  `http://127.0.0.1:8001/mcp`) — add more entries to fan out across
  multiple MCP servers.
- `.env` holds your API key and is excluded via `.gitignore` — never commit
  it. Use `.env.example` as the template.
