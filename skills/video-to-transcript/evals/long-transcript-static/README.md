# Static long-transcript workflow evaluation

This is a deterministic, portable fixture evaluation. It does **not** claim
that a language model was run, nor does it measure model quality. The fixture
is a dense, simulated one-hour lesson transcript used to exercise the binding
long-text workflow as static evidence.

The evaluator proves these artifact-level invariants:

- the fixture is longer than 8000 characters and is covered exactly once by
  bounded natural-boundary chunks;
- every chunk carries theme state and fact cards with source locations;
- a fenced code block remains wholly inside one chunk;
- dependent steps spanning chunks remain connected and ordered in the merged
  document; and
- every designated unique source fact occurs exactly once after merge, with no
  duplication or omission.

Reproduce the evidence with Python's standard library only:

```powershell
python -B -S evals/long-transcript-static/run_evaluation.py
```

The command exits nonzero when any invariant fails. The existing unittest
suite runs the same evaluator and asserts the machine-readable report.
