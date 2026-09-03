--
name: code-review
description: Default target is the current branch diff vs main.
--

# code-review - E2E healer team review

The team's own review procedure. It layers **things** on top of a generic quality review:

**Diff-scoped.** Review what changed, not the whole tree. Default target is the current branch's diff against `main`.

---

## Review Strategy

**Small diff (≤ ~20 files):** one pass. Read the diff, apply the checklist plus the generic dimensions below.

**Large diff (many files / whole PR):** fan out parallel review agents by area so coverage is real, then synthesize.

---

## Python-Specific Review Checklist

### 1. Type Safety & Annotations

- [ ] Explicit type annotations are used (Python 3.9+ built-in types or `typing` module).
- [ ] Excessive `Any` usages are avoided; `Optional` / `| None` unions are handled explicitly.
- [ ] Static type checks (`mypy` / `pyright`) pass without errors on modified files.

### 2. Performance & Resource Management

- [ ] List comprehensions vs. generator expressions are chosen appropriately for memory scale.
- [ ] No **mutable default arguments** in function signatures (e.g., `def fn(data=[])` is forbidden).
- [ ] Context managers (`with` / `async with`) are strictly used for I/O operations (files, network connections, DB sessions).
- [ ] `async/await` patterns are non-blocking and proper concurrency primitives (ThreadPool/ProcessPool) are used where applicable.

### 3. Exception Handling & Logging

- [ ] Bare `except:` or broad `except Exception:` blocks are strictly avoided unless re-raising or logging tracebacks properly.
- [ ] Domain-specific custom exceptions are raised instead of generic runtime errors.
- [ ] Log messages include relevant context parameters without exposing sensitive user data/PII.

### 4. Code Style, Security & Architecture

- [ ] Code adheres to PEP 8 standards (enforced via `ruff` / `black`).
- [ ] Import order follows the standard layout: `Standard Library -> Third-Party -> Local Application`.
- [ ] Data validation models (`Pydantic` / `dataclasses`) cleanly separate schema definitions from core business logic.
- [ ] Security practices are met (e.g., no hardcoded secrets, no raw SQL string formatting, no unsafe `eval` / `exec`).

---

## Tooling & Static Analysis Integration

Reviewers and agents must verify that the diff passes the following automated Python checks:

| Domain                   | Tool                    | Action on Failure                                           |
| :----------------------- | :---------------------- | :---------------------------------------------------------- |
| **Linting & Formatting** | `ruff`, `black`         | Block PR / request immediate fix                            |
| **Type Checking**        | `mypy`, `pyright`       | Categorize as **Major** or **Critical** severity            |
| **Security Scanning**    | `bandit`                | Categorize hardcoded secrets/flaws as **Critical** severity |
| **Testing & Coverage**   | `pytest`, `coverage.py` | Require test cases for new/modified business logic          |

---

## Report — three local outputs, plus one publish step

Never ask whether to anchor findings inline in the editor — that is the primary surface, the markdown file is the archive, and the receipt is what the push gate reads. Skipping any of the three means the review did not happen.

(GitHub PR comments) is the only step that leaves the machine, so it runs **only when the invocation targets a PR**. See its table.

The two local outputs are written differently: the **report file** (4.2) and **PR comments** (4.3) are English only, for the reviewee on GitHub.

Finding line format, used in the markdown file and in agent hand-offs:

```
SEVERITY (Critical | Major | Minor) | path/to/file.ts:LINE | one-line issue | concrete fix
```

Here's the example format

```markdown
Critical | app/services/user.py:42 | Mutable default argument used | Change `def get_users(filters={})` to `filters=None` and initialize inside the function.
Major | app/api/v1/endpoints.py:87 | Unhandled broad exception swallowing | Remove bare `except:` block and explicitly catch `SQLAlchemyError` with traceback logging.
Minor | app/models/domain.py:15 | Missing return type annotation | Add explicit return type `-> list[User]` to `find_active_users`.
```

---

## Merge Gate Policy & Author Autonomy

### Merge Blockers (Critical & Major):

Only severe issues that violate system integrity, security, core architecture, or runtime stability block the merge. These must be resolved before merging.

### Author Discretion (Minor / Suggestions):

For non-critical findings, style preferences, or non-blocking optimizations, full autonomy is granted to the author. The author retains final ownership and responsibility to decide whether to address them immediately, defer them to follow-up tasks, or leave the code as-is.
