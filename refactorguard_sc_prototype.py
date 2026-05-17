"""
RefactorGuard-SC prototype
==========================

A self-contained proof-of-concept for the method described in the article:
generation of refactoring candidates + multi-layer semantic verification +
structured feedback + iterative self-correction.

The file is intentionally dependency-free so it can be published as a
single GitHub artifact and linked directly from the paper.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import textwrap
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol


JsonDict = dict[str, Any]


@dataclass
class RefactorTask:
    name: str
    description: str
    target_module: str
    target_function: str
    min_quality_gain: float = 0.05


@dataclass
class CounterExample:
    inputs: JsonDict
    expected: Any
    actual: Any
    violated_property: str
    trace_delta: str = ""


@dataclass
class FeedbackObject:
    summary: str
    failing_tests: list[str] = field(default_factory=list)
    counterexamples: list[CounterExample] = field(default_factory=list)
    violated_invariants: list[str] = field(default_factory=list)
    suspicious_files: list[str] = field(default_factory=list)
    blocked_transformations: list[str] = field(default_factory=list)


@dataclass
class CandidatePatch:
    candidate_id: str
    workspace: Path
    changed_files: list[Path]
    rationale: str
    quality_gain: float


@dataclass
class LayerResult:
    layer: str
    passed: bool
    message: str
    feedback: FeedbackObject | None = None


@dataclass
class AcceptanceDecision:
    accepted: bool
    candidate: CandidatePatch | None
    iterations_used: int
    history: list[LayerResult]
    feedback_memory: list[FeedbackObject]
    reason: str


@dataclass
class HardInvariant:
    name: str
    description: str
    checker: Callable[[Callable[[JsonDict], Any]], CounterExample | None]


@dataclass
class VerificationContext:
    baseline_workspace: Path
    baseline_module_path: Path
    function_name: str
    test_command: list[str]
    differential_cases: list[JsonDict]
    hard_invariants: list[HardInvariant]
    layers: list["VerificationLayer"]


class RefactorModel(Protocol):
    def generate_candidates(
        self,
        task: RefactorTask,
        context: VerificationContext,
        feedback_memory: list[FeedbackObject],
        k: int,
    ) -> list[CandidatePatch]:
        ...


class RiskRanker(Protocol):
    def rank(
        self,
        candidates: list[CandidatePatch],
        task: RefactorTask,
        context: VerificationContext,
        feedback_memory: list[FeedbackObject],
    ) -> list[CandidatePatch]:
        ...


class VerificationLayer(Protocol):
    name: str

    def run(self, candidate: CandidatePatch, context: VerificationContext) -> LayerResult:
        ...


def run_command(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )


def load_callable(module_path: Path, function_name: str) -> Callable[[JsonDict], Any]:
    module_name = f"dynamic_{module_path.stem}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    func = getattr(module, function_name, None)
    if func is None:
        raise AttributeError(f"{module_path} does not define {function_name}")
    return func


class HeuristicRiskRanker:
    """
    Placeholder for an ML risk scorer.

    In a production system this step may be implemented with a learned model
    over AST-diffs, dependency changes, and prior CI failures. Here we keep
    a simple deterministic ranking heuristic.
    """

    def rank(
        self,
        candidates: list[CandidatePatch],
        task: RefactorTask,
        context: VerificationContext,
        feedback_memory: list[FeedbackObject],
    ) -> list[CandidatePatch]:
        def score(candidate: CandidatePatch) -> tuple[float, int]:
            return (-candidate.quality_gain, len(candidate.changed_files))

        return sorted(candidates, key=score)


class CompileLayer:
    name = "compile"

    def run(self, candidate: CandidatePatch, context: VerificationContext) -> LayerResult:
        for file_path in candidate.changed_files:
            if file_path.suffix != ".py":
                continue
            proc = run_command([sys.executable, "-m", "py_compile", str(file_path)], candidate.workspace)
            if proc.returncode != 0:
                feedback = FeedbackObject(
                    summary="Compilation failed",
                    suspicious_files=[str(file_path.name)],
                    blocked_transformations=["keep syntax and imports valid"],
                )
                return LayerResult(self.name, False, proc.stderr.strip() or "py_compile failed", feedback)
        return LayerResult(self.name, True, "Compilation passed")


class TestLayer:
    name = "tests"

    def run(self, candidate: CandidatePatch, context: VerificationContext) -> LayerResult:
        proc = run_command(context.test_command, candidate.workspace)
        if proc.returncode != 0:
            feedback = FeedbackObject(
                summary="Regression tests failed",
                failing_tests=_extract_test_failures(proc.stdout + "\n" + proc.stderr),
                suspicious_files=[p.name for p in candidate.changed_files],
                blocked_transformations=["do not break explicit test contract"],
            )
            return LayerResult(self.name, False, proc.stdout.strip() or proc.stderr.strip(), feedback)
        return LayerResult(self.name, True, "Regression tests passed")


class DifferentialLayer:
    name = "differential"

    def run(self, candidate: CandidatePatch, context: VerificationContext) -> LayerResult:
        baseline_fn = load_callable(context.baseline_module_path, context.function_name)
        candidate_fn = load_callable(candidate.workspace / context.baseline_module_path.name, context.function_name)

        for case in context.differential_cases:
            expected = baseline_fn(case)
            actual = candidate_fn(case)
            if expected != actual:
                property_name = "behavior_equivalence"
                if case.get("customer_type") == "vip" and case.get("total") == 100.0:
                    property_name = "VIP_threshold_inclusive"
                feedback = FeedbackObject(
                    summary="Differential execution found semantic drift",
                    counterexamples=[
                        CounterExample(
                            inputs=case,
                            expected=expected,
                            actual=actual,
                            violated_property=property_name,
                            trace_delta=f"baseline={expected}, candidate={actual}",
                        )
                    ],
                    violated_invariants=[property_name],
                    suspicious_files=[p.name for p in candidate.changed_files],
                    blocked_transformations=["preserve observable behavior on boundary cases"],
                )
                message = f"Mismatch for inputs={case}: expected {expected!r}, got {actual!r}"
                return LayerResult(self.name, False, message, feedback)

        return LayerResult(self.name, True, "Differential execution passed")


class InvariantLayer:
    name = "invariants"

    def run(self, candidate: CandidatePatch, context: VerificationContext) -> LayerResult:
        candidate_fn = load_callable(candidate.workspace / context.baseline_module_path.name, context.function_name)

        for invariant in context.hard_invariants:
            counterexample = invariant.checker(candidate_fn)
            if counterexample is not None:
                feedback = FeedbackObject(
                    summary=f"Invariant violated: {invariant.name}",
                    counterexamples=[counterexample],
                    violated_invariants=[invariant.name],
                    suspicious_files=[p.name for p in candidate.changed_files],
                    blocked_transformations=["preserve mined and validated invariants"],
                )
                return LayerResult(self.name, False, invariant.description, feedback)

        return LayerResult(self.name, True, "Hard invariants passed")


class FormalLayer:
    """
    Bounded formal layer placeholder.

    In a real system this may call CBMC, KLEE, OpenJML, KeY, or a custom
    symbolic executor. The prototype keeps the extension point explicit and
    returns success by default.
    """

    name = "formal"

    def __init__(self, checker: Callable[[CandidatePatch, VerificationContext], tuple[bool, str]] | None = None):
        self.checker = checker

    def run(self, candidate: CandidatePatch, context: VerificationContext) -> LayerResult:
        if self.checker is None:
            return LayerResult(self.name, True, "No external formal checker configured")

        passed, message = self.checker(candidate, context)
        if passed:
            return LayerResult(self.name, True, message)

        feedback = FeedbackObject(
            summary="Bounded formal check failed",
            suspicious_files=[p.name for p in candidate.changed_files],
            blocked_transformations=["preserve verified path constraints"],
        )
        return LayerResult(self.name, False, message, feedback)


class RefactorGuardSC:
    def __init__(
        self,
        model: RefactorModel,
        risk_ranker: RiskRanker,
        max_iterations: int = 3,
        top_m: int = 1,
    ):
        self.model = model
        self.risk_ranker = risk_ranker
        self.max_iterations = max_iterations
        self.top_m = top_m

    def run(self, task: RefactorTask, context: VerificationContext, k: int = 1) -> AcceptanceDecision:
        feedback_memory: list[FeedbackObject] = []
        history: list[LayerResult] = []

        for iteration in range(1, self.max_iterations + 1):
            candidates = self.model.generate_candidates(task, context, feedback_memory, k)
            ranked_candidates = self.risk_ranker.rank(candidates, task, context, feedback_memory)

            for candidate in ranked_candidates[: self.top_m]:
                candidate_failed = False

                for layer in context.layers:
                    result = layer.run(candidate, context)
                    history.append(result)
                    if not result.passed:
                        candidate_failed = True
                        if result.feedback is not None:
                            feedback_memory.append(result.feedback)
                        break

                if candidate_failed:
                    continue

                if candidate.quality_gain < task.min_quality_gain:
                    feedback_memory.append(
                        FeedbackObject(
                            summary="Candidate preserved behavior but did not improve quality enough",
                            suspicious_files=[p.name for p in candidate.changed_files],
                        )
                    )
                    continue

                return AcceptanceDecision(
                    accepted=True,
                    candidate=candidate,
                    iterations_used=iteration,
                    history=history,
                    feedback_memory=feedback_memory,
                    reason="Candidate passed all semantic gates",
                )

        return AcceptanceDecision(
            accepted=False,
            candidate=None,
            iterations_used=self.max_iterations,
            history=history,
            feedback_memory=feedback_memory,
            reason="Escalate to human reviewer after bounded self-correction budget",
        )


def _extract_test_failures(output: str) -> list[str]:
    failures: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("FAIL:") or stripped.startswith("ERROR:"):
            failures.append(stripped)
    return failures


class ScriptedDemoModel:
    """
    Demonstration-only candidate generator.

    Round 1 returns a superficially clean refactor with a hidden boundary bug.
    If feedback contains the boundary counterexample, round 2 fixes it.
    """

    def __init__(self, root: Path, baseline_module_name: str, test_file_name: str):
        self.root = root
        self.baseline_module_name = baseline_module_name
        self.test_file_name = test_file_name
        self.attempt = 0

    def generate_candidates(
        self,
        task: RefactorTask,
        context: VerificationContext,
        feedback_memory: list[FeedbackObject],
        k: int,
    ) -> list[CandidatePatch]:
        self.attempt += 1
        workspace = self.root / f"candidate_{self.attempt}"
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.mkdir(parents=True, exist_ok=True)

        bug_fixed = self._feedback_mentions_vip_threshold(feedback_memory)

        shutil.copy(context.baseline_workspace / self.test_file_name, workspace / self.test_file_name)
        module_code = self._correct_candidate_code() if bug_fixed else self._buggy_candidate_code()
        module_path = workspace / self.baseline_module_name
        module_path.write_text(module_code, encoding="utf-8")

        rationale = (
            "Refactor with helper extraction; boundary condition repaired after semantic feedback."
            if bug_fixed
            else "Initial readability-oriented refactor with helper extraction."
        )
        quality_gain = 0.18 if bug_fixed else 0.12

        return [
            CandidatePatch(
                candidate_id=f"candidate_{self.attempt}",
                workspace=workspace,
                changed_files=[module_path],
                rationale=rationale,
                quality_gain=quality_gain,
            )
        ]

    @staticmethod
    def _feedback_mentions_vip_threshold(feedback_memory: list[FeedbackObject]) -> bool:
        for feedback in feedback_memory:
            if "VIP_threshold_inclusive" in feedback.violated_invariants:
                return True
            for counterexample in feedback.counterexamples:
                if counterexample.inputs.get("customer_type") == "vip" and counterexample.inputs.get("total") == 100.0:
                    return True
        return False

    @staticmethod
    def _buggy_candidate_code() -> str:
        return textwrap.dedent(
            """
            MAX_DISCOUNT = 30.0


            def _is_discount_eligible(order):
                return order["customer_type"] == "vip" and order["total"] > 100.0


            def _raw_discount(order):
                return min(order["total"] * 0.10, MAX_DISCOUNT)


            def calculate_discount(order):
                if not _is_discount_eligible(order):
                    return 0.0
                return _raw_discount(order)
            """
        ).strip() + "\n"

    @staticmethod
    def _correct_candidate_code() -> str:
        return textwrap.dedent(
            """
            MAX_DISCOUNT = 30.0


            def _is_discount_eligible(order):
                return order["customer_type"] == "vip" and order["total"] >= 100.0


            def _raw_discount(order):
                return min(order["total"] * 0.10, MAX_DISCOUNT)


            def calculate_discount(order):
                if not _is_discount_eligible(order):
                    return 0.0
                return _raw_discount(order)
            """
        ).strip() + "\n"


def write_demo_project(root: Path) -> tuple[Path, Path, str]:
    baseline_workspace = root / "baseline"
    baseline_workspace.mkdir(parents=True, exist_ok=True)

    baseline_code = textwrap.dedent(
        """
        MAX_DISCOUNT = 30.0


        def calculate_discount(order):
            is_vip = order["customer_type"] == "vip"
            total = order["total"]
            if not is_vip:
                return 0.0
            if total < 100.0:
                return 0.0
            return min(total * 0.10, MAX_DISCOUNT)
        """
    ).strip() + "\n"

    test_code = textwrap.dedent(
        """
        import unittest

        from pricing import calculate_discount


        class TestPricing(unittest.TestCase):
            def test_regular_customer_has_no_discount(self):
                self.assertEqual(calculate_discount({"customer_type": "regular", "total": 150.0}), 0.0)

            def test_vip_customer_gets_discount(self):
                self.assertEqual(calculate_discount({"customer_type": "vip", "total": 150.0}), 15.0)

            def test_discount_cap_is_preserved(self):
                self.assertEqual(calculate_discount({"customer_type": "vip", "total": 1000.0}), 30.0)


        if __name__ == "__main__":
            unittest.main()
        """
    ).strip() + "\n"

    module_name = "pricing.py"
    test_name = "test_pricing.py"

    (baseline_workspace / module_name).write_text(baseline_code, encoding="utf-8")
    (baseline_workspace / test_name).write_text(test_code, encoding="utf-8")
    return baseline_workspace, baseline_workspace / module_name, test_name


def vip_threshold_inclusive_invariant(candidate_fn: Callable[[JsonDict], Any]) -> CounterExample | None:
    case = {"customer_type": "vip", "total": 100.0}
    actual = candidate_fn(case)
    expected = 10.0
    if actual != expected:
        return CounterExample(
            inputs=case,
            expected=expected,
            actual=actual,
            violated_property="VIP_threshold_inclusive",
            trace_delta="VIP orders at the threshold must still receive the inclusive discount",
        )
    return None


def build_demo_context(root: Path) -> tuple[RefactorTask, VerificationContext, ScriptedDemoModel]:
    baseline_workspace, baseline_module_path, test_file_name = write_demo_project(root)

    task = RefactorTask(
        name="extract_helpers_from_discount",
        description=(
            "Refactor calculate_discount into smaller helper functions without changing external behavior. "
            "Keep caps, threshold semantics, and regular customer flow intact."
        ),
        target_module=baseline_module_path.name,
        target_function="calculate_discount",
        min_quality_gain=0.10,
    )

    context = VerificationContext(
        baseline_workspace=baseline_workspace,
        baseline_module_path=baseline_module_path,
        function_name="calculate_discount",
        test_command=[sys.executable, "-m", "unittest", "-q"],
        differential_cases=[
            {"customer_type": "regular", "total": 150.0},
            {"customer_type": "vip", "total": 99.99},
            {"customer_type": "vip", "total": 100.0},
            {"customer_type": "vip", "total": 100.01},
            {"customer_type": "vip", "total": 1000.0},
        ],
        hard_invariants=[
            HardInvariant(
                name="VIP_threshold_inclusive",
                description="VIP clients at the 100.0 threshold must still receive the discount.",
                checker=vip_threshold_inclusive_invariant,
            )
        ],
        layers=[
            CompileLayer(),
            TestLayer(),
            DifferentialLayer(),
            InvariantLayer(),
            FormalLayer(),
        ],
    )

    model = ScriptedDemoModel(root=root, baseline_module_name=baseline_module_path.name, test_file_name=test_file_name)
    return task, context, model


def print_decision(decision: AcceptanceDecision) -> None:
    print("\n=== RefactorGuard-SC decision ===")
    print(f"accepted: {decision.accepted}")
    print(f"iterations_used: {decision.iterations_used}")
    print(f"reason: {decision.reason}")
    if decision.candidate is not None:
        print(f"accepted_candidate: {decision.candidate.candidate_id}")
        print(f"workspace: {decision.candidate.workspace}")
        print(f"rationale: {decision.candidate.rationale}")

    print("\nVerification history:")
    for result in decision.history:
        status = "PASS" if result.passed else "FAIL"
        print(f"  [{status}] {result.layer}: {result.message}")

    if decision.feedback_memory:
        print("\nFeedback memory:")
        for idx, feedback in enumerate(decision.feedback_memory, start=1):
            print(f"  feedback #{idx}: {feedback.summary}")
            for counterexample in feedback.counterexamples:
                print(
                    "    counterexample:",
                    f"inputs={counterexample.inputs},",
                    f"expected={counterexample.expected},",
                    f"actual={counterexample.actual},",
                    f"property={counterexample.violated_property}",
                )


def demo() -> None:
    temp_root = Path(tempfile.mkdtemp(prefix="refactorguard_sc_demo_"))
    task, context, model = build_demo_context(temp_root)
    guard = RefactorGuardSC(model=model, risk_ranker=HeuristicRiskRanker(), max_iterations=3, top_m=1)
    decision = guard.run(task, context, k=1)
    print(f"Demo workspace: {temp_root}")
    print_decision(decision)


if __name__ == "__main__":
    demo()
