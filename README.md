# Azure DevOps QA MCP Server

> An unofficial, open-source Model Context Protocol (MCP) server 
> for Azure DevOps test automation.

**Note:** This project is not affiliated with, endorsed by, or 
associated with Microsoft Corporation. Azure DevOps® is a registered 
trademark of Microsoft Corporation.

# ado-qa-mcp-agent

An **agent-driven MCP server** for Azure DevOps test automation — **no Anthropic (or any other) LLM API required**.

Instead of embedding an LLM inside the server, this project exposes four granular Azure DevOps tools that your AI coding agent (Claude Code, GitHub Copilot, or any other MCP-capable agent) calls in sequence. The agent license handles reasoning and generation; the MCP server handles Azure DevOps API I/O.

## Setup

### Prerequisites

- Python 3.10 to 3.13
- Azure DevOps PAT with permissions for work items and test cases
- An MCP-capable client (GitHub Copilot, Claude Code, etc.)

### 1. Clone and install

#### macOS / Linux

```bash
cd ado-qa-mcp-agent
python3 -m venv .venv
.venv/bin/python -m ensurepip --upgrade
.venv/bin/python -m pip install .
```

#### Windows (PowerShell)

```powershell
cd ado-qa-mcp-agent
python -m venv .venv
.venv\Scripts\python -m ensurepip --upgrade
.venv\Scripts\python -m pip install .
```

**Note:** For Windows Command Prompt, use `.venv\Scripts\activate.bat` instead of PowerShell paths.

#### Windows (Command Prompt)

```cmd
cd ado-qa-mcp-agent
python -m venv .venv
.venv\Scripts\python -m ensurepip --upgrade
.venv\Scripts\python -m pip install .
```

### 2. Configure environment

```bash
cp .env.example .env
```

Set value in `.env`:

```dotenv
AZURE_DEVOPS_PAT=your_personal_access_token_here
```

### 3. Configure MCP client

#### VS Code / GitHub Copilot (`.vscode/mcp.json`)

```json
{
  "servers": {
    "azure-devops-qa-agent": {
      "type": "stdio",
      "command": "/absolute/path/to/ado-qa-mcp-agent/.venv/bin/azure-devops-qa-mcp",
      "env": {
        "PYTHONPATH": "/absolute/path/to/ado-qa-mcp-agent/src"
      }
    }
  }
}
```

**Note:** The server reads `AZURE_DEVOPS_PAT` from your `.env` file automatically. Do not add it to the MCP config, as that would override the `.env` setting and trigger a prompt instead.

#### Claude Code (`.mcp.json` in project root)

```json
{
  "mcpServers": {
    "azure-devops-qa-agent": {
      "command": "/absolute/path/to/ado-qa-mcp-agent/.venv/bin/azure-devops-qa-mcp",
      "env": {
        "PYTHONPATH": "/absolute/path/to/ado-qa-mcp-agent/src"
      }
    }
  }
}
```

**Note:** The server reads `AZURE_DEVOPS_PAT` from your `.env` file automatically.

### 4. Verify server starts

- Start or restart the MCP server from your MCP client.
- Ensure server status moves to `Running` with no `ModuleNotFoundError`.

### 5. Available MCP tools

- `fetch_work_item_for_test_generation` — Fetch work item details (Step 1 of test case generation)
- `create_and_link_test_cases` — Create and link test cases (Step 2 of test case generation)
- `fetch_feature_for_gherkin_generation` — Fetch feature hierarchy (Step 1 of Gherkin generation)
- `attach_gherkin_regression_to_feature` — Attach Gherkin file to feature (Step 2 of Gherkin generation)

**Important:** When using `create_and_link_test_cases`, test cases automatically inherit the parent work item's area path. You do not need to specify `area_path` in your test cases unless you want them in a different area. This ensures test cases are created with proper permissions.

## License

MIT
