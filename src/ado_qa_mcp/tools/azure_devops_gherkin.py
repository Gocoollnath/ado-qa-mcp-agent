"""
Azure DevOps Gherkin tool — pure Azure DevOps REST API functions.
No LLM dependency. Used directly by server.py handlers.

Public API:
  fetch_feature(org_url, project, feature_id) -> str (JSON)
  get_child_work_items(org_url, project, feature_id) -> str (JSON)
  get_all_test_cases_for_items(org_url, project, work_item_ids) -> str (JSON)
  create_attachment(org_url, project, work_item_id, file_content, file_name) -> str (JSON)
"""
import os
import json
import base64
import xml.etree.ElementTree as ET
from typing import Dict, List

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
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ---------------------------------------------------------------------------
# fetch_feature
# ---------------------------------------------------------------------------

def fetch_feature(org_url: str, project_name: str, feature_id: int) -> str:
    """Fetch Feature work item details."""
    if not feature_id:
        return json.dumps({"error": "feature_id is required"})

    headers = _make_headers(_get_pat())
    org_url = org_url.rstrip("/")

    resp = requests.get(
        f"{org_url}/{project_name}/_apis/wit/workitems/{feature_id}",
        headers=headers,
        params={"api-version": "7.1"},
        timeout=30,
    )

    if resp.status_code != 200:
        return json.dumps({
            "error": f"Failed to fetch feature {feature_id}. "
                     f"Status: {resp.status_code}, Body: {resp.text}"
        })

    data = resp.json()
    fields = data.get("fields", {})

    feature = {
        "id": data.get("id"),
        "work_item_type": fields.get("System.WorkItemType", ""),
        "title": fields.get("System.Title", ""),
        "description": fields.get("System.Description", ""),
        "acceptance_criteria": fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", ""),
        "state": fields.get("System.State", ""),
        "priority": fields.get("Microsoft.VSTS.Common.Priority", ""),
        "tags": fields.get("System.Tags", ""),
        "area_path": fields.get("System.AreaPath", ""),
        "iteration_path": fields.get("System.IterationPath", ""),
        "value_area": fields.get("Microsoft.VSTS.Common.ValueArea", ""),
        "business_value": fields.get("Microsoft.VSTS.Common.BusinessValue", ""),
    }

    return json.dumps({"feature": feature}, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# get_child_work_items
# ---------------------------------------------------------------------------

def get_child_work_items(org_url: str, project_name: str, feature_id: int) -> str:
    """Get all PBIs and Bugs that are direct children of a Feature."""
    if not feature_id:
        return json.dumps({"error": "feature_id is required"})

    headers = _make_headers(_get_pat())
    org_url = org_url.rstrip("/")

    resp = requests.get(
        f"{org_url}/{project_name}/_apis/wit/workitems/{feature_id}",
        headers=headers,
        params={"$expand": "relations", "api-version": "7.1"},
        timeout=30,
    )

    if resp.status_code != 200:
        return json.dumps({
            "error": f"Failed to fetch feature {feature_id}. Status: {resp.status_code}"
        })

    relations = resp.json().get("relations", [])
    children = []

    for rel in relations:
        if rel.get("rel") == "System.LinkTypes.Hierarchy-Forward":
            child_url = rel.get("url")
            child_id = int(child_url.split("/")[-1])
            try:
                child_resp = requests.get(
                    f"{child_url}?api-version=7.1", headers=headers, timeout=30
                )
                if child_resp.status_code == 200:
                    cf = child_resp.json().get("fields", {})
                    child_type = cf.get("System.WorkItemType", "")
                    if child_type in ("Product Backlog Item", "Bug"):
                        children.append({
                            "id": child_id,
                            "work_item_type": child_type,
                            "title": cf.get("System.Title", ""),
                            "description": cf.get("System.Description", ""),
                            "acceptance_criteria": cf.get(
                                "Microsoft.VSTS.Common.AcceptanceCriteria", ""
                            ),
                            "repro_steps": cf.get("Microsoft.VSTS.TCM.ReproSteps", ""),
                            "state": cf.get("System.State", ""),
                            "priority": cf.get("Microsoft.VSTS.Common.Priority", ""),
                            "story_points": cf.get("Microsoft.VSTS.Scheduling.StoryPoints", ""),
                        })
            except Exception as exc:
                children.append({"id": child_id, "error": str(exc)})

    return json.dumps({
        "feature_id": feature_id,
        "child_work_items_count": len(children),
        "child_work_items": children,
    }, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# get_all_test_cases_for_items
# ---------------------------------------------------------------------------

def get_all_test_cases_for_items(
    org_url: str, project_name: str, work_item_ids: List[int]
) -> str:
    """Get all test cases (with full steps) linked to a list of work items."""
    if not work_item_ids:
        return json.dumps({"error": "work_item_ids is required and must not be empty"})

    headers = _make_headers(_get_pat())
    org_url = org_url.rstrip("/")
    all_test_cases = []

    for wi_id in work_item_ids:
        try:
            resp = requests.get(
                f"{org_url}/{project_name}/_apis/wit/workitems/{wi_id}",
                headers=headers,
                params={"$expand": "relations", "api-version": "7.1"},
                timeout=30,
            )
            if resp.status_code != 200:
                continue

            for rel in resp.json().get("relations", []):
                if rel.get("rel") == "Microsoft.VSTS.Common.TestedBy-Forward":
                    tc_url = rel.get("url")
                    tc_id = int(tc_url.split("/")[-1])
                    try:
                        tc_resp = requests.get(
                            f"{tc_url}?api-version=7.1", headers=headers, timeout=30
                        )
                        if tc_resp.status_code == 200:
                            tc_fields = tc_resp.json().get("fields", {})
                            steps_xml = tc_fields.get("Microsoft.VSTS.TCM.Steps", "")
                            parsed_steps = _parse_test_steps(steps_xml)
                            all_test_cases.append({
                                "test_case_id": tc_id,
                                "linked_to_work_item": wi_id,
                                "title": tc_fields.get("System.Title", ""),
                                "state": tc_fields.get("System.State", ""),
                                "priority": tc_fields.get("Microsoft.VSTS.Common.Priority", ""),
                                "test_steps": parsed_steps,
                                "steps_count": len(parsed_steps),
                            })
                    except Exception as exc:
                        all_test_cases.append({
                            "test_case_id": tc_id,
                            "linked_to_work_item": wi_id,
                            "error": str(exc),
                        })
        except Exception:
            continue

    return json.dumps({
        "total_test_cases": len(all_test_cases),
        "test_cases": all_test_cases,
    }, indent=2, ensure_ascii=False)


def _parse_test_steps(steps_xml: str) -> list:
    """Parse test steps from Azure DevOps XML format."""
    if not steps_xml:
        return []
    steps = []
    try:
        root = ET.fromstring(steps_xml)
        for step in root.findall(".//step"):
            params = step.findall("parameterizedString")
            action = (params[0].text or "") if len(params) >= 1 else ""
            expected = (params[1].text or "") if len(params) >= 2 else ""
            for old, new in [("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"),
                              ("&quot;", '"'), ("&apos;", "'")]:
                action = action.replace(old, new)
                expected = expected.replace(old, new)
            steps.append({
                "step_id": step.get("id", ""),
                "action": action,
                "expected_result": expected,
            })
    except Exception:
        pass
    return steps


# ---------------------------------------------------------------------------
# create_attachment
# ---------------------------------------------------------------------------

def create_attachment(
    org_url: str,
    project_name: str,
    work_item_id: int,
    file_content: str,
    file_name: str,
) -> str:
    """Upload a file and attach it to a work item (two-step: upload then link)."""
    if not work_item_id:
        return json.dumps({"error": "work_item_id is required"})
    if not file_content or not file_name:
        return json.dumps({"error": "file_content and file_name are required"})

    pat = _get_pat()
    credentials = base64.b64encode(f":{pat}".encode()).decode()
    org_url = org_url.rstrip("/")

    try:
        # Step 1: Upload file bytes to attachment store
        upload_resp = requests.post(
            f"{org_url}/{project_name}/_apis/wit/attachments",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/octet-stream",
            },
            params={"fileName": file_name, "api-version": "7.1"},
            data=file_content.encode("utf-8"),
            timeout=30,
        )

        if upload_resp.status_code not in (200, 201):
            return json.dumps({
                "error": f"Attachment upload failed. "
                         f"Status: {upload_resp.status_code}, Body: {upload_resp.text}"
            })

        attachment_url = upload_resp.json().get("url")

        # Step 2: Link attachment to the work item
        link_resp = requests.patch(
            f"{org_url}/{project_name}/_apis/wit/workitems/{work_item_id}",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/json-patch+json",
            },
            params={"api-version": "7.1"},
            json=[{
                "op": "add",
                "path": "/relations/-",
                "value": {
                    "rel": "AttachedFile",
                    "url": attachment_url,
                    "attributes": {"comment": "UI Automation feature file"},
                },
            }],
            timeout=30,
        )

        if link_resp.status_code not in (200, 201):
            return json.dumps({
                "error": f"Attachment link failed. "
                         f"Status: {link_resp.status_code}, Body: {link_resp.text}"
            })

        return json.dumps({
            "success": True,
            "work_item_id": work_item_id,
            "file_name": file_name,
            "attachment_url": attachment_url,
            "message": (
                f"Successfully attached '{file_name}' to work item {work_item_id} "
                "with comment 'UI Automation feature file'."
            ),
        }, indent=2)

    except Exception as exc:
        return json.dumps({"error": f"Exception during attachment: {exc}"})
