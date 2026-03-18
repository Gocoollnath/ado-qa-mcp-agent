"""
Azure DevOps Work Item tool — pure Azure DevOps REST API functions.
No LLM dependency. Used directly by server.py handlers.

Public API:
  fetch_work_item(org_url, project, work_item_id) -> str (JSON)
  create_test_case(org_url, project, title, steps, priority, area_path, iteration_path, state) -> str (JSON)
  create_work_item_links(org_url, project, source_id, target_ids, link_type) -> str (JSON)
  get_linked_test_cases(org_url, project, work_item_id) -> str (JSON)
"""
import os
import json
import base64
from typing import Dict, List, Optional

import requests


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _get_pat() -> str:
    pat = (os.getenv("AZURE_DEVOPS_PAT") or "").strip()
    if not pat:
        raise ValueError(
            "AZURE_DEVOPS_PAT environment variable is not set. "
            "Set it to your Azure DevOps Personal Access Token."
        )
    return pat


def _make_headers(pat_token: str) -> Dict[str, str]:
    credentials = base64.b64encode(f":{pat_token}".encode()).decode()
    return {
        "Authorization": f"Basic {credentials}",
        "Content-Type": "application/json-patch+json",
        "Accept": "application/json",
    }


# ---------------------------------------------------------------------------
# fetch_work_item
# ---------------------------------------------------------------------------

