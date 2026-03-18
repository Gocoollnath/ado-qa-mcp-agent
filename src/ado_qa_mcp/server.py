"""
Agent-driven MCP Server for Azure DevOps QA automation.

This MCP server does NOT call any LLM API. Instead it exposes four
granular Azure DevOps data-access tools that the AI agent (Claude Code,
GitHub Copilot, or any MCP-capable agent) uses to orchestrate test case
and Gherkin regression generation entirely through its own intelligence.

Tools:
  1. fetch_work_item_for_test_generation  — Fetches PBI/Bug data + existing TCs (Step 1 of TC workflow)
  2. create_and_link_test_cases           — Creates & links test cases in Azure DevOps (Step 2 of TC workflow)
  3. fetch_feature_for_gherkin_generation — Fetches Feature + all children + TCs + best practice (Step 1 of Gherkin workflow)
  4. attach_gherkin_regression_to_feature — Attaches .feature file to Feature work item (Step 2 of Gherkin workflow)

Env vars required: AZURE_DEVOPS_PAT
"""
import asyncio
import json
import os
import re
import sys

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

from .logging_config import configure_logging, get_logger

# Configure logging early
log_level = os.getenv("MCP_LOG_LEVEL", "INFO")
configure_logging(level=log_level)
logger = get_logger(__name__)

load_dotenv()

app = Server("azure-devops-qa-agent")
logger.info("Azure DevOps QA Agent MCP Server initialized")

# ---------------------------------------------------------------------------
# Knowledge: Gherkin best practice reference
# ---------------------------------------------------------------------------

