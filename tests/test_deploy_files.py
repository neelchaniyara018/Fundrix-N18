"""Static checks on the deployment files (the workflow itself can only run on GitHub)."""
import json, os, re
import pytest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
yaml = pytest.importorskip("yaml")

def wf(): return yaml.safe_load(open(os.path.join(ROOT, ".github/workflows/deploy.yml")))

def test_workflow_triggers_and_permissions():
    w = wf(); on = w.get("on") or w[True]
    assert on["schedule"][0]["cron"] and "workflow_dispatch" in on and "push" in on
    assert w["permissions"] == {"contents": "write"} and w["concurrency"]["cancel-in-progress"] is False
    assert "frontend/**" in on["push"]["paths"]

def test_workflow_never_puts_secrets_in_the_frontend_and_pins_actions():
    text = open(os.path.join(ROOT, ".github/workflows/deploy.yml")).read()
    assert "SERVICE_ROLE" not in text.upper() or "service_role" not in text.lower()
    assert "secrets.CLOUDFLARE_API_TOKEN" in text and "secrets.CLOUDFLARE_ACCOUNT_ID" in text
    for m in re.finditer(r"uses:\s*([\w\-./]+)@(\S+)", text): assert m.group(2).startswith("v"), m.group(0)       # versioned actions
    steps = wf()["jobs"]["build-and-deploy"]["steps"]; names = [s.get("name", s.get("uses", "")) for s in steps]
    assert names.index("Build fund data") < names.index("Save state for tomorrow (single-commit data branch)") < names.index("Deploy to Cloudflare Pages")

def test_state_is_saved_only_after_a_successful_real_build():
    s = next(x for x in wf()["jobs"]["build-and-deploy"]["steps"] if x.get("name", "").startswith("Save state"))
    assert "REFRESH" in s["if"] and "steps.build.outcome == 'success'" in s["if"]

def test_headers_file_has_no_conflicting_duplicate_rules():
    t = open(os.path.join(ROOT, "frontend/_headers")).read(); paths = [l for l in t.splitlines() if l.startswith("/")]
    assert len(paths) == len(set(paths)) and "/data/meta.json" in paths and "X-Content-Type-Options: nosniff" in t

def test_committed_config_has_no_service_role_assignment():
    c = open(os.path.join(ROOT, "frontend/config.js")).read()
    assert not re.search(r"service[_-]?role[\"']?\s*[:=]", c, re.I)                    # a comment warning against it is fine; an assignment is not