def fetch_work_item(org_url: str, project_name: str, work_item_id: int) -> str:
    """Fetch work item details and parent feature information."""
    if not work_item_id:
        return json.dumps({"error": "work_item_id is required"})

    headers = _make_headers(_get_pat())
    org_url = org_url.rstrip("/")

    api_url = f"{org_url}/{project_name}/_apis/wit/workitems/{work_item_id}"
    response = requests.get(
        api_url, headers=headers,
        params={"$expand": "relations", "api-version": "7.1"},
        timeout=30,
    )

    if response.status_code != 200:
        return json.dumps({
            "error": f"Failed to fetch work item {work_item_id}. "
                     f"Status: {response.status_code}, Body: {response.text}"
        })

    data = response.json()
    fields = data.get("fields", {})

    work_item = {
        "id": data.get("id"),
        "work_item_type": fields.get("System.WorkItemType", ""),
        "title": fields.get("System.Title", ""),
        "description": fields.get("System.Description", ""),
        "acceptance_criteria": fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", ""),
        "state": fields.get("System.State", ""),
        "priority": fields.get("Microsoft.VSTS.Common.Priority", ""),
        "story_points": fields.get("Microsoft.VSTS.Scheduling.StoryPoints", ""),
        "severity": fields.get("Microsoft.VSTS.Common.Severity", ""),
        "tags": fields.get("System.Tags", ""),
        "assigned_to": (
            fields.get("System.AssignedTo", {}).get("displayName", "")
            if fields.get("System.AssignedTo")
            else ""
        ),
        "area_path": fields.get("System.AreaPath", ""),
        "iteration_path": fields.get("System.IterationPath", ""),
        "created_date": fields.get("System.CreatedDate", ""),
        "changed_date": fields.get("System.ChangedDate", ""),
    }

    result: Dict = {"work_item": work_item, "parent_feature": None}

    # Look for parent feature via Hierarchy-Reverse relation
    for relation in data.get("relations", []):
        if relation.get("rel") == "System.LinkTypes.Hierarchy-Reverse":
            parent_url = relation.get("url")
            try:
                parent_resp = requests.get(
                    f"{parent_url}?api-version=7.1", headers=headers, timeout=30
                )
                if parent_resp.status_code == 200:
                    pf = parent_resp.json()
                    pf_fields = pf.get("fields", {})
                    result["parent_feature"] = {
                        "id": pf.get("id"),
                        "work_item_type": pf_fields.get("System.WorkItemType", ""),
                        "title": pf_fields.get("System.Title", ""),
                        "description": pf_fields.get("System.Description", ""),
                        "acceptance_criteria": pf_fields.get(
                            "Microsoft.VSTS.Common.AcceptanceCriteria", ""
                        ),
                        "state": pf_fields.get("System.State", ""),
                        "area_path": pf_fields.get("System.AreaPath", ""),
                        "iteration_path": pf_fields.get("System.IterationPath", ""),
                    }
            except Exception as exc:
                result["parent_feature_error"] = f"Error fetching parent: {exc}"
            break

    return json.dumps(result, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# create_test_case
# ---------------------------------------------------------------------------

def create_test_case(
    org_url: str,
    project_name: str,
    test_case_title: str,
    test_steps: Optional[List[Dict]] = None,
    priority: int = 2,
    area_path: Optional[str] = None,
    iteration_path: Optional[str] = None,
    state: str = "Design",
) -> str:
    """Create a Test Case work item with properly formatted XML steps."""
    if not test_case_title:
        return json.dumps({"error": "test_case_title is required"})

    headers = _make_headers(_get_pat())
    org_url = org_url.rstrip("/")

    # Build XML step string
    formatted_steps = ""
    if test_steps:
        for i, step in enumerate(test_steps, start=1):
            action = str(step.get("action", ""))
            expected = str(step.get("expected_result", ""))

            def _escape(s: str) -> str:
                return (
                    s.replace("&", "&amp;")
                     .replace("<", "&lt;")
                     .replace(">", "&gt;")
                     .replace('"', "&quot;")
                     .replace("'", "&apos;")
                )

            formatted_steps += (
                f'<step id="{i}" type="ValidateStep">'
                f'<parameterizedString isformatted="false">{_escape(action)}</parameterizedString>'
                f'<parameterizedString isformatted="false">{_escape(expected)}</parameterizedString>'
                f"<description/></step>"
            )

    patch = [
        {"op": "add", "path": "/fields/System.Title", "value": test_case_title},
        {"op": "add", "path": "/fields/System.WorkItemType", "value": "Test Case"},
        {"op": "add", "path": "/fields/System.State", "value": state},
        {"op": "add", "path": "/fields/Microsoft.VSTS.Common.Priority", "value": priority},
    ]

    if formatted_steps:
        patch.append({
            "op": "add",
            "path": "/fields/Microsoft.VSTS.TCM.Steps",
            "value": f'<steps id="0" last="{len(test_steps)}">{formatted_steps}</steps>',
        })
    if area_path:
        patch.append({"op": "add", "path": "/fields/System.AreaPath", "value": area_path})
    if iteration_path:
        patch.append({"op": "add", "path": "/fields/System.IterationPath", "value": iteration_path})

    response = requests.post(
        f"{org_url}/{project_name}/_apis/wit/workitems/$Test%20Case",
        headers=headers,
        params={"api-version": "7.1"},
        json=patch,
        timeout=30,
    )

    if response.status_code not in (200, 201):
        return json.dumps({
            "error": f"Failed to create test case. "
                     f"Status: {response.status_code}, Body: {response.text}"
        })

    created = response.json()
    return json.dumps({
        "success": True,
        "test_case_id": created.get("id"),
        "test_case_title": test_case_title,
        "url": created.get("_links", {}).get("html", {}).get("href", ""),
        "state": state,
        "priority": priority,
        "steps_count": len(test_steps) if test_steps else 0,
    }, indent=2)


# ---------------------------------------------------------------------------
# create_work_item_links
# ---------------------------------------------------------------------------

def create_work_item_links(
    org_url: str,
    project_name: str,
    source_work_item_id: int,
    target_test_case_ids: List[int],
    link_type: str = "Tested by",
) -> str:
    """Create 'Tested by' links between a source work item and one or more test cases."""
    if not source_work_item_id:
        return json.dumps({"error": "source_work_item_id is required"})
    if not target_test_case_ids:
        return json.dumps({"error": "target_test_case_ids is required"})

    headers = _make_headers(_get_pat())
    org_url = org_url.rstrip("/")
    results = []

    for tc_id in target_test_case_ids:
        try:
            payload = [{
                "op": "add",
                "path": "/relations/-",
                "value": {
                    "rel": "Microsoft.VSTS.Common.TestedBy-Forward",
                    "url": f"{org_url}/{project_name}/_apis/wit/workItems/{tc_id}",
                },
            }]
            resp = requests.patch(
                f"{org_url}/{project_name}/_apis/wit/workitems/{source_work_item_id}",
                headers=headers,
                params={"api-version": "7.1"},
                json=payload,
                timeout=30,
            )
            if resp.status_code in (200, 201):
                results.append({
                    "success": True,
                    "source_id": source_work_item_id,
                    "test_case_id": tc_id,
                    "link_type": link_type,
                })
            else:
                results.append({
                    "success": False,
                    "source_id": source_work_item_id,
                    "test_case_id": tc_id,
                    "error": f"Status {resp.status_code}: {resp.text}",
                })
        except Exception as exc:
            results.append({
                "success": False,
                "source_id": source_work_item_id,
                "test_case_id": tc_id,
                "error": str(exc),
            })

    successful = [r for r in results if r.get("success")]
    return json.dumps({
        "summary": {
            "total": len(target_test_case_ids),
            "linked": len(successful),
            "failed": len(results) - len(successful),
        },
        "results": results,
    }, indent=2)


# ---------------------------------------------------------------------------
# get_linked_test_cases
# ---------------------------------------------------------------------------

def get_linked_test_cases(org_url: str, project_name: str, work_item_id: int) -> str:
    """Return all test cases linked via 'Tested by' to a work item."""
    if not work_item_id:
        return json.dumps({"error": "work_item_id is required"})

    headers = _make_headers(_get_pat())
    org_url = org_url.rstrip("/")

    resp = requests.get(
        f"{org_url}/{project_name}/_apis/wit/workitems/{work_item_id}",
        headers=headers,
        params={"$expand": "relations", "api-version": "7.1"},
        timeout=30,
    )

    if resp.status_code != 200:
        return json.dumps({"error": f"Failed to fetch work item. Status: {resp.status_code}"})

    relations = resp.json().get("relations", [])
    linked = []
    for rel in relations:
        if rel.get("rel") == "Microsoft.VSTS.Common.TestedBy-Forward":
            tc_url = rel.get("url")
            tc_id = int(tc_url.split("/")[-1])
            try:
                tc_resp = requests.get(
                    f"{tc_url}?api-version=7.1", headers=headers, timeout=30
                )
                if tc_resp.status_code == 200:
                    tc_fields = tc_resp.json().get("fields", {})
                    linked.append({
                        "id": tc_id,
                        "title": tc_fields.get("System.Title", ""),
                        "state": tc_fields.get("System.State", ""),
                        "priority": tc_fields.get("Microsoft.VSTS.Common.Priority", ""),
                    })
                else:
                    linked.append({"id": tc_id, "error": f"Status {tc_resp.status_code}"})
            except Exception as exc:
                linked.append({"id": tc_id, "error": str(exc)})

    return json.dumps({
        "work_item_id": work_item_id,
        "linked_test_cases_count": len(linked),
        "linked_test_cases": linked,
    }, indent=2)
