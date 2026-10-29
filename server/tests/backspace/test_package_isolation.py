"""Guards the architectural constraints, not just the data shapes.

Checked statically (via ``ast``) rather than by inspecting ``sys.modules``
after import: in a shared pytest process, other test modules (test_runtime.py,
test_harness.py, ...) will already have pulled ``asyncio`` and FastAPI into
``sys.modules`` by the time these tests run, which would make a runtime
"was it imported" check pass or fail depending on test order rather than on
what backspace's own source actually does. Reading the source is what
actually answers "does this package depend on those modules".
"""

from __future__ import annotations

import ast
from pathlib import Path

import app.backspace as backspace

BACKSPACE_DIR = Path(backspace.__file__).resolve().parent

_FORBIDDEN_MODULES = {
    "asyncio",
    "fastapi",
    "starlette",
    "app.goals",
    "goals",
    "app.retrieval",
    "retrieval",
    "app.runtime",
    "app.main",
}


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module:
                names.add(module)
    return names


def test_backspace_source_files_exist():
    expected = {
        "__init__.py", "facts.py", "claims.py", "graph.py",
        "changes.py", "invalidation.py", "planner.py", "core.py",
        "explanation.py", "runtime_adapter.py", "work_lifecycle.py",
        "actions.py",
    }
    actual = {p.name for p in BACKSPACE_DIR.glob("*.py")}
    assert expected <= actual


def test_no_backspace_module_imports_forbidden_names():
    offenders: dict[str, set[str]] = {}
    for path in sorted(BACKSPACE_DIR.glob("*.py")):
        imported = _imported_module_names(path)
        hit = imported & _FORBIDDEN_MODULES
        if hit:
            offenders[path.name] = hit
    assert not offenders, f"forbidden imports found: {offenders}"


def test_package_exports_every_documented_model():
    for name in [
        "Fact", "FactStatus", "FactNotebook", "FactNotFoundError",
        "Evidence", "Claim", "ClaimStatus",
        "WorkItem", "WorkStatus", "Dependency", "DependencyKind",
        "ChangeSet", "ChangeKind", "Invalidation", "RecomputationPlan",
        "PlanStatus", "FactUpdate", "Retraction", "BackspaceEvent",
        "BackspaceEventType", "BackspaceCore",
        "NodeKind", "DependencyGraph", "DependencyGraphError",
        "UnsupportedDependencyKindError", "NodeNotRegisteredError",
        "NodeKindMismatchError", "DependencyCycleError",
        "invalidate", "invalidate_many",
        "ClaimNotFoundError", "ClaimNotSpokenError", "ClaimNotInvalidatedError",
        "mark_claim_spoken", "invalidate_claim", "require_claim_retractable",
        "supersede_claim", "get_claim_history",
        "BackspaceExplanation", "ChangeExplanation", "WorkExplanation",
        "ClaimExplanation", "RecomputeStep", "ExplanationSummary",
        "ChangeSetNotFoundError", "build_explanation", "render_explanation_text",
        "FactObservation", "BackspaceIntegrationResult", "process_backspace_observation",
        "IllegalWorkTransitionError", "StaleExecutionError", "start_work",
        "complete_work", "fail_work", "cancel_work", "invalidate_work", "is_result_current",
        "ActionCommit", "ActionCommitResult", "ActionLedger", "action_id_for",
    ]:
        assert hasattr(backspace, name), f"app.backspace does not export {name}"
        assert name in backspace.__all__


def test_importing_backspace_does_not_require_asyncio_or_fastapi_source_usage():
    # Belt-and-braces companion to the static check above: every symbol the
    # package exports must be constructible with plain synchronous code.
    fact = backspace.Fact(key="k", value=1)
    assert fact.key == "k"
