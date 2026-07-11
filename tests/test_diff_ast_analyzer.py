from app.preprocess.diff_ast_analyzer import analyze_diff
import app.preprocess.diff_ast_analyzer as analyzer_module

SAMPLE_DIFF = """diff --git a/components/SubmitButton.tsx b/components/SubmitButton.tsx
index abc1234..def5678 100644
--- a/components/SubmitButton.tsx
+++ b/components/SubmitButton.tsx
@@ -1,3 +1,3 @@
 export function SubmitButton() {
-  return <button id="old-id" className="btn">Submit</button>
+  return <button id="new-id" className="btn">Submit</button>
 }
"""

MULTI_LINE_DIFF = """diff --git a/components/SubmitButton.tsx b/components/SubmitButton.tsx
index abc1234..def5678 100644
--- a/components/SubmitButton.tsx
+++ b/components/SubmitButton.tsx
@@ -1,10 +1,10 @@
 export function SubmitButton() {
-  return (
-    <button
-      id="old-id"
-      className="btn"
-    >
-      Submit
-    </button>
-  )
+  return (
+    <button
+      id="new-id"
+      className="btn"
+    >
+      Submit
+    </button>
+  )
 }
"""

NESTED_DIFF = """diff --git a/components/Layout.tsx b/components/Layout.tsx
--- a/components/Layout.tsx
+++ b/components/Layout.tsx
@@ -1,3 +1,3 @@
-<div><button id="old" /></div>
+<div><button id="new" /></div>
"""

SPREAD_PROP_DIFF = """diff --git a/components/Input.tsx b/components/Input.tsx
--- a/components/Input.tsx
+++ b/components/Input.tsx
@@ -1,3 +1,3 @@
-<input {...props} type="text" id="old-id" required />
+<input {...props} type="text" id="new-id" required />
"""

EXPRESSION_PROP_DIFF = """diff --git a/components/Button.tsx b/components/Button.tsx
--- a/components/Button.tsx
+++ b/components/Button.tsx
@@ -1,3 +1,3 @@
-<button disabled={isLoading} className={isActive ? "active" : "inactive"} />
+<button disabled={!isLoading} className={isActive ? "active" : "inactive"} />
"""


def test_detects_single_changed_element():
    diffs = analyze_diff(SAMPLE_DIFF)
    assert len(diffs) == 1
    assert diffs[0].file == "components/SubmitButton.tsx"


def test_captures_before_and_after_attributes():
    diff = analyze_diff(SAMPLE_DIFF)[0]
    assert diff.previous["attributes"]["id"] == "old-id"
    assert diff.current["attributes"]["id"] == "new-id"
    assert diff.current["tag"] == "button"


def test_ignores_non_jsx_files():
    non_jsx = SAMPLE_DIFF.replace(".tsx", ".css")
    assert analyze_diff(non_jsx) == []


def test_multi_line_jsx_parsing():
    diffs = analyze_diff(MULTI_LINE_DIFF)
    assert len(diffs) == 1
    assert diffs[0].file == "components/SubmitButton.tsx"
    assert diffs[0].previous["tag"] == "button"
    assert diffs[0].previous["attributes"]["id"] == "old-id"
    assert diffs[0].current["attributes"]["id"] == "new-id"


def test_nested_jsx_parsing():
    diffs = analyze_diff(NESTED_DIFF)
    assert len(diffs) == 2
    assert diffs[0].file == "components/Layout.tsx"
    assert diffs[0].previous["tag"] == "div"
    assert diffs[1].previous["tag"] == "button"
    assert diffs[1].previous["attributes"]["id"] == "old"
    assert diffs[1].current["attributes"]["id"] == "new"


def test_spread_and_boolean_attributes():
    diffs = analyze_diff(SPREAD_PROP_DIFF)
    assert len(diffs) == 1
    assert diffs[0].previous["tag"] == "input"
    assert diffs[0].previous["attributes"]["type"] == "text"
    assert diffs[0].previous["attributes"]["id"] == "old-id"
    assert "required" in diffs[0].previous["attributes"]
    assert diffs[0].previous["attributes"]["required"] == ""
    assert "...props" not in diffs[0].previous["attributes"]


def test_expression_attributes():
    diffs = analyze_diff(EXPRESSION_PROP_DIFF)
    assert len(diffs) == 1
    assert diffs[0].previous["tag"] == "button"
    assert diffs[0].previous["attributes"]["disabled"] == "isLoading"
    assert diffs[0].current["attributes"]["disabled"] == "!isLoading"
    assert diffs[0].previous["attributes"]["className"] == 'isActive ? "active" : "inactive"'


def test_fallback_to_regex_when_tree_sitter_disabled(monkeypatch):
    monkeypatch.setattr(analyzer_module, "_HAS_TREE_SITTER", False)
    diffs = analyze_diff(SAMPLE_DIFF)
    assert len(diffs) == 1
    assert diffs[0].file == "components/SubmitButton.tsx"
    assert diffs[0].previous["attributes"]["id"] == "old-id"
    assert diffs[0].current["attributes"]["id"] == "new-id"


def test_fallback_to_regex_on_tree_sitter_exception(monkeypatch):
    def mock_analyze_tree_sitter(git_diff):
        raise RuntimeError("Parsing failed")

    monkeypatch.setattr(analyzer_module, "_analyze_diff_tree_sitter", mock_analyze_tree_sitter)
    diffs = analyze_diff(SAMPLE_DIFF)
    assert len(diffs) == 1
    assert diffs[0].file == "components/SubmitButton.tsx"
    assert diffs[0].previous["attributes"]["id"] == "old-id"
    assert diffs[0].current["attributes"]["id"] == "new-id"
