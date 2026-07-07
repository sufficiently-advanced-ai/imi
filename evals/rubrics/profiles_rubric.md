# Entity Profile Rubric (narrative-profile generation)

Grades the output of the entity profile prompts
(`app/prompts/{person,project,team}_update.xml`, invoked by
`DomainAwareEntityProcessor._generate_entity_profile`).

Checked-by: **D** = deterministic (`evals/harness/runners/profiles.py`),
**J** = LLM judge (`evals/harness/judge.py`). Item ids are mirrored in
`runners/profiles.py` — keep them in sync when editing.

Scoring: `must_pass_rate` = fraction of MUST items passed (gated metric;
1.0 = fixture fully passes). `should_pass_rate` is informational.
`attribution_violations` (deterministic forbidden hits + a failed PM3) is
trended in history.jsonl as an early failure-mode signal.

The bug this task exists to catch: a subject's profile absorbing a
*co-participant's* activities because they shared a meeting. Each fixture's
transcript contains several people's work; `<grounded_facts>` lists only the
subject's own signals/relationships. A correct profile reflects the grounded
facts and omits everyone else's work.

## MUST (gated)

- [ ] **PM1 (D)** Every `must_mention_keywords` group (facts grounded to the
      subject) is satisfied, and no `forbidden_attribution_keywords` term (a
      co-participant's activity) appears in the profile.
- [ ] **PM2 (D)** The profile contains the type's activity section
      (person/team: "Recent Activities"; project: "Recent Developments").
- [ ] **PM3 (J)** No activity, decision, action item, or responsibility the
      transcript attributes to a different participant is attributed to the
      subject (quote the true owner's transcript line and the offending line).
- [ ] **PM4 (J)** Every fact stated about the subject is supported by the
      transcript — no hallucinations (quote the supporting line).

## SHOULD (informational)

- [ ] **PS1 (J)** Activities listed are the subject's own contributions, not
      the meeting's general agenda or another attendee's work.
- [ ] **PS2 (J)** Relationships/roles named for the subject are supported by the
      transcript, not inferred from mere co-attendance.

## Known baseline failures

Before the attribution-grounding work (signal phase reordered ahead of profile
generation, `<grounded_facts>` block, and the hardened attribution rules in the
`*_update.xml` prompts), the prompt was handed the whole shared-meeting
transcript with no per-activity attribution and asked for "Recent Activities",
so PM1/PM3 failed whenever a subject co-attended a meeting with an active
contributor (e.g. a co-attendee's YC application / website work landing on the
subject's profile). That is the "before" picture this rubric exists to move.
