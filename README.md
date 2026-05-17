# RefactorGuard-SC Prototype

`RefactorGuard-SC` is a small proof-of-concept of a self-correcting refactoring pipeline for LLM-assisted code transformation.

The prototype was prepared as a companion artifact for the article on reducing semantic hallucinations during automated refactoring. Its goal is not to be a production framework, but to show the core method in executable form:

- generate a refactoring candidate,
- validate it through several semantic gates,
- convert failures into structured feedback,
- let the next iteration correct the mistake,
- accept only candidates that preserve behavior and improve code quality.

## What this prototype demonstrates

The demo models a typical failure mode of automated refactoring:

- the code is refactored into smaller helper functions,
- the result compiles,
- the regular regression tests still pass,
- but a hidden semantic bug appears on a boundary value,
- differential execution and an invariant catch the regression,
- the next iteration uses that feedback and fixes the candidate.

Concretely, the example uses a discount calculation function where the intended condition is `total >= 100.0`, while the first refactored candidate accidentally changes it to `total > 100.0`.

This is exactly the kind of “looks correct, but changes behavior” error the article calls a semantic hallucination.

## File

- [refactorguard_sc_prototype.py](/Users/artemsuhovcev/Documents/Playground/refactorguard_sc_prototype.py)

## Requirements

- Python 3.10+ is recommended
- No external dependencies are required

The prototype uses only the Python standard library.

## Run

From the directory containing the file:

```bash
python3 refactorguard_sc_prototype.py
```

## Expected behavior

When you run the script, it creates a temporary demo workspace and executes two candidate iterations:

1. Candidate 1 passes compilation and the small regression suite.
2. Candidate 1 fails the `differential` layer on the boundary case `total = 100.0`.
3. A structured feedback object is stored in memory.
4. Candidate 2 is generated with the boundary condition fixed.
5. Candidate 2 passes `compile`, `tests`, `differential`, `invariants`, and `formal`.
6. The pipeline accepts Candidate 2.

Typical output looks like this:

```text
accepted: True
iterations_used: 2
reason: Candidate passed all semantic gates
```

and the feedback memory includes a counterexample similar to:

```text
inputs={'customer_type': 'vip', 'total': 100.0}
expected=10.0
actual=0.0
property=VIP_threshold_inclusive
```

## Method structure

The prototype is intentionally organized around the same concepts used in the article.

### Core data structures

- `RefactorTask`: describes the refactoring goal
- `CandidatePatch`: stores a generated candidate and its changed files
- `FeedbackObject`: carries structured diagnostics for the next iteration
- `CounterExample`: stores the concrete failing input and observed mismatch
- `VerificationContext`: groups tests, invariants, cases, and verification layers
- `AcceptanceDecision`: final result of the guarded loop

### Verification layers

The pipeline uses the following layers:

- `CompileLayer`
  - Checks that changed Python files compile

- `TestLayer`
  - Runs the regression suite

- `DifferentialLayer`
  - Compares baseline and candidate behavior on selected inputs

- `InvariantLayer`
  - Applies hard semantic properties extracted from the task context

- `FormalLayer`
  - Placeholder for bounded formal verification
  - In the prototype it is an extension point and succeeds by default

## Why tests alone are not enough in the demo

The included regression tests are intentionally incomplete:

- they test `vip + 150.0`,
- they test `vip + 1000.0`,
- they test `regular + 150.0`,
- but they do **not** test `vip + 100.0`.

Because of that:

- the first buggy refactor survives normal test execution,
- but it is still rejected by semantic verification.

This is the main point of the prototype.

## Extension points

The file is designed so that each toy component can be replaced with a real one.

### Replace the candidate generator

The demo uses `ScriptedDemoModel`, which returns:

- one intentionally flawed candidate first,
- one corrected candidate after feedback.

In a real system, replace it with an implementation of the `RefactorModel` protocol that:

- calls an LLM,
- edits a worktree,
- returns one or more `CandidatePatch` objects.

### Replace the risk ranker

`HeuristicRiskRanker` is only a placeholder. A real system may rank candidates using:

- AST-diff features,
- dependency graph changes,
- CI failure history,
- learned regression-risk signals.

### Replace the formal checker

`FormalLayer` accepts an external checker callback. In a real version this could call:

- CBMC,
- KLEE,
- OpenJML,
- KeY,
- or another bounded symbolic / deductive verifier.

## How this maps to the article

The article describes a full `RefactorGuard-SC` architecture with:

- semantic bundle construction,
- executable specification extraction,
- invariant mining,
- risk-aware ranking,
- cascade verification,
- self-correction through structured feedback,
- bounded acceptance with escalation to a human reviewer.

This prototype implements the central control loop of that idea in minimal form. It is best read as an executable illustration of the method, not as a complete industrial tool.

## Repository suggestion

If you want to put this on GitHub as a small companion repository, a simple structure is enough:

```text
.
├── README.md
└── refactorguard_sc_prototype.py
```

If you later expand it, the next natural split would be:

```text
.
├── README.md
├── refactorguard_sc_prototype.py
├── examples/
├── layers/
├── models/
└── docs/
```

## Citation note

If you reference this artifact from the article, a simple phrasing is enough, for example:

> Prototype implementation of the RefactorGuard-SC method is available in the accompanying GitHub repository.

If you want, I can also prepare the next step:

- a cleaner GitHub-ready repo layout,
- a short `LICENSE`,
- a `.gitignore`,
- or an English-only version of the README for publication.