def _load_gherkin_best_practice() -> str:
    """Load the Gherkin best practice .feature file at runtime."""
    knowledge_path = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "knowledge",
        "gherkin_example_best_practice.feature",
    )
    knowledge_path = os.path.normpath(knowledge_path)
    try:
        with open(knowledge_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "(best practice file not found)"


# ---------------------------------------------------------------------------
# Coverage-hint extraction (static analysis of work item text)
# ---------------------------------------------------------------------------

def _extract_coverage_hints(work_item: dict) -> list[str]:
    """Scan description and acceptance criteria for patterns that commonly produce test gaps."""
    hints: list[str] = []
    text = " ".join([
        work_item.get("description", "") or "",
        work_item.get("acceptance_criteria", "") or "",
    ]).lower()

    if any(kw in text for kw in ["toast", "notification", "success message", "confirmation", "banner"]):
        hints.append(
            "Include test cases for BOTH success AND failure toast/notification messages — "
            "one scenario should confirm the success toast, another the failure/error toast."
        )

    if any(kw in text for kw in ["mb", "kb", "gb", "size limit", "file size", "bytes", "maximum size"]):
        hints.append(
            "Add boundary tests at the EXACT size limit stated in the ACs (e.g. a file of exactly 5 MB "
            "should succeed; a file 1 byte over the limit should fail with an error)."
        )

    if any(kw in text for kw in ["display", "shows", "reflects", "after upload", "after save",
                                   "after submit", "updated", "visible"]):
        hints.append(
            "Add a post-action state test: after a successful operation the UI should visibly reflect "
            "the change (e.g. the new profile picture is displayed in place of the old one)."
        )

    if any(kw in text for kw in ["mobile", "webview", "web view", "native app", "ios", "android"]):
        hints.append(
            "Add platform-specific test cases for mobile/webview scenarios explicitly mentioned in the description."
        )

    if any(kw in text for kw in ["initials", "first name", "last name", "full name", "derived", "generated"]):
        hints.append(
            "Add a test verifying that computed/derived values (e.g. initials from first + last name) "
            "are correct and update automatically when the source fields are changed."
        )

    if any(kw in text for kw in ["refresh", "reload", "re-login", "log out", "logout",
                                   "persist", "persists", "saved", "retained"]):
        hints.append(
            "Add a persistence test: verify the updated data is still present after a page refresh or "
            "after the user logs out and back in."
        )

    if any(kw in text for kw in ["tab", "keyboard", "accessible", "accessibility", "screen reader",
                                   "aria", "wcag", "a11y"]):
        hints.append(
            "Add accessibility test cases for keyboard navigation (Tab order) and any ARIA/screen-reader "
            "requirements stated in the ACs."
        )

    if any(kw in text for kw in ["crop", "resize", "zoom", "drag", "rotate", "edit image"]):
        hints.append(
            "Add test cases for image-manipulation interactions (crop, resize, zoom, drag) — "
            "including both Save and Cancel paths within the image editor modal."
        )

    if any(kw in text for kw in ["format", "png", "jpg", "jpeg", "tiff", "bmp", "gif", "svg", "webp"]):
        hints.append(
            "For each supported file format listed in the ACs, add a separate positive test case. "
            "Also add a negative test for an explicitly unsupported format (e.g. .exe, .pdf)."
        )

    if any(kw in text for kw in ["cancel", "close", "discard", "dismiss"]):
        hints.append(
            "Add test cases for cancel/close/discard flows — verify that no changes are persisted "
            "and the UI returns to its previous state."
        )

    return hints


# ---------------------------------------------------------------------------
# Tool list
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="fetch_work_item_for_test_generation",
            description=(
                "[STEP 1 of 2 — Test Case Generation]\n\n"
                "Fetches a Product Backlog Item (PBI) or Bug from Azure DevOps, including its "
                "title, description, acceptance criteria, story points, parent feature, and all "
                "currently linked test cases (to prevent duplicates).\n\n"
                "AFTER receiving the response, YOU (the AI agent) must:\n"
                "1. Classify complexity based on the returned data:\n"
                "   - Simple  (3-6 TCs):  story_points ≤ 2, or ≤ 2 acceptance criteria & short description\n"
                "   - Medium  (6-12 TCs): story_points 3-5, or 3-5 acceptance criteria\n"
                "   - Complex (12-18 TCs): story_points > 5, or > 5 acceptance criteria, or very long description\n"
                "   Additionally, add +1 TC for every hint in coverage_hints that is not already "
                "covered by another test case — do not skip hints just to stay within the base range.\n"
                "2. GRANULARITY RULE — before writing any test case, ask: 'Does this step naturally lead "
                "into the next one as part of a single user journey?' If yes, merge them into ONE test case "
                "with multiple steps. Do NOT create a separate test case for each micro-interaction "
                "(e.g. 'slide-out opens' and 'Update Photo button is visible' belong inside the happy-path "
                "test as steps, not as standalone TCs). A well-scoped test case covers a complete "
                "user flow: trigger → interaction → observable outcome.\n"
                "   BAD (too granular): TC1='Slide-out displays profile section', TC2='Clicking Update Photo "
                "opens file picker' — these are consecutive steps of the same action.\n"
                "   GOOD (right scope): TC='User opens My Profile slide-out and initiates a photo update' "
                "with steps: open slide-out → verify section + button → click Update Photo → file picker appears.\n"
                "3. Design test cases covering ALL of the following categories (as applicable):\n"
                "   a) Happy paths — successful end-to-end flows (merge sequential UI steps into one TC)\n"
                "   b) Success feedback — verify success toast/confirmation messages after happy paths\n"
                "   c) Post-action state — verify the UI reflects the change after a successful action\n"
                "   d) Validation errors — invalid input, wrong format, missing required fields\n"
                "   e) Boundary conditions — test AT the exact limit stated in the ACs (e.g. exactly 5 MB), "
                "not just above or below it\n"
                "   f) Edge cases — empty/default/fallback states (e.g. initials shown when no image)\n"
                "   g) Negative scenarios — failure states, error toasts, unchanged state after failure\n"
                "   h) Data persistence — verify data survives a page refresh or re-login when relevant\n"
                "   i) Computed/derived state — verify values derived from other fields (e.g. initials from "
                "first/last name) are correct and update when the source fields change\n"
                "   j) Platform-specific — mobile/webview scenarios when mentioned in the description\n"
                "   k) Accessibility — keyboard navigation and any a11y requirements stated in the ACs\n"
                "   IMPORTANT: also apply every hint listed in coverage_hints returned by this tool.\n"
                "   Skip any title that already exists in existing_test_cases.\n"
                "4. Each test case must have a clear title and action/expected_result steps.\n"
                "5. Call create_and_link_test_cases with the test cases you designed.\n\n"
                "Only env var needed: AZURE_DEVOPS_PAT"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "work_item_id": {
                        "type": "integer",
                        "description": "Azure DevOps work item ID (PBI or Bug)",
                    },
                    "organization_url": {
                        "type": "string",
                        "description": "Azure DevOps organization URL (e.g. https://dev.azure.com/myorg)",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Azure DevOps project name",
                    },
                },
                "required": ["work_item_id", "organization_url", "project_name"],
            },
        ),
        types.Tool(
            name="create_and_link_test_cases",
            description=(
                "[STEP 2 of 2 — Test Case Generation]\n\n"
                "Creates one or more test cases in Azure DevOps with properly formatted steps, "
                "then immediately creates 'Tested by' links back to the source work item.\n\n"
                "Call this after fetch_work_item_for_test_generation with the test_cases array you designed.\n\n"
                "Each test case must include a title and steps (action + expected_result).\n"
                "Priority: 1=Critical, 2=High (default), 3=Medium, 4=Low.\n\n"
                "IMPORTANT: area_path is automatically inherited from the parent work item if not specified. "
                "Test cases are created in the same area path as the parent work item, ensuring proper permissions. "
                "Only override area_path if you need test cases in a different area.\n\n"
                "Only env var needed: AZURE_DEVOPS_PAT"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "work_item_id": {
                        "type": "integer",
                        "description": "Source work item ID that all test cases will be linked to",
                    },
                    "organization_url": {
                        "type": "string",
                        "description": "Azure DevOps organization URL",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Azure DevOps project name",
                    },
                    "test_cases": {
                        "type": "array",
                        "description": "List of test cases to create and link",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "description": "Test case title",
                                },
                                "priority": {
                                    "type": "integer",
                                    "description": "Priority: 1=Critical, 2=High, 3=Medium, 4=Low (default: 2)",
                                },
                                "area_path": {
                                    "type": "string",
                                    "description": "Optional area path. If omitted, automatically inherits from the parent work item's area path.",
                                },
                                "iteration_path": {
                                    "type": "string",
                                    "description": "Optional iteration path",
                                },
                                "steps": {
                                    "type": "array",
                                    "description": "Ordered test steps",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "action": {
                                                "type": "string",
                                                "description": "What the tester does",
                                            },
                                            "expected_result": {
                                                "type": "string",
                                                "description": "What should happen",
                                            },
                                        },
                                        "required": ["action", "expected_result"],
                                    },
                                },
                            },
                            "required": ["title", "steps"],
                        },
                    },
                },
                "required": ["work_item_id", "organization_url", "project_name", "test_cases"],
            },
        ),
        types.Tool(
            name="fetch_feature_for_gherkin_generation",
            description=(
                "[STEP 1 of 2 — Gherkin Regression Generation]\n\n"
                "Fetches a Feature work item from Azure DevOps along with ALL child PBIs/Bugs "
                "and ALL their linked test cases (with full steps). Also provides a Gherkin "
                "best practice reference example.\n\n"
                "AFTER receiving the response, YOU (the AI agent) must:\n"
                "1. Analyze the full feature scope: acceptance criteria, child requirements, "
                "and existing test coverage.\n"
                "2. Design 5-10 end-to-end BDD Gherkin scenarios covering feature-level user journeys.\n"
                "3. Write a complete .feature file with:\n"
                "   - Feature block: title + 'As a/I want/So that' description\n"
                "   - Background block: shared preconditions (if applicable)\n"
                "   - Scenarios with @tags (@smoke, @regression, @critical, @end-to-end as appropriate)\n"
                "   - Given/When/Then/And steps in natural business language\n"
                "   - Coverage: happy paths, error cases, boundary/edge conditions\n"
                "4. Follow the style and quality of the best_practice_example exactly.\n"
                "5. Call attach_gherkin_regression_to_feature with the complete .feature file content "
                "and the feature_title from the response.\n\n"
                "Only env var needed: AZURE_DEVOPS_PAT"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "feature_id": {
                        "type": "integer",
                        "description": "Azure DevOps Feature work item ID",
                    },
                    "organization_url": {
                        "type": "string",
                        "description": "Azure DevOps organization URL (e.g. https://dev.azure.com/myorg)",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Azure DevOps project name",
                    },
                },
                "required": ["feature_id", "organization_url", "project_name"],
            },
        ),
        types.Tool(
            name="attach_gherkin_regression_to_feature",
            description=(
                "[STEP 2 of 2 — Gherkin Regression Generation]\n\n"
                "Attaches the Gherkin .feature file you wrote to the Feature work item in Azure DevOps. "
                "The filename is auto-generated as {feature_id}_{sanitized_title}_regression.feature.\n\n"
                "Call this after fetch_feature_for_gherkin_generation with the Gherkin content you designed "
                "and the feature_title returned in the previous step's response.\n\n"
                "Only env var needed: AZURE_DEVOPS_PAT"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "feature_id": {
                        "type": "integer",
                        "description": "Azure DevOps Feature work item ID",
                    },
                    "feature_title": {
                        "type": "string",
                        "description": "Feature title (used to generate the filename)",
                    },
                    "organization_url": {
                        "type": "string",
                        "description": "Azure DevOps organization URL",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "Azure DevOps project name",
                    },
                    "gherkin_content": {
                        "type": "string",
                        "description": "Complete .feature file content written by the AI agent",
                    },
                },
                "required": [
                    "feature_id", "feature_title", "organization_url",
                    "project_name", "gherkin_content",
                ],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """
    Handle tool calls from the MCP client.

    Args:
        name: Tool name
        arguments: Tool arguments

    Returns:
        Tool execution result as text content
    """
    try:
        logger.info(f"Tool called: {name}")
        logger.debug(f"Tool arguments: {arguments}")

        if name == "fetch_work_item_for_test_generation":
            result = await asyncio.to_thread(_handle_fetch_work_item, arguments)
        elif name == "create_and_link_test_cases":
            result = await asyncio.to_thread(_handle_create_test_cases, arguments)
        elif name == "fetch_feature_for_gherkin_generation":
            result = await asyncio.to_thread(_handle_fetch_feature_hierarchy, arguments)
        elif name == "attach_gherkin_regression_to_feature":
            result = await asyncio.to_thread(_handle_publish_gherkin, arguments)
        else:
            error_msg = f"Unknown tool: {name}"
            logger.error(error_msg)
            result = json.dumps({"error": error_msg})

        logger.info(f"Tool {name} completed successfully")
        return [types.TextContent(type="text", text=result)]

    except Exception as exc:
        logger.error(f"Tool execution failed for {name}: {exc}", exc_info=True)
        error_response = {
            "error": f"Tool execution failed: {str(exc)}",
            "tool": name,
        }
        return [types.TextContent(
            type="text",
            text=json.dumps(error_response),
        )]



# ---------------------------------------------------------------------------
# Handler: fetch_work_item_data
# ---------------------------------------------------------------------------

def _handle_fetch_work_item(args: dict) -> str:
    from .tools.azure_devops_workitem import fetch_work_item, get_linked_test_cases

    work_item_id = args["work_item_id"]
    org_url = args["organization_url"]
    project = args["project_name"]

    work_item_raw = json.loads(fetch_work_item(org_url, project, work_item_id))
    existing_raw = json.loads(get_linked_test_cases(org_url, project, work_item_id))

    work_item = work_item_raw.get("work_item") or {}
    coverage_hints = _extract_coverage_hints(work_item)

    result = {
        "work_item": work_item,
        "parent_feature": work_item_raw.get("parent_feature"),
        "existing_test_cases": existing_raw.get("linked_test_cases", []),
        "existing_test_cases_count": existing_raw.get("linked_test_cases_count", 0),
        "coverage_hints": coverage_hints,
        "_next_step": (
            "Design test cases based on complexity tier (Simple 3-6 / Medium 6-12 / Complex 12-18), "
            "adding at least one extra test case per applicable coverage_hint not already covered, "
            "and avoiding duplicates with existing ones. "
            "IMPORTANT: Do NOT include area_path in your test_cases — it will be automatically inherited from the parent work item. "
            "Then call create_and_link_test_cases with your test_cases array."
        ),
    }
    return json.dumps(result, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Handler: create_and_link_test_cases
# ---------------------------------------------------------------------------

def _handle_create_test_cases(args: dict) -> str:
    from .tools.azure_devops_workitem import create_test_case, create_work_item_links, fetch_work_item

    work_item_id = args["work_item_id"]
    org_url = args["organization_url"]
    project = args["project_name"]
    test_cases = args.get("test_cases", [])

    if not test_cases:
        return json.dumps({"error": "test_cases array is required and must not be empty"})

    # Fetch parent work item to get its area_path as fallback
    parent_area_path = None
    try:
        wi_raw = json.loads(fetch_work_item(org_url, project, work_item_id))
        parent_work_item = wi_raw.get("work_item", {})
        parent_area_path = parent_work_item.get("area_path")
        if parent_area_path:
            logger.debug(f"Using parent work item area_path: {parent_area_path}")
    except Exception as e:
        logger.warning(f"Could not fetch parent work item area_path: {e}")

    created_ids = []
    creation_results = []

    for tc in test_cases:
        title = tc.get("title", "")
        steps = tc.get("steps", [])
        priority = tc.get("priority", 2)
        # Use test case's area_path if provided, otherwise fall back to parent's area_path
        area_path = tc.get("area_path") or parent_area_path
        iteration_path = tc.get("iteration_path")

        # Normalise steps to the format create_test_case() expects
        normalised_steps = [
            {"action": s.get("action", ""), "expected_result": s.get("expected_result", "")}
            for s in steps
        ]

        raw = json.loads(create_test_case(
            org_url, project, title, normalised_steps, priority, area_path, iteration_path
        ))
        creation_results.append(raw)
        if raw.get("success"):
            created_ids.append(raw["test_case_id"])

    # Bulk-link all successfully created test cases
    link_result = {}
    if created_ids:
        link_result = json.loads(create_work_item_links(org_url, project, work_item_id, created_ids))

    return json.dumps({
        "summary": {
            "requested": len(test_cases),
            "created": len(created_ids),
            "linked_to_work_item": work_item_id,
        },
        "created_test_cases": creation_results,
        "link_result": link_result,
    }, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Handler: fetch_feature_hierarchy
# ---------------------------------------------------------------------------

def _handle_fetch_feature_hierarchy(args: dict) -> str:
    from .tools.azure_devops_gherkin import (
        fetch_feature,
        get_child_work_items,
        get_all_test_cases_for_items,
    )

    feature_id = args["feature_id"]
    org_url = args["organization_url"]
    project = args["project_name"]

    feature_raw = json.loads(fetch_feature(org_url, project, feature_id))
    children_raw = json.loads(get_child_work_items(org_url, project, feature_id))

    child_ids = [c["id"] for c in children_raw.get("child_work_items", []) if "id" in c]
    test_cases_raw = (
        json.loads(get_all_test_cases_for_items(org_url, project, child_ids))
        if child_ids
        else {"total_test_cases": 0, "test_cases": []}
    )

    best_practice = _load_gherkin_best_practice()

    result = {
        "feature": feature_raw.get("feature"),
        "child_work_items": children_raw.get("child_work_items", []),
        "child_work_items_count": children_raw.get("child_work_items_count", 0),
        "existing_test_cases": test_cases_raw.get("test_cases", []),
        "existing_test_cases_count": test_cases_raw.get("total_test_cases", 0),
        "best_practice_example": best_practice,
        "_next_step": (
            "Write a complete Gherkin .feature file (5-10 scenarios) covering the feature described above. "
            "Mirror the style and quality of the best_practice_example. "
            "Then call attach_gherkin_regression_to_feature with the gherkin_content and the feature_title."
        ),
    }
    return json.dumps(result, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Handler: publish_gherkin_regression
# ---------------------------------------------------------------------------

def _handle_publish_gherkin(args: dict) -> str:
    from .tools.azure_devops_gherkin import create_attachment

    feature_id = args["feature_id"]
    feature_title = args["feature_title"]
    org_url = args["organization_url"]
    project = args["project_name"]
    gherkin_content = args["gherkin_content"]

    # Build a safe filename: {id}_{Title_With_Underscores}_regression.feature
    safe_title = re.sub(r"[^a-zA-Z0-9_\-]", "_", feature_title)
    safe_title = re.sub(r"_+", "_", safe_title).strip("_")
    file_name = f"{feature_id}_{safe_title}_regression.feature"

    result = json.loads(create_attachment(
        org_url, project, feature_id, gherkin_content, file_name
    ))
    return json.dumps(result, indent=2, ensure_ascii=False)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _run():
    """Run the MCP server with proper error handling."""
    try:
        logger.info("Starting MCP server...")
        async with stdio_server() as (read_stream, write_stream):
            logger.info("Stdio transport established")
            await app.run(read_stream, write_stream, app.create_initialization_options())
    except KeyboardInterrupt:
        logger.info("Server shutdown requested by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error in MCP server: {e}", exc_info=True)
        sys.exit(1)


def main():
    """Entry point for the MCP server."""
    # Verify required environment variables
    pat = os.getenv("AZURE_DEVOPS_PAT", "").strip()
    if not pat:
        logger.error(
            "AZURE_DEVOPS_PAT environment variable is not set. "
            "Please set your Azure DevOps Personal Access Token before running the server."
        )
        sys.exit(1)

    logger.info("Azure DevOps PAT environment variable is set")
    logger.info(f"Log level: {log_level}")

    try:
        asyncio.run(_run())
    except Exception as e:
        logger.error(f"Failed to start server: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
