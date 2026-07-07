# Meeting Summary Rubric (finalization output)

Grades the output of the meeting finalization prompt
(`app/prompts/meeting_finalize.xml`, invoked by
`StateSync.finalize_meeting_with_transcript`).

Checked-by: **D** = deterministic (`evals/harness/runners/summary.py`),
**J** = LLM judge (`evals/harness/judge.py`). Item ids are mirrored in
`runners/summary.py` — keep them in sync when editing.

Scoring: `must_pass_rate` = fraction of MUST items passed (gated metric;
1.0 = fixture fully passes). `should_pass_rate` is informational.

## MUST (gated)

- [ ] **M1 (D)** A derivable title exists: a `# Meeting Summary: <title>` line
      or leading `# <title>` heading whose title is non-empty, is not
      "Untitled Meeting", satisfies the fixture's `title_max_words`, and names
      a topic from `title_must_contain_any`.
- [ ] **M2 (D)** The output contains identifiable Decisions and Action Items
      sections (markdown headings or bold labels).
- [ ] **M3 (D)** Every `must_mention_keywords` group from the fixture is
      satisfied, and no `must_not_mention_keywords` term appears.
- [ ] **M4 (J)** Every item presented as a decision is supported by the
      transcript — no hallucinated decisions (quote the supporting line).
- [ ] **M5 (J)** Personal or banter content (jokes, appearance, off-topic
      remarks) does not appear under Decisions or Action Items.

## SHOULD (informational)

- [ ] **S1 (J)** The title names the dominant topic or client rather than a
      generic label ("Weekly Sync", "Meeting Summary").
- [ ] **S2 (J)** The summary opens with a 2-6 sentence narrative overview
      rather than jumping straight into bullet fragments.
- [ ] **S3 (J)** Action items name an owner whenever the transcript states
      one.
- [ ] **S4 (J)** Insights are synthesis (implications, risks, connections),
      not restatements of discussion points.

## Known baseline failures

The current shipping prompt requests a free-form summary with no title line
and no fixed section names, so M1 and often M2 fail at baseline. That is the
"before" picture this rubric exists to move — see the Part 2 Step 5
finalization-schema work.
