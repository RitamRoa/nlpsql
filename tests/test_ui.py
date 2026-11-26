import re
from pathlib import Path

import app

UI = Path(app.__file__).resolve().parent / "ui"


def test_page_is_assembled_from_the_ui_files():
    page = app.HTML_PAGE
    assert "/*__CSS__*/" not in page and "/*__JS__*/" not in page
    assert "NLPSQL Studio" in page
    assert "'__SCHEMA__'" in page and "__SUGGESTIONS__" in page  # filled in per request


def test_every_element_the_script_looks_up_exists_in_the_page():
    js = (UI / "studio.js").read_text(encoding="utf-8")
    html = (UI / "index.html").read_text(encoding="utf-8")
    used = set(re.findall(r"getElementById\('([^']+)'\)", js))
    present = set(re.findall(r'id="([^"]+)"', html)) | set(re.findall(r'id="([^"$]+)"', js))
    assert used - present == set()


def test_page_has_landmarks_and_labels_for_assistive_tech():
    html = (UI / "index.html").read_text(encoding="utf-8")
    for needle in ('<main', '<header', 'role="tablist"', 'aria-live', 'class="skip-link"', 'lang="en"'):
        assert needle in html
    assert "prefers-reduced-motion" in (UI / "studio.css").read_text(encoding="utf-8")


def test_security_summary_reports_masks_and_filters(monkeypatch):
    from core.security import AccessPolicy, RolePolicy

    policy = AccessPolicy(
        roles={"analyst": RolePolicy(tables={
            "employees": {"masked_columns": ["salary"], "row_filter": "department = 'Sales'"},
            "sales": {},
        })},
        enabled=True,
    )
    monkeypatch.setattr(app, "access_policy", policy)
    summary = app._security_summary("analyst", {"tables": ["employees"]})
    assert summary["enforced"] is True
    assert summary["masked_columns"] == ["employees.salary"]
    assert summary["row_filtered_tables"] == ["employees"]


def test_security_summary_when_access_control_is_off(monkeypatch):
    from core.security import AccessPolicy

    monkeypatch.setattr(app, "access_policy", AccessPolicy())
    summary = app._security_summary(None, None)
    assert summary["enforced"] is False and summary["masked_columns"] == []
