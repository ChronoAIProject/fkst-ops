# Evidence index

**The root [SPEC.md](../../../SPEC.md) remains the sole normative owner of
`fkst-ops` behavioural guarantees. This file is evidence index only: it restates
no mechanism fact, root-SPEC clause, or doctrine wording.**

| Claim ID | Disposition | Normative owner | Executable evidence | Falsifying mutation |
|---|---|---|---|---|
| A1 | Mechanism fact; bound | Root `SPEC.md` | `tests/schema/test_mechanism_tools.py::test_skill_mechanism_tool_claims_match_table` | Give A1's tool an environment binding |
| A2 | Mechanism fact; bound | Root `SPEC.md` | `tests/schema/test_mechanism_tools.py::test_skill_mechanism_tool_claims_match_table` | Remove A2's environment binding |
| A3 | Mechanism fact; bound | Root `SPEC.md` | `tests/schema/test_mechanism_tools.py::test_skill_mechanism_tool_claims_match_table` | Remove A3's environment binding |
| A4 | Keep as doctrine; diagnostic prior, not mechanically verifiable | Operating doctrine | none | Change the external exit-code convention |
| A5 | Covered | Root `SPEC.md` | `tests/watch/test_cadence_round.py::test_one_round_writes_one_ledger_line_per_declaration` | Reorder the indexed calls |
| A6 | Covered | Root `SPEC.md` | `tests/schema/test_validator.py::ValidatorTests::test_guard_restart_attempt_limit_is_required_without_a_default`<br>`tests/schema/test_validator.py::ValidatorTests::test_guard_restart_attempt_limit_accepts_zero_and_resolves`<br>`tests/schema/test_validator.py::ValidatorTests::test_guard_restart_attempt_limit_rejects_nonintegers_and_negative_values` | Add a default or widen the accepted domain |
| A7 | Covered after wording correction | Root `SPEC.md` | `tests/watch/test_cadence_round.py::test_one_round_writes_one_ledger_line_per_declaration`<br>`tests/watch/test_cadence_round.py::test_guard_targets_each_deployment_within_one_declaration` | Change the zero/positive branch record shape or calls |
| A8 (first) | Covered | Root `SPEC.md` | `tests/watch/test_cadence_round.py::test_guard_attempts_three_times_then_opens_even_when_restart_fails`<br>`tests/watch/test_cadence_round.py::test_successful_restart_does_not_reset_cross_round_stopped_streak`<br>`tests/watch/test_cadence_round.py::test_running_record_refills_guard_budget` | Change streak, open, or refill transitions |
| A8 (second) | Covered by appended assertion | Root `SPEC.md` | `tests/watch/test_cadence_round.py::test_one_round_writes_one_ledger_line_per_declaration` | Create a cadence state sidecar |
| A9 (first) | Keep as highest-value doctrine; remote runtime fact | Operated remote | none | Change the remote prerequisite |
| A9 (second) | Test rejected; negative source grep is refactor-defeated | Root `SPEC.md` | none | Add branch creation behind any spelling |
| A10 | Keep; unverifiable by construction from the same observation | Operating doctrine | none | Make the observation sufficient |
| A11 | Covered only for paired-pass/crossed-fail; external-repository commit composition is not observed | Root `SPEC.md` | `tests/bootstrap/test_bootstrap.py::BootstrapTest::test_matching_declaration_pin_pairs_pass_and_crossed_pairs_fail_closed_after_reexec` | Change a four-cell outcome |
| A12 | Delete local restatement; reference owner only | `fkst-deployments` authoritative policy | none | Change or relocate the owner policy |
