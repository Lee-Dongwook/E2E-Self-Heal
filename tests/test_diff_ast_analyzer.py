from app.preprocess.diff_ast_analyzer import analyze_diff

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
