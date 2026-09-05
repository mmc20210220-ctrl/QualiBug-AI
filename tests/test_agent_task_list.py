from pathlib import Path
import json
import pytest
from ai_test_asset_center.agent_task_store import create_agent_task, list_agent_tasks
from ai_test_asset_center.private_pilot_agent_task_handlers import AgentTaskHandlersMixin


def create(root, tenant="a", project="workspace", goal="Review behavior"):
    return create_agent_task(root, tenant_id=tenant, project_id=project, goal=goal, intent="verify_changes")


def test_list_is_scoped_and_read_only(tmp_path: Path):
    first = create(tmp_path)
    second = create(tmp_path, goal="Review change")
    create(tmp_path, tenant="other")
    create(tmp_path, project="another")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    items = list_agent_tasks(tmp_path, tenant_id="a", project_id="workspace")
    assert [x["task_id"] for x in items] == [second["task_id"], first["task_id"]]
    assert all("events" not in x and x["event_count"] == 1 for x in items)
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    assert list_agent_tasks(tmp_path, tenant_id="missing", project_id="workspace") == []


def test_corrupt_list_fails_visibly(tmp_path: Path):
    task = create(tmp_path)
    path = tmp_path / "platform_workspace/workspace/agent_tasks" / (task["task_id"] + ".json")
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        list_agent_tasks(tmp_path, tenant_id="a", project_id="workspace")


def test_get_collection_uses_authenticated_scope(tmp_path: Path):
    task = create(tmp_path)
    class Handler(AgentTaskHandlersMixin):
        path = "/api/v1/projects/workspace/agent-tasks"
        def _root(self): return tmp_path
        def _init_request_context(self): pass
        def _require_actor(self): return {"role": "qa_lead"}
        def _require_tenant(self, root): return "a"
        def _require_project_scope(self, project): return True
        def _require_known_project(self, project, root): return True
        def _request_tenant(self): return "a"
        def _json(self, payload, status=200): return payload, status
    payload, status = Handler().do_GET()
    assert status == 200
    assert payload["schema_version"] == "qualibug.agent-task-list.v1"
    assert payload["items"][0]["task_id"] == task["task_id"]
    blocked = Handler()
    blocked._require_project_scope = lambda project: False
    assert blocked.do_GET() is None
