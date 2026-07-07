#!/usr/bin/env python3
"""Generate a complete synthetic knowledge base for Meridian Partners consulting firm.

Creates meetings, entities (people, projects, teams), accounts, and signal files
with a coherent Q1 2026 narrative including contradictions for temporal analysis.

Usage:
    python scripts/generate_synthetic_kb.py [output_dir]
    python scripts/generate_synthetic_kb.py /tmp/meridian-partners-kb/
"""

import argparse
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Deterministic UUIDs
# ---------------------------------------------------------------------------

SIGNAL_NAMESPACE = uuid.UUID("a1b2c3d4-0000-4000-8000-000000000000")


def meeting_uuid(n: int) -> str:
    """Deterministic meeting UUID for meeting number n (1-15)."""
    return f"a1b2c3d4-{n:04d}-4000-8000-000000000001"


def bot_uuid(n: int) -> str:
    """Deterministic bot UUID for meeting number n."""
    return f"a1b2c3d4-{n:04d}-4000-8000-000000000002"


def signal_uuid(content: str) -> str:
    """Deterministic UUID5 for signal content."""
    return str(uuid.uuid5(SIGNAL_NAMESPACE, content))


def slug(name: str) -> str:
    return name.lower().replace(" ", "-").replace("&", "and")


def entity_ref(name: str) -> dict:
    """Create an EntityRef dict from a person name."""
    return {"id": f"person-{slug(name)}", "type": "person", "name": name}


# ---------------------------------------------------------------------------
# YAML helpers (no PyYAML dependency — hand-rolled for stdlib-only)
# ---------------------------------------------------------------------------


def yaml_value(v, indent=0) -> str:
    """Render a Python value as inline YAML (for simple scalars and short lists)."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        # Quote if it contains special chars or looks like a date/number
        needs_quote = any(c in v for c in ":#{}[]|>&*!%@`") or v.startswith('"')
        if not needs_quote:
            try:
                float(v)
                needs_quote = True
            except ValueError:
                pass
        if not needs_quote and (v.lower() in ("true", "false", "null", "yes", "no")):
            needs_quote = True
        if needs_quote:
            return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
        return '"' + v + '"'
    if isinstance(v, list):
        if not v:
            return "[]"
        # Short simple lists inline
        if all(isinstance(x, str) for x in v) and sum(len(str(x)) for x in v) < 120:
            items = ", ".join(yaml_value(x) for x in v)
            return f"[{items}]"
        # Multi-line list
        prefix = "  " * indent
        lines = []
        for item in v:
            lines.append(f"{prefix}- {yaml_value(item, indent + 1)}")
        return "\n".join(lines)
    if isinstance(v, dict):
        return _render_yaml_dict(v, indent)
    return str(v)


def _render_yaml_dict(d: dict, indent: int) -> str:
    """Render a dict as block-style YAML at the given indent level."""
    prefix = "  " * indent
    lines = []
    for k, v in d.items():
        if isinstance(v, dict):
            lines.append(f"{prefix}{k}:")
            lines.append(_render_yaml_dict(v, indent + 1))
        elif isinstance(v, list) and v and not (all(isinstance(x, str) for x in v) and sum(len(str(x)) for x in v) < 120):
            lines.append(f"{prefix}{k}:")
            lines.append(yaml_value(v, indent + 1))
        else:
            lines.append(f"{prefix}{k}: {yaml_value(v, indent + 1)}")
    return "\n".join(lines)


def render_frontmatter(data: dict) -> str:
    """Render a dict as YAML frontmatter block."""
    lines = ["---"]
    for k, v in data.items():
        if isinstance(v, dict):
            lines.append(f"{k}:")
            lines.append(_render_yaml_dict(v, 1))
        elif isinstance(v, list) and v and not (all(isinstance(x, str) for x in v) and sum(len(str(x)) for x in v) < 120):
            lines.append(f"{k}:")
            lines.append(yaml_value(v, 1))
        else:
            lines.append(f"{k}: {yaml_value(v, 1)}")
    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------

PEOPLE = [
    {
        "name": "Nadia Reeves",
        "title": "Managing Partner",
        "department": "Leadership",
        "company": "Meridian Partners",
        "contact": "nadia.reeves@meridianpartners.com",
        "valid_from": "2026-01-06T00:00:00Z",
        "valid_to": None,
        "teams": ["team-leadership-team"],
        "projects": ["project-atlas", "project-beacon", "project-vanguard-replatform"],
    },
    {
        "name": "Marcus Chen",
        "title": "Director Client Delivery / Interim Managing Partner",
        "department": "Delivery",
        "company": "Meridian Partners",
        "contact": "marcus.chen@meridianpartners.com",
        "valid_from": "2026-01-06T00:00:00Z",
        "valid_to": None,
        "teams": ["team-atlas-squad", "team-leadership-team"],
        "projects": ["project-atlas", "project-vanguard-replatform", "project-polaris-onboarding"],
    },
    {
        "name": "Priya Kapoor",
        "title": "Senior Consultant",
        "department": "Consulting",
        "company": "Meridian Partners",
        "contact": "priya.kapoor@meridianpartners.com",
        "valid_from": "2026-01-06T00:00:00Z",
        "valid_to": None,
        "teams": ["team-atlas-squad"],
        "projects": ["project-atlas", "project-beacon", "project-polaris-onboarding"],
    },
    {
        "name": "Tom Halstead",
        "title": "Engagement Lead, Vanguard",
        "department": "Delivery",
        "company": "Meridian Partners",
        "contact": "tom.halstead@meridianpartners.com",
        "valid_from": "2026-01-06T00:00:00Z",
        "valid_to": "2026-02-28T23:59:59Z",
        "teams": ["team-beacon-squad"],
        "projects": ["project-vanguard-replatform"],
    },
    {
        "name": "Elena Voss",
        "title": "Senior Consultant",
        "department": "Consulting",
        "company": "Meridian Partners",
        "contact": "elena.voss@meridianpartners.com",
        "valid_from": "2026-01-06T00:00:00Z",
        "valid_to": None,
        "teams": ["team-atlas-squad"],
        "projects": ["project-atlas", "project-vanguard-replatform"],
    },
    {
        "name": "Derek Osman",
        "title": "Junior Consultant",
        "department": "Consulting",
        "company": "Meridian Partners",
        "contact": "derek.osman@meridianpartners.com",
        "valid_from": "2026-01-06T00:00:00Z",
        "valid_to": None,
        "teams": ["team-atlas-squad"],
        "projects": ["project-beacon", "project-atlas", "project-polaris-onboarding"],
    },
    {
        "name": "Rachel Lin",
        "title": "Finance & Operations Manager",
        "department": "Operations",
        "company": "Meridian Partners",
        "contact": "rachel.lin@meridianpartners.com",
        "valid_from": "2026-01-06T00:00:00Z",
        "valid_to": None,
        "teams": ["team-leadership-team"],
        "projects": ["project-beacon"],
    },
    {
        "name": "Sam Okoro",
        "title": "VP Sales",
        "department": "Sales",
        "company": "Meridian Partners",
        "contact": "sam.okoro@meridianpartners.com",
        "valid_from": "2026-01-06T00:00:00Z",
        "valid_to": None,
        "teams": ["team-leadership-team"],
        "projects": ["project-polaris-onboarding"],
    },
    {
        "name": "Jess Nolan",
        "title": "VP Product, Vanguard Group",
        "department": "Product",
        "company": "Vanguard Group",
        "contact": "jess.nolan@vanguardgroup.com",
        "valid_from": "2026-01-27T00:00:00Z",
        "valid_to": None,
        "teams": [],
        "projects": ["project-vanguard-replatform"],
    },
    {
        "name": "Kai Novak",
        "title": "CTO, Polaris Health",
        "department": "Technology",
        "company": "Polaris Health",
        "contact": "kai.novak@polarishealth.com",
        "valid_from": "2026-02-20T00:00:00Z",
        "valid_to": None,
        "teams": [],
        "projects": ["project-polaris-onboarding"],
    },
]

PEOPLE_BY_NAME = {p["name"]: p for p in PEOPLE}

# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

PROJECTS = [
    {
        "name": "Project Atlas",
        "id": "project-atlas",
        "status": "on_track",
        "belongs_to_account": ["account-northstar-retail"],
        "focus_areas": ["data strategy", "data migration", "data remediation"],
        "key_technologies": ["Python", "dbt", "Snowflake", "Airflow"],
        "valid_from": "2026-01-06T00:00:00Z",
    },
    {
        "name": "Project Beacon",
        "id": "project-beacon",
        "status": "paused",
        "belongs_to_account": ["account-meridian-internal"],
        "focus_areas": ["AI tooling", "internal productivity", "automation"],
        "key_technologies": ["Python", "LangChain", "Claude API", "FastAPI"],
        "valid_from": "2026-01-06T00:00:00Z",
    },
    {
        "name": "Vanguard Replatform",
        "id": "project-vanguard-replatform",
        "status": "at_risk",
        "belongs_to_account": ["account-vanguard-group"],
        "focus_areas": ["platform modernization", "API migration", "mobile"],
        "key_technologies": ["TypeScript", "React Native", "Node.js", "PostgreSQL"],
        "valid_from": "2026-01-06T00:00:00Z",
    },
    {
        "name": "Polaris Onboarding",
        "id": "project-polaris-onboarding",
        "status": "active",
        "belongs_to_account": ["account-polaris-health"],
        "focus_areas": ["data platform consolidation", "HIPAA compliance", "assessment"],
        "key_technologies": ["Python", "AWS", "Terraform", "PostgreSQL"],
        "valid_from": "2026-02-20T00:00:00Z",
    },
]

# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------

TEAMS = [
    {
        "name": "Atlas Squad",
        "id": "team-atlas-squad",
        "members": ["person-marcus-chen", "person-priya-kapoor", "person-elena-voss", "person-derek-osman"],
        "lead": "person-marcus-chen",
        "projects": ["project-atlas"],
    },
    {
        "name": "Beacon Squad",
        "id": "team-beacon-squad",
        "members": ["person-tom-halstead", "person-priya-kapoor", "person-derek-osman"],
        "lead": "person-tom-halstead",
        "projects": ["project-beacon"],
    },
    {
        "name": "Leadership Team",
        "id": "team-leadership-team",
        "members": ["person-nadia-reeves", "person-marcus-chen", "person-sam-okoro", "person-rachel-lin"],
        "lead": "person-nadia-reeves",
        "projects": [],
    },
]

# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

ACCOUNTS = [
    {
        "name": "Northstar Retail",
        "slug": "northstar-retail",
        "industry": "Retail",
        "projects": ["project-atlas"],
        "primary_contact": "person-marcus-chen",
        "description": "Major retail client engaged for enterprise data strategy and migration. Northstar Retail is undergoing a comprehensive data modernization initiative with Meridian Partners leading the data strategy, migration, and remediation workstreams.",
    },
    {
        "name": "Vanguard Group",
        "slug": "vanguard-group",
        "industry": "Financial Services",
        "projects": ["project-vanguard-replatform"],
        "primary_contact": "person-elena-voss",
        "description": "Financial services client engaged for platform modernization. The Vanguard Replatform project involves migrating legacy systems to modern architecture with API compatibility and mobile modernization.",
    },
    {
        "name": "Polaris Health",
        "slug": "polaris-health",
        "industry": "Healthcare",
        "projects": ["project-polaris-onboarding"],
        "primary_contact": "person-sam-okoro",
        "description": "New healthcare client onboarded in February 2026. Polaris Health requires data platform consolidation across 3 legacy systems with strict HIPAA compliance requirements.",
    },
    {
        "name": "Meridian Internal",
        "slug": "meridian-internal",
        "industry": "Internal",
        "projects": ["project-beacon"],
        "primary_contact": "person-rachel-lin",
        "description": "Internal account for Meridian Partners projects. Currently houses the Beacon AI tooling initiative which is building internal productivity and automation tools.",
    },
]

# ---------------------------------------------------------------------------
# Meetings
# ---------------------------------------------------------------------------

MEETINGS = [
    {
        "num": 1,
        "date": "2026-01-06",
        "title": "Q1 Kickoff: Team Assignments & Project Priorities",
        "participants": ["Nadia Reeves", "Marcus Chen", "Priya Kapoor", "Tom Halstead", "Elena Voss", "Derek Osman", "Rachel Lin", "Sam Okoro"],
        "accounts": ["Northstar Retail", "Vanguard Group", "Meridian Internal"],
        "projects": ["Project Atlas", "Project Beacon", "Vanguard Replatform"],
        "signals": [
            {"type": "decision", "content": "Derek Osman assigned to Beacon project starting January 13", "entities": ["person-derek-osman", "project-beacon"]},
            {"type": "decision", "content": "Q1 budget allocations confirmed: Atlas $180K, Vanguard $120K, Beacon $45K", "entities": ["project-atlas", "project-beacon", "project-vanguard-replatform"]},
            {"type": "action_item", "content": "Sam to follow up on three prospective clients by January 20", "entities": ["person-sam-okoro"], "owner": "Sam Okoro", "status": "open"},
            {"type": "key_point", "content": "Atlas project is on track for March deliverable", "entities": ["project-atlas"]},
            {"type": "key_point", "content": "Weekly status cadence established for all three active projects", "entities": ["project-atlas", "project-beacon", "project-vanguard-replatform"]},
            {"type": "insight", "content": "Resource allocation is tight across all projects and any delays could create cascading staffing issues", "entities": ["project-atlas", "project-beacon", "project-vanguard-replatform"]},
            {"type": "key_point", "content": "Sam Okoro has three prospective clients in pipeline including a healthcare company", "entities": ["person-sam-okoro"]},
            {"type": "insight", "content": "The team is well-positioned for Q1 with clear project ownership but budget flexibility is limited", "entities": ["project-atlas", "project-beacon", "project-vanguard-replatform"]},
        ],
        "body": """# Q1 Kickoff: Team Assignments & Project Priorities

## Meeting Overview
The Q1 2026 kickoff meeting established team assignments, budget allocations, and project priorities for Meridian Partners. Nadia Reeves led the session with all senior team members present, setting the strategic direction for the quarter.

## Key Discussion Points
- Q1 budget allocations were finalized across all three active projects
- Derek Osman was formally assigned to the Beacon project for internal AI tooling
- Atlas project confirmed on track for March deliverable with Northstar Retail
- Sam Okoro presented pipeline of three prospective new clients
- Vanguard Replatform scope and timeline reviewed with Tom Halstead

## Decisions Made
1. Derek Osman assigned to Beacon project starting January 13
2. Q1 budget allocations confirmed: Atlas $180K, Vanguard $120K, Beacon $45K
3. Weekly status cadence established for all three projects

## Action Items
1. Sam Okoro to follow up on three prospective clients by January 20
2. Tom Halstead to prepare Vanguard expansion proposal by January 27
3. Marcus Chen to finalize Atlas sprint plan for Phase 1

## Important Insights
- The team is well-positioned for Q1 with clear project ownership and budget clarity
- Sam's sales pipeline could introduce new revenue streams mid-quarter
- Resource allocation is tight and any project delays could create cascading issues

## Transcript

**Nadia Reeves**: Welcome everyone. Let's kick off our Q1 planning session. I want to start by discussing our project priorities for the quarter.

**Marcus Chen**: Thanks Nadia. I've been reviewing the Atlas project timeline and I'm confident we can hit our March deliverable. The data migration is ready to start.

**Priya Kapoor**: I agree with Marcus. The Northstar data set looks clean enough for phase one. I'd estimate about 15% will need remediation though.

**Nadia Reeves**: Good. Let's talk budget. Rachel, can you walk us through the allocations?

**Rachel Lin**: Sure. We're looking at Atlas at $180K, Vanguard at $120K, and Beacon at $45K. That's the full Q1 envelope.

**Tom Halstead**: The Vanguard allocation should cover our current scope, but if the scope expansion conversation goes anywhere, we'll need to revisit.

**Nadia Reeves**: Understood. Let's cross that bridge when we get there. Derek, we want you on Beacon starting January 13. Priya's been doing double duty and we need dedicated capacity.

**Derek Osman**: Happy to take that on. I've been reading up on the LangChain architecture Priya set up and I think I can contribute quickly.

**Priya Kapoor**: I'll do a proper handoff with Derek next week. The core framework is solid, it just needs someone focused on it full-time.

**Sam Okoro**: Quick pipeline update from my end. I've got three prospective clients I'm working, including a healthcare company that's very interested in data platform work. Should know more by the 20th.

**Nadia Reeves**: That's exciting, Sam. Healthcare could be a great vertical for us. Keep us posted.

**Marcus Chen**: One thing I want to flag. Resource allocation is tight across all projects. If any one project slips, it could create cascading staffing issues across the board.

**Elena Voss**: Marcus is right. We don't have a lot of bench capacity this quarter. We should establish a weekly status cadence so we catch problems early.

**Nadia Reeves**: Agreed. Let's do weekly status updates for all three projects. Tom, can you have the Vanguard expansion proposal ready by January 27?

**Tom Halstead**: Absolutely. I'll have the full proposal with cost estimates and timeline ready for review.

**Rachel Lin**: I'll track budget burn weekly so we can spot any overruns early. The $45K Beacon allocation is the tightest one.

**Nadia Reeves**: Perfect. Marcus, please finalize the Atlas sprint plan for Phase 1. Let's make Q1 count, everyone.

**Marcus Chen**: Will do. I'll have it circulated by end of week.

**Sam Okoro**: One more thing. The healthcare lead is at a company called Polaris Health. They have three legacy data systems they need consolidated. Could be a significant engagement.

**Nadia Reeves**: Interesting. That aligns well with our Atlas expertise. Keep nurturing that one, Sam.

**Derek Osman**: Quick question on Beacon. What's the primary deliverable we're targeting this quarter?

**Priya Kapoor**: The AI-assisted document analysis tool. We have the prototype but need to productionize it and build the API layer.

**Nadia Reeves**: All right, I think we have clear ownership and priorities. Let's execute. Thank you all.""",
    },
    {
        "num": 2,
        "date": "2026-01-13",
        "title": "Atlas Sprint Review: Data Migration Progress",
        "participants": ["Marcus Chen", "Priya Kapoor", "Elena Voss", "Derek Osman"],
        "accounts": ["Northstar Retail"],
        "projects": ["Project Atlas"],
        "signals": [
            {"type": "key_point", "content": "Atlas project phase 1 completed ahead of schedule", "entities": ["project-atlas"]},
            {"type": "decision", "content": "Priya to lead data remediation workstream for Northstar", "entities": ["person-priya-kapoor", "account-northstar-retail"]},
            {"type": "action_item", "content": "Elena to draft change management playbook by January 24", "entities": ["person-elena-voss"], "owner": "Elena Voss", "status": "open"},
            {"type": "key_point", "content": "Data quality metrics exceeded initial benchmarks with 97% accuracy on migrated records", "entities": ["project-atlas", "account-northstar-retail"]},
            {"type": "insight", "content": "Early completion of Phase 1 builds confidence in the Atlas timeline and could free resources for other projects", "entities": ["project-atlas"]},
            {"type": "action_item", "content": "Derek to document Phase 1 migration runbook by January 20", "entities": ["person-derek-osman"], "owner": "Derek Osman", "status": "open"},
            {"type": "key_point", "content": "Phase 2 timeline moved up by one week given Phase 1 early completion", "entities": ["project-atlas"]},
            {"type": "insight", "content": "Priya's deep data expertise makes her ideal for the remediation workstream but creates single-point-of-failure risk", "entities": ["person-priya-kapoor", "project-atlas"]},
        ],
        "body": """# Atlas Sprint Review: Data Migration Progress

## Meeting Overview
The Atlas squad reviewed Phase 1 data migration progress for Northstar Retail. The team reported completion ahead of schedule, and Priya Kapoor was designated to lead the data remediation workstream.

## Key Discussion Points
- Phase 1 data migration completed two days ahead of schedule
- Data quality metrics exceeded initial benchmarks
- Priya Kapoor proposed remediation framework for legacy data issues
- Elena Voss outlined change management requirements for Northstar stakeholders

## Decisions Made
1. Priya Kapoor to lead data remediation workstream for Northstar Retail
2. Phase 2 timeline moved up by one week given Phase 1 early completion

## Action Items
1. Elena Voss to draft change management playbook by January 24
2. Priya Kapoor to begin data quality assessment on remediation workstream
3. Derek Osman to document Phase 1 migration runbook

## Important Insights
- Early completion of Phase 1 builds confidence in the Atlas timeline
- Priya's deep data expertise makes her ideal for the remediation workstream
- Change management will be critical for Northstar stakeholder buy-in

## Transcript

**Marcus Chen**: All right team, let's review where we stand on Phase 1. Priya, you want to kick us off?

**Priya Kapoor**: Happy to. Phase 1 data migration is complete. We finished two days ahead of schedule. The quality metrics look great, 97% accuracy on migrated records, which exceeded our initial benchmark of 95%.

**Marcus Chen**: That's excellent. What about the remaining 3%?

**Priya Kapoor**: Mostly edge cases in the legacy customer records. Encoding issues, some duplicate detection that needs manual review. This is exactly the kind of thing the remediation workstream needs to tackle.

**Elena Voss**: I've been talking to the Northstar stakeholders, and they're pleased with the progress. But they're nervous about the rollout. We need a proper change management playbook.

**Marcus Chen**: Agreed. Elena, can you have a draft ready by January 24? We want to get ahead of any stakeholder concerns.

**Elena Voss**: Absolutely. I'll include communication templates and training schedules for their team.

**Derek Osman**: Quick question. Since Phase 1 went well, are we moving Phase 2 up?

**Marcus Chen**: Good thinking, Derek. Yes, let's move the Phase 2 start up by one week. We have the momentum, let's keep it going.

**Priya Kapoor**: I'd like to formally propose that I lead the data remediation workstream. I've identified about 15% of the Northstar dataset that needs cleanup, and I have a framework in mind for how to approach it systematically.

**Marcus Chen**: That makes sense. Priya, you have the deepest data quality expertise on the team. Consider yourself the remediation lead.

**Elena Voss**: One concern. If Priya is heads-down on remediation, that's a lot of critical knowledge in one person's hands. Should we think about cross-training?

**Marcus Chen**: Good point. Derek, I want you to document the Phase 1 migration runbook by the 20th. That way we have institutional knowledge captured even if people move around.

**Derek Osman**: On it. I'll document the pipeline configuration, the validation steps, everything.

**Priya Kapoor**: I can review Derek's runbook and fill in any gaps. The dbt models are the most nuanced part.

**Marcus Chen**: Perfect. Let's keep this pace up. If Phase 2 goes as well as Phase 1, we'll be in great shape for the March deliverable.

**Elena Voss**: The Northstar team meets on Thursdays. I'll schedule the change management walkthrough for next week so they have time to prepare.

**Marcus Chen**: Great initiative. All right, let's wrap up. Strong start to the quarter, team.""",
    },
    {
        "num": 3,
        "date": "2026-01-20",
        "title": "Vanguard Account Status & Expansion Discussion",
        "participants": ["Tom Halstead", "Sam Okoro", "Marcus Chen", "Nadia Reeves"],
        "accounts": ["Vanguard Group"],
        "projects": ["Vanguard Replatform"],
        "signals": [
            {"type": "decision", "content": "Approved exploration of Vanguard scope expansion to include mobile modernization", "entities": ["project-vanguard-replatform", "account-vanguard-group"]},
            {"type": "key_point", "content": "Vanguard client satisfaction is high and Tom Halstead reports strong relationship", "entities": ["person-tom-halstead", "account-vanguard-group"]},
            {"type": "key_point", "content": "Sam Okoro has a warm lead at a healthcare company interested in data platform work", "entities": ["person-sam-okoro"]},
            {"type": "insight", "content": "Vanguard expansion represents significant upsell opportunity with low risk given existing trust", "entities": ["project-vanguard-replatform", "account-vanguard-group"]},
            {"type": "key_point", "content": "Mobile modernization could add 85K to the Vanguard engagement value", "entities": ["project-vanguard-replatform"]},
            {"type": "insight", "content": "Resource allocation for expanded Vanguard scope needs careful planning to avoid overextending the team", "entities": ["project-vanguard-replatform"]},
            {"type": "action_item", "content": "Tom Halstead to draft Vanguard expansion proposal by January 27", "entities": ["person-tom-halstead"], "owner": "Tom Halstead", "status": "open"},
            {"type": "action_item", "content": "Sam to advance healthcare company conversation to introductory meeting", "entities": ["person-sam-okoro"], "owner": "Sam Okoro", "status": "open"},
        ],
        "body": """# Vanguard Account Status & Expansion Discussion

## Meeting Overview
Review of the Vanguard Group account with discussion of scope expansion opportunities. Tom Halstead reported strong client satisfaction and Sam Okoro shared pipeline updates including a promising healthcare lead.

## Key Discussion Points
- Vanguard client satisfaction remains high with strong Tom Halstead relationship
- Mobile modernization identified as natural scope expansion opportunity
- Sam Okoro reported warm lead at healthcare company for data platform work
- Budget implications of Vanguard expansion discussed with Rachel's input

## Decisions Made
1. Approved exploration of Vanguard scope expansion to include mobile modernization
2. Tom to prepare formal expansion proposal for client review

## Action Items
1. Tom Halstead to draft Vanguard expansion proposal by January 27
2. Sam Okoro to advance healthcare company conversation to introductory meeting

## Important Insights
- Vanguard expansion represents significant upsell opportunity with low risk
- Sam's healthcare lead could become Q1's biggest new business win
- Resource allocation for expanded Vanguard scope needs careful planning

## Transcript

**Nadia Reeves**: Tom, let's start with the Vanguard status. How's the relationship looking?

**Tom Halstead**: Really strong. Jess Nolan's team has been responsive, and they're happy with the progress on the replatform. Client satisfaction is high across the board.

**Marcus Chen**: That's good to hear. Any technical concerns on the replatform side?

**Tom Halstead**: Nothing major. The API migration is on track. But here's where it gets interesting. They've started asking about mobile modernization. Their mobile app is on a legacy framework and they know it needs attention.

**Nadia Reeves**: Mobile modernization. That would be a natural extension of the replatform work, wouldn't it?

**Tom Halstead**: Exactly. We're already in the codebase, we understand their architecture. I think we could add mobile modernization for around $85K on top of the current engagement.

**Marcus Chen**: That's a solid upsell. But we need to think about staffing. We don't have a deep React Native bench right now.

**Tom Halstead**: I know, but the mobile work wouldn't start until late Q1 at the earliest. We have time to plan.

**Nadia Reeves**: I'm inclined to approve the exploration. Tom, put together a formal expansion proposal by January 27 and we'll review it.

**Tom Halstead**: Will do. I'll include the technical scope, timeline, and resource requirements.

**Sam Okoro**: Before we move on, I have a pipeline update. The healthcare company I mentioned at kickoff, Polaris Health, is very warm. Their CTO reached out directly last week asking about data platform consolidation.

**Nadia Reeves**: That's promising. What's their situation?

**Sam Okoro**: They have three legacy data systems that need to be consolidated. They're in healthcare, so HIPAA compliance is a big factor. It's right in our wheelhouse given what we're doing with Atlas.

**Marcus Chen**: The data platform expertise we're building on Atlas would translate directly. That's a strong positioning angle.

**Nadia Reeves**: Absolutely. Sam, get that introductory meeting scheduled. I want to move on this before someone else does.

**Sam Okoro**: Already working on it. Targeting an intro meeting in February. Their CTO, Kai Novak, seems eager to move.

**Tom Halstead**: One thing to flag on Vanguard. If we expand the scope, I'm going to need additional support. I'm managing the full engagement solo right now.

**Nadia Reeves**: Noted. We'll address resource allocation when we review your proposal. We need to be careful not to overextend.

**Marcus Chen**: Agreed. Let's not spread ourselves too thin. But both opportunities look compelling.

**Nadia Reeves**: Good meeting. Tom, proposal by the 27th. Sam, get that Polaris intro on the calendar.""",
    },
    {
        "num": 4,
        "date": "2026-01-27",
        "title": "Vanguard Replatform: Technical Deep Dive with Client",
        "participants": ["Tom Halstead", "Marcus Chen", "Jess Nolan"],
        "accounts": ["Vanguard Group"],
        "projects": ["Vanguard Replatform"],
        "signals": [
            {"type": "key_point", "content": "Jess Nolan VP Product at Vanguard Group first engagement with Meridian team", "entities": ["person-jess-nolan", "account-vanguard-group"]},
            {"type": "decision", "content": "API backward compatibility confirmed and will be maintained during replatform with no breaking changes", "entities": ["project-vanguard-replatform"]},
            {"type": "action_item", "content": "Marcus to produce API compatibility impact assessment by February 7", "entities": ["person-marcus-chen"], "owner": "Marcus Chen", "status": "open"},
            {"type": "key_point", "content": "Vanguard has 47 external API consumers that depend on backward compatibility", "entities": ["project-vanguard-replatform", "account-vanguard-group"]},
            {"type": "insight", "content": "API backward compatibility is a firm requirement that constrains technical options significantly", "entities": ["project-vanguard-replatform"]},
            {"type": "key_point", "content": "Jess Nolan validated the replatform technical architecture approach", "entities": ["person-jess-nolan", "project-vanguard-replatform"]},
            {"type": "action_item", "content": "Tom to schedule follow-up technical review with Vanguard engineering in two weeks", "entities": ["person-tom-halstead"], "owner": "Tom Halstead", "status": "open"},
            {"type": "insight", "content": "Jess Nolan's direct engagement signals Vanguard's executive commitment to the replatform success", "entities": ["person-jess-nolan", "account-vanguard-group"]},
        ],
        "body": """# Vanguard Replatform: Technical Deep Dive with Client

## Meeting Overview
Technical deep dive with Jess Nolan, VP Product at Vanguard Group, marking her first direct engagement with the Meridian team. The session focused on API compatibility requirements and replatform technical architecture.

## Key Discussion Points
- Jess Nolan introduced as VP Product at Vanguard Group, key technical stakeholder
- API backward compatibility requirements discussed in detail
- Replatform architecture reviewed with focus on zero-downtime migration
- Mobile modernization timeline aligned with core replatform milestones

## Decisions Made
1. API backward compatibility confirmed and will be maintained during replatform with no breaking changes
2. Technical architecture approach validated by Jess Nolan

## Action Items
1. Marcus Chen to produce API compatibility impact assessment by February 7
2. Tom Halstead to schedule follow-up technical review in two weeks

## Important Insights
- Jess Nolan's engagement signals Vanguard's commitment to the replatform
- API backward compatibility is a firm requirement that constrains technical options
- The team must rigorously track compatibility as the replatform progresses

## Transcript

**Tom Halstead**: Jess, thanks for joining us today. I wanted to bring Marcus Chen in for this deep dive since he oversees our technical delivery.

**Jess Nolan**: Great to finally meet you, Marcus. I've heard good things about the Atlas work your team is doing. Impressive data migration at that scale.

**Marcus Chen**: Thanks, Jess. We're excited about the Vanguard replatform. Let me walk you through our proposed architecture.

**Jess Nolan**: Before we dive in, I need to be very clear about one thing. We have 47 external API consumers. API backward compatibility is non-negotiable. No breaking changes, period.

**Tom Halstead**: Understood completely. We've designed the replatform with a compatibility layer specifically for this.

**Marcus Chen**: Right. Our approach uses API versioning with a shim layer. The new platform serves all existing endpoints through the compatibility shim while we build the v2 API in parallel.

**Jess Nolan**: Walk me through the shim layer. How do you handle schema changes?

**Marcus Chen**: The shim translates between the legacy schema and the new internal models. We maintain a comprehensive compatibility test suite that runs against all 47 consumer contracts.

**Jess Nolan**: That's exactly what I needed to hear. And what about the mobile modernization Tom mentioned?

**Tom Halstead**: We'd tackle mobile after the core replatform reaches feature parity. Probably late February or early March start.

**Jess Nolan**: That timeline works for us. Our mobile team is planning a refresh cycle in Q2 anyway, so having the foundation ready by then would be ideal.

**Marcus Chen**: I'll produce a full API compatibility impact assessment by February 7. That will map every endpoint, identify any potential friction points, and document our mitigation strategy.

**Tom Halstead**: I'll also schedule a follow-up technical review with your engineering team in two weeks. We want to make sure everyone is aligned on the architecture.

**Jess Nolan**: I appreciate the thoroughness. The replatform is critical infrastructure for us. I'm cautiously optimistic about this approach.

**Marcus Chen**: We take the backward compatibility commitment very seriously. Any deviation would go through a formal review process with your team.

**Jess Nolan**: Good. I'll be watching the compatibility metrics closely. Let's make this happen.

**Tom Halstead**: We'll keep you updated weekly, Jess. Any concerns, don't hesitate to reach out directly.

**Jess Nolan**: One more thing. The board is presenting our modernization roadmap in March. I'll need some metrics on the replatform progress by then. Can you factor that into your reporting?

**Marcus Chen**: Absolutely. We'll include replatform progress metrics in the weekly status reports.""",
    },
    {
        "num": 5,
        "date": "2026-02-03",
        "title": "Monthly Business Review",
        "participants": ["Nadia Reeves", "Marcus Chen", "Rachel Lin", "Sam Okoro", "Tom Halstead"],
        "accounts": ["Northstar Retail", "Vanguard Group", "Meridian Internal"],
        "projects": ["Project Atlas", "Project Beacon", "Vanguard Replatform"],
        "signals": [
            {"type": "key_point", "content": "Atlas project running under budget at 140K spent of 180K allocation", "entities": ["project-atlas"]},
            {"type": "key_point", "content": "Beacon project has consumed 60 percent of Q1 budget and is behind on budget targets", "entities": ["project-beacon"]},
            {"type": "decision", "content": "Approved Vanguard scope expansion proposal at 85K additional budget", "entities": ["project-vanguard-replatform"]},
            {"type": "key_point", "content": "Nadia Reeves mentioned considering a sabbatical starting in March", "entities": ["person-nadia-reeves"]},
            {"type": "action_item", "content": "Sam to schedule Polaris Health introductory meeting for February 20", "entities": ["person-sam-okoro"], "owner": "Sam Okoro", "status": "open"},
            {"type": "insight", "content": "Atlas under-budget performance gives financial cushion but Beacon overrun is concerning", "entities": ["project-atlas", "project-beacon"]},
            {"type": "key_point", "content": "Total Q1 revenue tracking at 92% of target across all engagements", "entities": ["project-atlas", "project-vanguard-replatform"]},
            {"type": "insight", "content": "Nadia's potential sabbatical would create a leadership gap at a critical time with multiple projects in flight", "entities": ["person-nadia-reeves", "person-marcus-chen"]},
            {"type": "action_item", "content": "Rachel to prepare Beacon budget remediation options by February 10", "entities": ["person-rachel-lin"], "owner": "Rachel Lin", "status": "open"},
        ],
        "body": """# Monthly Business Review

## Meeting Overview
February monthly business review covering financial performance across all projects, Vanguard scope expansion approval, and early discussion of Nadia Reeves' potential sabbatical. Key budget concerns raised about Beacon project.

## Key Discussion Points
- Atlas project running under budget at $140K of $180K allocation spent
- Beacon project over-consuming budget at 60% of Q1 allocation already spent and is behind on budget targets
- Vanguard scope expansion formally approved with $85K additional budget
- Nadia Reeves mentioned considering sabbatical starting March
- Sam Okoro's healthcare lead progressing toward introductory meeting

## Decisions Made
1. Approved Vanguard scope expansion proposal at $85K additional budget
2. Beacon budget to be closely monitored through end of February

## Action Items
1. Sam Okoro to schedule Polaris Health introductory meeting for February 20
2. Rachel Lin to prepare Beacon budget remediation options
3. Marcus Chen to finalize Vanguard expansion SOW

## Important Insights
- Atlas under-budget performance gives financial cushion for the quarter
- Beacon budget overrun is concerning and may require intervention
- Nadia's potential sabbatical would create leadership transition needs

## Transcript

**Nadia Reeves**: Let's run through the numbers. Rachel, where are we?

**Rachel Lin**: Overall, Q1 revenue is tracking at 92% of target. Atlas is the star here, running under budget at $140K spent out of the $180K allocation. Strong execution by Marcus's team.

**Marcus Chen**: Phase 2 just launched and we're in good shape. The early Phase 1 completion helped with cost management.

**Rachel Lin**: Beacon is the concern. We've consumed 60% of the Q1 budget and we're only a third of the way through the quarter. The project is behind on budget targets.

**Nadia Reeves**: That's alarming. What's driving the overrun?

**Rachel Lin**: Combination of things. The AI tooling prototype needed more iteration than expected, and we had some unplanned infrastructure costs.

**Tom Halstead**: On the Vanguard side, here's the expansion proposal. The mobile modernization scope comes in at $85K, which Jess Nolan's team has indicated they'd approve.

**Nadia Reeves**: I've reviewed the proposal. I think the expansion is well-justified given the client relationship. Let's approve it.

**Marcus Chen**: That brings total Vanguard engagement to $205K. Healthy revenue.

**Sam Okoro**: Pipeline update. Polaris Health is ready for an introductory meeting. Their CTO, Kai Novak, is very engaged. I'm targeting February 20 for the intro.

**Nadia Reeves**: Excellent. Get that scheduled, Sam. Now, there's something I want to put on the table. I've been considering taking a sabbatical starting in March. Nothing decided yet, but I wanted to give you all early notice.

**Marcus Chen**: How long are you thinking?

**Nadia Reeves**: Three months, potentially. Through June. I know the timing isn't ideal with everything we have in flight.

**Rachel Lin**: We'd need a clear leadership plan. Marcus, you'd be the natural person to step in as interim.

**Marcus Chen**: I could manage it, but I'm already deep in Atlas and now Vanguard expansion oversight. It would be a heavy load.

**Nadia Reeves**: I understand. We have a few weeks to plan. Let's not panic. I just wanted transparency.

**Tom Halstead**: Appreciated, Nadia. We'll make it work either way.

**Rachel Lin**: I'll prepare Beacon budget remediation options by February 10. We may need to make some tough calls on that project.

**Nadia Reeves**: Good. Let's stay ahead of the budget situation. Sam, get Polaris on the calendar. Marcus, finalize the Vanguard SOW. Anything else?

**Sam Okoro**: Just that Polaris could be big. Kai mentioned they're looking at a multi-phase engagement. Assessment plus architecture. Could be $150K or more over two quarters.

**Nadia Reeves**: Music to my ears. All right, good meeting everyone. Let's execute.""",
    },
    {
        "num": 6,
        "date": "2026-02-07",
        "title": "Atlas Sprint Review: Phase 2 Launch",
        "participants": ["Marcus Chen", "Priya Kapoor", "Elena Voss"],
        "accounts": ["Northstar Retail"],
        "projects": ["Project Atlas"],
        "signals": [
            {"type": "key_point", "content": "Atlas project is ahead of schedule with phase 2 launched on time and data remediation 70 percent complete", "entities": ["project-atlas"]},
            {"type": "key_point", "content": "Northstar Retail approved Elena change management playbook", "entities": ["account-northstar-retail", "person-elena-voss"]},
            {"type": "insight", "content": "Atlas is in excellent shape and the team is executing well ahead of schedule", "entities": ["project-atlas"]},
            {"type": "key_point", "content": "Quality metrics trending positively with zero data integrity issues in Phase 2 launch", "entities": ["project-atlas", "account-northstar-retail"]},
            {"type": "action_item", "content": "Priya to complete remaining 30% of data remediation by February 21", "entities": ["person-priya-kapoor"], "owner": "Priya Kapoor", "status": "open"},
            {"type": "action_item", "content": "Elena to begin change management rollout with Northstar stakeholders", "entities": ["person-elena-voss"], "owner": "Elena Voss", "status": "open"},
            {"type": "insight", "content": "Change management playbook approval removes a key risk from the project timeline", "entities": ["project-atlas", "account-northstar-retail"]},
        ],
        "body": """# Atlas Sprint Review: Phase 2 Launch

## Meeting Overview
Atlas sprint review marking the successful launch of Phase 2 data migration. The project is running ahead of schedule with data remediation 70% complete and Northstar Retail approving the change management playbook.

## Key Discussion Points
- Phase 2 data migration launched on schedule
- Data remediation workstream 70% complete under Priya's leadership
- Northstar Retail stakeholders approved Elena's change management playbook
- Quality metrics trending positively across all migration workstreams

## Decisions Made
1. Phase 2 migration approach validated and proceeding as planned
2. Change management playbook approved for rollout to Northstar teams

## Action Items
1. Priya Kapoor to complete remaining 30% of data remediation by February 21
2. Elena Voss to begin change management rollout with Northstar stakeholders

## Important Insights
- Atlas is in excellent shape and the team is executing well ahead of schedule
- Priya's data remediation work has been critical to maintaining quality
- Change management approval removes a key risk from the project timeline

## Transcript

**Marcus Chen**: Let's do the Phase 2 launch review. Priya, how does the data remediation look?

**Priya Kapoor**: We're at 70% complete on the remediation workstream. The remaining 30% is mostly the legacy customer records with non-standard encoding. I'll have that wrapped up by February 21.

**Marcus Chen**: That's great progress. And the Phase 2 migration itself?

**Priya Kapoor**: Launched on time with zero data integrity issues. The pipeline configurations Derek documented were really helpful. Quality metrics are trending positively across all workstreams.

**Elena Voss**: Big news from the client side. Northstar approved the change management playbook. Their VP of Operations signed off yesterday.

**Marcus Chen**: Excellent. That was one of our biggest risks. With the playbook approved, we can start the rollout.

**Elena Voss**: I'm planning to begin stakeholder training sessions next week. Small groups first, then broader rollout as we get closer to the final migration cutover.

**Priya Kapoor**: One thing to note. The dbt models for the remediation pipeline are complex. If anything changes with the team structure, whoever takes over would need at least a week of ramp-up time on those models.

**Marcus Chen**: Good flag. Let's make sure everything is documented. We can't afford knowledge gaps on critical pipelines.

**Elena Voss**: Agreed. The Northstar team has been incredibly collaborative. They're excited about the new data platform capabilities.

**Marcus Chen**: Atlas is in excellent shape. We're ahead of schedule, quality is strong, and the client is happy. This is exactly where we want to be.

**Priya Kapoor**: I should mention the remaining remediation work involves some tricky ETL encoding transforms. If there are any surprises, it could take longer than planned, but I don't expect issues.

**Marcus Chen**: Understood. Let's keep the momentum going. Priya, target February 21 for remediation completion. Elena, get the change management rollout started. Great work, team.

**Elena Voss**: I'll send the first stakeholder communication today and begin scheduling the training sessions.

**Marcus Chen**: Perfect. Atlas is our flagship engagement right now and it shows.""",
    },
    {
        "num": 7,
        "date": "2026-02-14",
        "title": "Team Restructuring: Cross-Project Moves",
        "participants": ["Nadia Reeves", "Marcus Chen", "Priya Kapoor", "Elena Voss", "Derek Osman", "Tom Halstead"],
        "accounts": [],
        "projects": ["Project Atlas", "Project Beacon"],
        "signals": [
            {"type": "decision", "content": "Priya Kapoor reassigned from Atlas to Beacon effective immediately", "entities": ["person-priya-kapoor", "project-atlas", "project-beacon"]},
            {"type": "decision", "content": "Derek Osman reassigned from Beacon to Atlas effective immediately", "entities": ["person-derek-osman", "project-atlas", "project-beacon"]},
            {"type": "decision", "content": "Nadia Reeves confirmed sabbatical starting March 1 and Marcus Chen appointed Interim Managing Partner", "entities": ["person-nadia-reeves", "person-marcus-chen"]},
            {"type": "key_point", "content": "Tom Halstead expressed concern about workload on Vanguard after scope expansion", "entities": ["person-tom-halstead", "project-vanguard-replatform"]},
            {"type": "insight", "content": "The Priya-Derek swap introduces significant knowledge transfer risk on Atlas data remediation", "entities": ["person-priya-kapoor", "person-derek-osman", "project-atlas"]},
            {"type": "key_point", "content": "Priya has 4 days to transfer Atlas remediation knowledge to Derek before switch takes effect", "entities": ["person-priya-kapoor", "person-derek-osman"]},
            {"type": "insight", "content": "Nadia's sabbatical puts significant leadership burden on Marcus who is already managing Atlas technical delivery", "entities": ["person-nadia-reeves", "person-marcus-chen"]},
            {"type": "action_item", "content": "Priya to complete Atlas knowledge transfer to Derek by February 18", "entities": ["person-priya-kapoor", "person-derek-osman"], "owner": "Priya Kapoor", "status": "open"},
            {"type": "action_item", "content": "Marcus to prepare leadership transition plan by February 21", "entities": ["person-marcus-chen"], "owner": "Marcus Chen", "status": "open"},
        ],
        "body": """# Team Restructuring: Cross-Project Moves

## Meeting Overview
Major team restructuring session addressing cross-project resource moves, Nadia Reeves' confirmed sabbatical, and Marcus Chen's appointment as Interim Managing Partner. Significant personnel changes effective immediately.

## Key Discussion Points
- Priya Kapoor moving from Atlas to Beacon to accelerate AI tooling work
- Derek Osman moving from Beacon to Atlas to backfill Priya's role
- Nadia Reeves confirmed sabbatical starting March 1
- Marcus Chen appointed Interim Managing Partner during Nadia's absence
- Tom Halstead raised concerns about Vanguard workload post-expansion

## Decisions Made
1. Priya Kapoor reassigned from Atlas to Beacon effective immediately
2. Derek Osman reassigned from Beacon to Atlas effective immediately
3. Nadia Reeves confirmed sabbatical starting March 1; Marcus Chen appointed Interim Managing Partner
4. Tom Halstead to receive additional support for Vanguard scope

## Action Items
1. Marcus Chen to prepare leadership transition plan by February 21
2. Priya Kapoor to complete Atlas knowledge transfer to Derek by February 18
3. Tom Halstead to flag any Vanguard resource gaps by February 21

## Important Insights
- The Priya-Derek swap introduces knowledge transfer risk on Atlas
- Nadia's sabbatical puts significant leadership burden on Marcus
- Tom's workload concerns on Vanguard are an early warning signal

## Transcript

**Nadia Reeves**: Thank you all for coming. I have some significant announcements. First, I've decided to confirm my sabbatical. I'll be stepping away starting March 1.

**Marcus Chen**: We expected that might happen. How long?

**Nadia Reeves**: Through June, tentatively. Marcus, I'd like you to serve as Interim Managing Partner during my absence.

**Marcus Chen**: I'm honored. It's a lot on top of Atlas, but I'll make it work.

**Nadia Reeves**: I have full confidence in you. Now, the second major change. We need to address the Beacon budget situation and the AI tooling timeline. I'm proposing we move Priya from Atlas to Beacon effective immediately.

**Priya Kapoor**: I'm fine with that. Beacon needs senior technical leadership and the AI tooling work is exciting. But I'm concerned about Atlas. The data remediation is 70% done and the remaining work is the most complex part.

**Nadia Reeves**: Derek, you'd move from Beacon to Atlas to backfill Priya.

**Derek Osman**: I'm willing, but I need to be honest. I don't have Priya's depth on the dbt models and the data remediation pipeline. There's a real knowledge gap.

**Elena Voss**: Can we at least have a proper handoff period? Priya, how long would you need to transfer the critical knowledge?

**Priya Kapoor**: Ideally a week, but I could do a compressed handoff by February 18 if I really focus on it. Four days.

**Marcus Chen**: Four days is tight. Priya, please document everything you can about the remediation pipeline. Derek, shadow Priya starting tomorrow.

**Tom Halstead**: I want to flag something while we're discussing resources. With the Vanguard scope expansion, I'm stretched thin. I raised this before and now we're reshuffling people again. I need additional support.

**Nadia Reeves**: Tom, that's valid. We'll find a way to get you help on Vanguard. Marcus, include that in your transition planning.

**Marcus Chen**: Noted. I'll prepare a full leadership transition plan by February 21. Tom, flag any specific resource gaps by the same date.

**Elena Voss**: I want to go on record saying this swap introduces risk. Priya is the only person who deeply understands the Atlas remediation pipeline. If something goes wrong...

**Nadia Reeves**: The risk is noted, Elena. But Beacon needs Priya's expertise to have any chance of delivering value this quarter. It's a calculated trade-off.

**Priya Kapoor**: I'll make the knowledge transfer as thorough as possible. Derek is smart, he'll pick it up. It'll just take time.

**Derek Osman**: I'll give it everything I've got. And I'll reach out to Priya if I hit any walls.

**Marcus Chen**: Let's make this as smooth as possible. Priya, knowledge transfer to Derek by February 18. I'll have the transition plan ready by the 21st. We have a lot of balls in the air, let's keep them all up.

**Nadia Reeves**: Thank you all for your flexibility. I know this is a lot of change at once. I believe in this team.""",
    },
    {
        "num": 8,
        "date": "2026-02-20",
        "title": "Polaris Health: Introductory Meeting",
        "participants": ["Sam Okoro", "Marcus Chen", "Kai Novak"],
        "accounts": ["Polaris Health"],
        "projects": ["Polaris Onboarding"],
        "signals": [
            {"type": "key_point", "content": "Kai Novak CTO at Polaris Health presented data platform consolidation needs across 3 legacy systems", "entities": ["person-kai-novak", "account-polaris-health"]},
            {"type": "decision", "content": "Proposed phased engagement for Polaris with assessment at 60K followed by architecture at 90K", "entities": ["account-polaris-health", "project-polaris-onboarding"]},
            {"type": "key_point", "content": "HIPAA compliance is non-negotiable requirement for Polaris engagement", "entities": ["account-polaris-health"]},
            {"type": "action_item", "content": "Marcus to prepare Polaris Health proposal by February 28", "entities": ["person-marcus-chen"], "owner": "Marcus Chen", "status": "open"},
            {"type": "key_point", "content": "Polaris board wants architecture plan delivered by Q2 creating timeline pressure", "entities": ["account-polaris-health", "project-polaris-onboarding"]},
            {"type": "insight", "content": "Polaris Health represents a significant new revenue stream and entry into healthcare vertical", "entities": ["account-polaris-health", "project-polaris-onboarding"]},
            {"type": "action_item", "content": "Sam to send Meridian credentials and healthcare case studies to Kai Novak", "entities": ["person-sam-okoro", "person-kai-novak"], "owner": "Sam Okoro", "status": "open"},
            {"type": "insight", "content": "The phased approach reduces risk for both parties and builds trust incrementally", "entities": ["account-polaris-health", "project-polaris-onboarding"]},
            {"type": "key_point", "content": "Polaris legacy systems include Oracle DB, custom PostgreSQL, and a Hadoop cluster that need unification", "entities": ["account-polaris-health"]},
        ],
        "body": """# Polaris Health: Introductory Meeting

## Meeting Overview
Introductory meeting with Kai Novak, CTO at Polaris Health, to discuss data platform consolidation needs. Sam Okoro facilitated the introduction with Marcus Chen leading the technical discussion and proposal framework.

## Key Discussion Points
- Kai Novak presented Polaris Health's challenge: 3 legacy data systems needing consolidation
- HIPAA compliance identified as non-negotiable requirement for any engagement
- Phased engagement proposed: assessment ($60K) followed by architecture ($90K)
- Polaris looking for a partner who understands healthcare data regulations
- Timeline pressure: Polaris board wants architecture plan by Q2

## Decisions Made
1. Proposed phased engagement: assessment at $60K followed by architecture at $90K
2. HIPAA compliance framework to be included in all deliverables

## Action Items
1. Marcus Chen to prepare Polaris Health proposal by February 28
2. Sam Okoro to send Meridian credentials and healthcare case studies
3. Kai Novak to provide access to legacy system documentation

## Important Insights
- Polaris Health represents a significant new revenue stream in healthcare
- HIPAA compliance requirements will require specialized expertise
- The phased approach reduces risk for both Polaris and Meridian

## Transcript

**Sam Okoro**: Kai, thank you for making time today. I'd like to introduce Marcus Chen, our Director of Client Delivery. Marcus leads our data platform practice.

**Kai Novak**: Great to meet you, Marcus. I've been looking forward to this conversation. We have some serious data challenges at Polaris Health.

**Marcus Chen**: We're eager to hear about them, Kai. Sam mentioned you're dealing with legacy system consolidation?

**Kai Novak**: That's putting it mildly. We have three separate data systems that don't talk to each other. An Oracle database from our original platform, a custom PostgreSQL system from an acquisition three years ago, and a Hadoop cluster that our analytics team built independently.

**Sam Okoro**: That sounds like a scenario we've seen before. Our work with Northstar Retail involved similar data consolidation challenges.

**Kai Novak**: The critical thing for us is HIPAA compliance. Everything we do touches patient data. HIPAA compliance is non-negotiable for any work we do with an outside partner.

**Marcus Chen**: Absolutely understood. We would build HIPAA compliance into every deliverable from day one. No exceptions.

**Kai Novak**: Good. I've been burned before by vendors who treat compliance as an afterthought. What kind of engagement structure are you thinking?

**Marcus Chen**: I'd recommend a phased approach. Start with a 4-week assessment at $60K where we map all three systems, identify data overlaps and gaps, and design the target architecture. Then move to an architecture and implementation phase at $90K.

**Kai Novak**: I like the phased approach. It lets us validate fit before committing to the bigger engagement. My board wants an architecture plan by end of Q2, so timing matters.

**Sam Okoro**: We could start the assessment as early as mid-March if we get the contract sorted quickly.

**Kai Novak**: That would work for our timeline. I'll need to see a formal proposal, though. Our procurement process requires detailed scope documentation.

**Marcus Chen**: I'll have a comprehensive proposal ready by February 28. It will include scope, timeline, HIPAA compliance framework, and team qualifications.

**Kai Novak**: One more thing. I'll need your team to sign our data handling agreement. It's strict, but necessary given the nature of patient data.

**Sam Okoro**: Of course. We'll also send over our credentials and relevant healthcare case studies so your team can review our background.

**Kai Novak**: Perfect. I'll provide access to our legacy system documentation so you can understand what you're working with before the proposal.

**Marcus Chen**: That would be very helpful. We want the proposal to be specific to your actual systems, not generic.

**Kai Novak**: I appreciate that approach. Looking forward to the proposal, Marcus.

**Sam Okoro**: Thanks, Kai. We'll be in touch soon.""",
    },
    {
        "num": 9,
        "date": "2026-02-21",
        "title": "Atlas Emergency Standup: Data Quality Crisis",
        "participants": ["Marcus Chen", "Elena Voss", "Derek Osman"],
        "accounts": ["Northstar Retail"],
        "projects": ["Project Atlas"],
        "signals": [
            {"type": "key_point", "content": "Atlas project is now delayed due to phase 2 migration corrupting 8 percent of Northstar customer records", "entities": ["project-atlas", "account-northstar-retail"]},
            {"type": "decision", "content": "Atlas delivery timeline extended by 3 weeks to remediate data corruption", "entities": ["project-atlas"]},
            {"type": "key_point", "content": "Derek Osman struggling to debug the issue without Priya expertise", "entities": ["person-derek-osman", "person-priya-kapoor"]},
            {"type": "action_item", "content": "Elena to communicate revised timeline to Northstar by February 24", "entities": ["person-elena-voss"], "owner": "Elena Voss", "status": "open"},
            {"type": "insight", "content": "The Priya-to-Beacon move has directly impacted Atlas quality and timeline as predicted by Elena", "entities": ["person-priya-kapoor", "project-atlas", "project-beacon"]},
            {"type": "key_point", "content": "Data corruption affects customer address and contact fields in the legacy ETL pipeline", "entities": ["project-atlas", "account-northstar-retail"]},
            {"type": "action_item", "content": "Derek to document all corrupted records for remediation tracking by February 23", "entities": ["person-derek-osman"], "owner": "Derek Osman", "status": "open"},
            {"type": "insight", "content": "This crisis validates earlier warnings about resource allocation risks and single-point-of-failure on Priya", "entities": ["project-atlas", "person-priya-kapoor"]},
            {"type": "action_item", "content": "Marcus to evaluate whether to recall Priya from Beacon", "entities": ["person-marcus-chen", "person-priya-kapoor"], "owner": "Marcus Chen", "status": "open"},
        ],
        "body": """# Atlas Emergency Standup: Data Quality Crisis

## Meeting Overview
Emergency standup called after Phase 2 migration corrupted 8% of Northstar customer records. The Atlas project status has shifted from ahead of schedule to delayed, requiring a 3-week timeline extension for data remediation.

## Key Discussion Points
- Phase 2 migration introduced data corruption affecting 8% of customer records
- Derek Osman struggling to identify root cause without Priya's domain expertise
- Atlas delivery timeline needs 3-week extension for remediation
- Client communication strategy discussed for Northstar stakeholders
- Knowledge transfer gap from Priya's reassignment to Beacon now visible

## Decisions Made
1. Atlas delivery timeline extended by 3 weeks to remediate data corruption
2. Emergency remediation sprint to begin immediately
3. Elena Voss to lead client communication with Northstar

## Action Items
1. Elena Voss to communicate revised timeline to Northstar by February 24
2. Derek Osman to document all corrupted records for remediation tracking
3. Marcus Chen to evaluate whether to recall Priya from Beacon

## Important Insights
- The Priya-to-Beacon move has directly impacted Atlas quality and timeline
- Derek needs additional support to handle the data remediation complexity
- This crisis validates Tom's earlier warnings about resource allocation risks

## Transcript

**Marcus Chen**: I called this emergency standup because we have a serious problem on Atlas. Derek, walk us through what happened.

**Derek Osman**: Last night's Phase 2 migration batch corrupted customer records. I've been digging into it since 6 AM. It's affecting about 8% of Northstar's customer database. Mostly address and contact fields.

**Elena Voss**: Eight percent? That's thousands of records. How did this get past the validation checks?

**Derek Osman**: The corruption is in the legacy ETL pipeline. There's an encoding transformation that Priya built for the remediation workstream. I thought I understood it, but something is wrong with how it handles the edge cases in the legacy format.

**Marcus Chen**: Can you pinpoint the root cause?

**Derek Osman**: Honestly, no. Not yet. I'm struggling to debug the issue without Priya's expertise. The dbt models are complex and the encoding logic has a lot of nuance that wasn't fully captured in the documentation.

**Elena Voss**: This is exactly what I was worried about when we did the swap. Priya flagged those encoding transforms as tricky.

**Marcus Chen**: I know. Let's focus on next steps. How long to fix this?

**Derek Osman**: If I can figure out the root cause, maybe a week to remediate. But finding the root cause could take days. Honestly, Marcus, I think we need Priya.

**Marcus Chen**: I hear you. I need to evaluate whether to pull Priya back from Beacon. That has its own consequences.

**Elena Voss**: What about the client? Northstar needs to know about this. We can't hide 8% data corruption.

**Marcus Chen**: Agreed. Elena, I need you to communicate a revised timeline to Northstar by February 24. We're extending the delivery by 3 weeks to remediate.

**Elena Voss**: Three weeks? That's significant. They were expecting delivery on the original timeline.

**Marcus Chen**: I know, but we can't deliver corrupted data. We need to be transparent. The change management playbook you built should help frame the communication.

**Derek Osman**: I feel terrible about this. I should have caught it earlier.

**Marcus Chen**: This isn't about blame, Derek. The knowledge transfer window was too short. That's a leadership decision, not a Derek decision.

**Elena Voss**: Marcus is right. Derek, document every corrupted record you find. We need a complete audit trail for the remediation.

**Derek Osman**: I'll have the full corrupted records inventory by the 23rd.

**Marcus Chen**: Good. I need to make a decision about Priya by end of day tomorrow. The Beacon work is important but Atlas is a revenue-generating client engagement. This has to take priority.

**Elena Voss**: Whatever you decide, we need to move fast. Every day we wait is another day of risk for Northstar.

**Marcus Chen**: Understood. Let's regroup tomorrow morning. Derek, keep digging. Elena, start drafting the client communication.""",
    },
    {
        "num": 10,
        "date": "2026-02-28",
        "title": "Vanguard Check-in: Client Escalation",
        "participants": ["Tom Halstead", "Marcus Chen", "Jess Nolan"],
        "accounts": ["Vanguard Group"],
        "projects": ["Vanguard Replatform"],
        "signals": [
            {"type": "key_point", "content": "Jess Nolan reports API backward compatibility promise has failed with several endpoints having breaking changes", "entities": ["person-jess-nolan", "project-vanguard-replatform"]},
            {"type": "decision", "content": "Tom Halstead departure from Meridian Partners effective February 28", "entities": ["person-tom-halstead"]},
            {"type": "key_point", "content": "Vanguard replatform at risk of client escalation due to broken API compatibility promise", "entities": ["project-vanguard-replatform", "account-vanguard-group"]},
            {"type": "action_item", "content": "Marcus to assign new Vanguard engagement lead by March 5", "entities": ["person-marcus-chen"], "owner": "Marcus Chen", "status": "open"},
            {"type": "insight", "content": "The broken API compatibility promise is a serious client trust issue that contradicts the January commitment", "entities": ["project-vanguard-replatform", "account-vanguard-group"]},
            {"type": "key_point", "content": "Three critical API endpoints have breaking changes affecting Vanguard's payment processing consumers", "entities": ["project-vanguard-replatform"]},
            {"type": "insight", "content": "Tom's departure at this critical moment compounds the Vanguard risk and leaves no institutional knowledge on the engagement", "entities": ["person-tom-halstead", "project-vanguard-replatform"]},
            {"type": "action_item", "content": "Tom to complete knowledge transfer documentation before end of day", "entities": ["person-tom-halstead"], "owner": "Tom Halstead", "status": "open"},
            {"type": "action_item", "content": "Marcus to prepare API compatibility recovery plan for Jess Nolan within one week", "entities": ["person-marcus-chen"], "owner": "Marcus Chen", "status": "open"},
        ],
        "body": """# Vanguard Check-in: Client Escalation

## Meeting Overview
Critical Vanguard check-in revealing that the API backward compatibility promise has been broken, with Jess Nolan reporting breaking changes in several endpoints. Compounded by Tom Halstead's departure effective today.

## Key Discussion Points
- Jess Nolan identified multiple API endpoints with breaking changes
- API backward compatibility promise from Meeting 4 has failed
- Tom Halstead's last day at Meridian Partners is today (February 28)
- Vanguard replatform at risk of formal client escalation
- Need for new engagement lead assignment urgently

## Decisions Made
1. Tom Halstead departure from Meridian Partners effective February 28
2. Immediate API compatibility audit to be conducted
3. Recovery plan to be presented to Jess Nolan within one week

## Action Items
1. Marcus Chen to assign new Vanguard engagement lead by March 5
2. Marcus to prepare API compatibility recovery plan for Jess Nolan
3. Tom Halstead to complete knowledge transfer documentation before end of day

## Important Insights
- The broken API compatibility promise is a serious client trust issue
- Tom's departure at this critical moment compounds the Vanguard risk
- Marcus is now managing multiple crises simultaneously (Atlas + Vanguard + transition)

## Transcript

**Tom Halstead**: Before we get into the status update, I need to share some news. Today is my last day at Meridian Partners.

**Marcus Chen**: Tom, this is sudden. We're in the middle of the Vanguard expansion.

**Tom Halstead**: I know the timing is difficult. I've accepted a position elsewhere. I'll do everything I can to make the transition smooth today.

**Jess Nolan**: I'm sorry to hear that, Tom. But I have to be direct. We have bigger problems. My engineering team ran compatibility tests last week and found breaking changes in three critical API endpoints.

**Marcus Chen**: Breaking changes? We committed to maintaining backward compatibility with no breaking changes. Which endpoints?

**Jess Nolan**: The payment processing endpoints. Three of them have schema changes that break our downstream consumers. We have 47 external API consumers, Marcus. This is exactly what I told you couldn't happen.

**Tom Halstead**: I'm not aware of those changes being deployed. This may have happened during the scope expansion work.

**Jess Nolan**: Regardless of how it happened, the promise of API backward compatibility has failed. I need to know how you're going to fix this, and I need to know quickly. My board presentation is in two weeks.

**Marcus Chen**: Jess, I take full responsibility. We will fix this. I'll personally lead a compatibility audit starting Monday and have a recovery plan to you within one week.

**Jess Nolan**: A week is the maximum I can wait. If those endpoints aren't fixed or if I see any more breaking changes, I'm escalating this to our procurement team. That means a formal contract review.

**Tom Halstead**: I'll document everything I know about the Vanguard architecture and current state before end of day today. All the deployment history, the compatibility test results, everything.

**Marcus Chen**: Thank you, Tom. I also need to assign a new engagement lead. I'll have someone in place by March 5.

**Jess Nolan**: I need someone who understands our architecture and takes the compatibility commitment seriously. Not someone learning on the job.

**Marcus Chen**: Understood. I'm thinking Elena Voss. She's been on our most successful engagement this quarter and she's rigorous about client commitments.

**Tom Halstead**: Elena would be a good choice. She has the right temperament for the relationship recovery work this needs.

**Jess Nolan**: I'm reserving judgment until I see results. Marcus, one week. Fix the breaking changes and show me a plan for ensuring it doesn't happen again.

**Marcus Chen**: You'll have it, Jess. I give you my word.

**Tom Halstead**: I'm sorry I'm leaving at this moment. Marcus, the knowledge transfer docs will be in the shared drive by 6 PM.

**Marcus Chen**: Appreciated, Tom. And thank you for your service to Meridian. We'll miss you.""",
    },
    {
        "num": 11,
        "date": "2026-03-03",
        "title": "Leadership Transition & Vanguard Recovery",
        "participants": ["Marcus Chen", "Elena Voss", "Jess Nolan"],
        "accounts": ["Vanguard Group"],
        "projects": ["Vanguard Replatform"],
        "signals": [
            {"type": "decision", "content": "Elena Voss appointed Vanguard engagement lead replacing Tom Halstead", "entities": ["person-elena-voss", "project-vanguard-replatform", "person-tom-halstead"]},
            {"type": "decision", "content": "Weekly written status reports to Vanguard required every Monday", "entities": ["project-vanguard-replatform", "account-vanguard-group"]},
            {"type": "key_point", "content": "Jess Nolan cautiously accepted recovery plan for API compatibility", "entities": ["person-jess-nolan"]},
            {"type": "key_point", "content": "API compatibility fixes prioritized over all new feature development on Vanguard", "entities": ["project-vanguard-replatform"]},
            {"type": "insight", "content": "Elena's appointment brings fresh energy but she needs to build client trust quickly in a damaged relationship", "entities": ["person-elena-voss", "account-vanguard-group"]},
            {"type": "insight", "content": "Weekly reporting cadence demonstrates commitment to transparency and helps rebuild client confidence", "entities": ["project-vanguard-replatform", "account-vanguard-group"]},
            {"type": "action_item", "content": "Elena to deliver first weekly status report by March 9", "entities": ["person-elena-voss"], "owner": "Elena Voss", "status": "open"},
            {"type": "action_item", "content": "Elena to schedule dedicated technical review with Vanguard engineering team", "entities": ["person-elena-voss"], "owner": "Elena Voss", "status": "open"},
        ],
        "body": """# Leadership Transition & Vanguard Recovery

## Meeting Overview
First meeting of Marcus Chen's tenure as Interim Managing Partner, focused on Vanguard recovery. Elena Voss appointed as new Vanguard engagement lead, and a recovery plan presented to Jess Nolan.

## Key Discussion Points
- Elena Voss takes over as Vanguard engagement lead from Tom Halstead
- API compatibility recovery plan presented to Jess Nolan
- Weekly written status reports to be provided to Vanguard every Monday
- Jess Nolan cautiously positive about recovery approach
- Marcus managing leadership transition while handling multiple project crises

## Decisions Made
1. Elena Voss appointed Vanguard engagement lead replacing Tom Halstead
2. Weekly written status reports to Vanguard required every Monday
3. API compatibility fixes prioritized over new feature development

## Action Items
1. Elena Voss to deliver first weekly status report by March 9
2. Marcus Chen to review API compatibility fix progress weekly
3. Elena to schedule dedicated technical review with Vanguard engineering

## Important Insights
- Elena's appointment brings fresh energy but she needs to build client trust quickly
- The weekly reporting cadence shows commitment to transparency
- Jess's cautious acceptance gives Meridian a narrow window to rebuild confidence

## Transcript

**Marcus Chen**: Jess, thank you for giving us this meeting. I want to introduce Elena Voss, who I'm appointing as your new engagement lead.

**Elena Voss**: Jess, I've reviewed the full situation. The API compatibility failures are unacceptable and I want you to know that fixing them is my top priority.

**Jess Nolan**: I appreciate the directness, Elena. I've seen too many handoffs where the new person needs weeks to get up to speed. Where do you stand?

**Elena Voss**: I spent the weekend reviewing Tom's documentation and the compatibility test results. I've identified the three failing endpoints and I have a fix plan for each one.

**Marcus Chen**: Elena is our strongest client relationship manager. She led the Northstar engagement through a challenging period and maintained strong client satisfaction throughout.

**Jess Nolan**: Tell me about the fix plan.

**Elena Voss**: Endpoint one, the payment schema change, we can revert without impacting the new functionality. Two days of work. Endpoint two, the transaction history format change, requires a compatibility layer. About a week. Endpoint three, the authentication token format, is the most complex. Two weeks but I've already started the analysis.

**Jess Nolan**: And how do I know this won't happen again?

**Elena Voss**: Two things. First, I'm implementing an automated compatibility test suite that runs on every deployment. If any endpoint breaks backward compatibility, the deployment is blocked. Second, I'll provide weekly written status reports every Monday with compatibility metrics.

**Marcus Chen**: We're also prioritizing API compatibility fixes over all new feature development on Vanguard. Nothing else ships until compatibility is restored.

**Jess Nolan**: That's what I needed to hear. I'll accept the recovery plan, cautiously. Elena, I'm holding you to those weekly reports.

**Elena Voss**: You'll have the first one by March 9. And I'd like to schedule a dedicated technical review with your engineering team so we can align on the compatibility test criteria.

**Jess Nolan**: I'll set that up. Marcus, one question. With Nadia on sabbatical and you running the firm, who's watching the technical details on Vanguard?

**Marcus Chen**: Elena has my full support and I'll review compatibility progress weekly. This engagement is too important to leave anything to chance.

**Jess Nolan**: Okay. Let's see how the next two weeks go. Elena, you have my direct number. Don't hesitate to use it.

**Elena Voss**: Thank you, Jess. We're going to get this right.""",
    },
    {
        "num": 12,
        "date": "2026-03-07",
        "title": "Beacon Budget Review & Pause Decision",
        "participants": ["Marcus Chen", "Priya Kapoor", "Rachel Lin"],
        "accounts": ["Meridian Internal"],
        "projects": ["Project Beacon", "Project Atlas", "Polaris Onboarding"],
        "signals": [
            {"type": "decision", "content": "Beacon project paused effective March 14 to reallocate budget", "entities": ["project-beacon"]},
            {"type": "key_point", "content": "Rachel Lin reports Beacon deliverables on track despite budget overrun", "entities": ["project-beacon", "person-rachel-lin"]},
            {"type": "decision", "content": "Priya Kapoor temporarily reassigned to Atlas to assist with data remediation", "entities": ["person-priya-kapoor", "project-atlas"]},
            {"type": "decision", "content": "30K reallocated from Beacon to Atlas recovery and 15K to Polaris assessment", "entities": ["project-beacon", "project-atlas", "project-polaris-onboarding"]},
            {"type": "insight", "content": "Beacon pause is pragmatic but risks losing development momentum on the AI tooling initiative", "entities": ["project-beacon"]},
            {"type": "key_point", "content": "Beacon has consumed 78% of its Q1 budget with 3 weeks remaining in the quarter", "entities": ["project-beacon"]},
            {"type": "insight", "content": "Priya's return to Atlas should accelerate the data corruption fix given her deep pipeline knowledge", "entities": ["person-priya-kapoor", "project-atlas"]},
            {"type": "action_item", "content": "Priya to begin Atlas data remediation support by March 10", "entities": ["person-priya-kapoor"], "owner": "Priya Kapoor", "status": "open"},
            {"type": "action_item", "content": "Rachel to prepare Beacon preservation plan for Q2 restart evaluation", "entities": ["person-rachel-lin"], "owner": "Rachel Lin", "status": "open"},
        ],
        "body": """# Beacon Budget Review & Pause Decision

## Meeting Overview
Budget review for the Beacon project resulting in a pause decision to reallocate resources. Despite Rachel Lin reporting deliverables on track, budget pressures and competing priorities led to pausing Beacon and reassigning Priya back to Atlas.

## Key Discussion Points
- Beacon has consumed more budget than planned but deliverables are on track
- Rachel Lin presented financial analysis showing budget overrun but delivery progress
- Atlas needs Priya's expertise to resolve the data corruption crisis
- Polaris assessment phase needs initial funding allocation
- Budget reallocation: $30K from Beacon to Atlas, $15K to Polaris

## Decisions Made
1. Beacon project paused effective March 14 to reallocate budget
2. Priya Kapoor temporarily reassigned to Atlas to assist with data remediation
3. $30K reallocated from Beacon to Atlas recovery and $15K to Polaris assessment

## Action Items
1. Priya Kapoor to begin Atlas data remediation support by March 10
2. Rachel Lin to prepare Beacon preservation plan for Q2 restart evaluation
3. Marcus to update all stakeholders on Beacon pause and resource changes

## Important Insights
- Beacon pause is pragmatic given competing demands on Atlas and Polaris
- Priya's return to Atlas should accelerate the data corruption fix
- Budget reallocation reflects the firm's shifting priorities toward revenue-generating work

## Transcript

**Marcus Chen**: Rachel, let's look at the Beacon numbers. Where are we exactly?

**Rachel Lin**: Beacon has consumed 78% of its Q1 budget with three weeks remaining in the quarter. We've spent about $35K of the $45K allocation. The burn rate accelerated when Priya joined the team.

**Priya Kapoor**: The AI tooling work needed more compute resources than we initially scoped. But the deliverables are on track. The document analysis prototype is working.

**Rachel Lin**: That's true. Despite the budget overrun, the actual deliverables are progressing well. The question is whether we can afford to keep going.

**Marcus Chen**: Here's my dilemma. Atlas has a data corruption crisis that needs Priya's expertise. Polaris starts in 10 days and needs funding. Something has to give.

**Priya Kapoor**: I understand. If you need me back on Atlas, I can go. Derek called me yesterday about the encoding issue and I think I know what went wrong.

**Marcus Chen**: That's exactly what I needed to hear. Here's what I'm proposing. We pause Beacon effective March 14. We reallocate $30K to Atlas recovery and $15K to the Polaris assessment phase.

**Rachel Lin**: That's a tough call on Beacon. We'll lose momentum on the AI tooling work. But financially, it makes sense. Atlas and Polaris are revenue-generating client work.

**Priya Kapoor**: Can we at least preserve the Beacon codebase and documentation so we can restart in Q2? I don't want to lose the work we've done.

**Marcus Chen**: Absolutely. Rachel, prepare a Beacon preservation plan. We want to be able to restart quickly if budget allows in Q2.

**Rachel Lin**: I'll have that ready by end of next week. I'll include the current state of deliverables, the codebase inventory, and the estimated cost to restart.

**Priya Kapoor**: I'll start on Atlas remediation by March 10. I want to review Derek's notes first so I can hit the ground running.

**Marcus Chen**: Good. The Northstar team is counting on us. This data corruption issue has been hanging over us for too long.

**Rachel Lin**: One more thing. With the budget reallocation, our Q1 financial picture actually improves. Atlas and Polaris both have better margins than Beacon.

**Marcus Chen**: That helps with the quarterly report. All right, let's move forward with the pause. Priya, you're back on Atlas. Rachel, Beacon preservation plan. I'll communicate the changes to the broader team.

**Priya Kapoor**: Copy. I'll coordinate with Derek on the Atlas remediation schedule.

**Rachel Lin**: Understood. I'll also prepare Q2 budget proposals that include a Beacon restart option.""",
    },
    {
        "num": 13,
        "date": "2026-03-10",
        "title": "Polaris Health: Proposal Review",
        "participants": ["Marcus Chen", "Sam Okoro", "Kai Novak"],
        "accounts": ["Polaris Health"],
        "projects": ["Polaris Onboarding"],
        "signals": [
            {"type": "decision", "content": "Polaris Health assessment phase approved starting March 17 at 60K", "entities": ["account-polaris-health", "project-polaris-onboarding"]},
            {"type": "decision", "content": "Priya Kapoor and Derek Osman assigned to Polaris once Atlas stabilizes", "entities": ["person-priya-kapoor", "person-derek-osman", "project-polaris-onboarding"]},
            {"type": "key_point", "content": "Kai Novak approved HIPAA compliance framework for data handling", "entities": ["person-kai-novak", "account-polaris-health"]},
            {"type": "insight", "content": "Polaris represents significant growth opportunity in the healthcare vertical for Meridian Partners", "entities": ["account-polaris-health", "project-polaris-onboarding"]},
            {"type": "key_point", "content": "Architecture phase at 90K to follow pending assessment findings", "entities": ["project-polaris-onboarding"]},
            {"type": "action_item", "content": "Marcus to onboard Polaris in project management systems by March 14", "entities": ["person-marcus-chen"], "owner": "Marcus Chen", "status": "open"},
            {"type": "action_item", "content": "Sam to finalize Polaris contract paperwork this week", "entities": ["person-sam-okoro"], "owner": "Sam Okoro", "status": "open"},
            {"type": "insight", "content": "Resource dependency on Atlas stabilization introduces scheduling risk for Polaris staffing", "entities": ["project-atlas", "project-polaris-onboarding"]},
            {"type": "action_item", "content": "Kai Novak to provide legacy system access credentials by March 14", "entities": ["person-kai-novak"], "owner": "Kai Novak", "status": "open"},
        ],
        "body": """# Polaris Health: Proposal Review

## Meeting Overview
Proposal review with Kai Novak for the Polaris Health engagement. The assessment phase was formally approved at $60K starting March 17, with team assignments planned contingent on Atlas stabilization.

## Key Discussion Points
- Polaris Health assessment phase approved at $60K starting March 17
- HIPAA compliance framework reviewed and approved by Kai Novak
- Priya Kapoor and Derek Osman designated for Polaris once Atlas stabilizes
- Architecture phase ($90K) to follow pending assessment findings
- Data platform consolidation scope covering all 3 legacy systems

## Decisions Made
1. Polaris Health assessment phase approved starting March 17 at $60K
2. Priya Kapoor and Derek Osman assigned to Polaris once Atlas stabilizes
3. HIPAA compliance framework accepted as baseline for all work

## Action Items
1. Marcus Chen to onboard Polaris in project management systems by March 14
2. Sam Okoro to finalize Polaris contract paperwork
3. Kai Novak to provide legacy system access credentials by March 14

## Important Insights
- Polaris represents significant growth opportunity in healthcare vertical
- Resource dependency on Atlas stabilization introduces scheduling risk
- HIPAA compliance approval streamlines the engagement start

## Transcript

**Sam Okoro**: Kai, we've put together a comprehensive proposal based on our initial conversation. Marcus, would you like to walk through it?

**Marcus Chen**: Thanks Sam. Kai, the proposal covers a 4-week assessment phase at $60K. We'll map all three of your legacy systems, identify data quality issues, and deliver a target architecture recommendation.

**Kai Novak**: I've reviewed the proposal document in detail. The scope looks right. My main question is about the team. Who's doing the work?

**Marcus Chen**: We're planning to assign Priya Kapoor and Derek Osman. Priya is our top data platform specialist with deep experience in complex migrations. Derek has been gaining strong experience on a similar data project this quarter.

**Kai Novak**: When can they start?

**Marcus Chen**: We're targeting March 17 for kickoff. We have another client engagement that's wrapping up remediation work, and both Priya and Derek will be available once that stabilizes.

**Sam Okoro**: In the meantime, Marcus and I can handle the initial setup and access provisioning.

**Kai Novak**: That works. Let's talk about the HIPAA compliance framework. I've reviewed your proposed approach and I'm satisfied with it. Our legal team signed off as well.

**Marcus Chen**: Excellent. HIPAA compliance is built into every phase of our methodology, from data handling to documentation to access controls.

**Kai Novak**: Good. The architecture phase at $90K. Can you give me more detail on what that includes?

**Marcus Chen**: The architecture phase builds on the assessment findings. We'll design the target platform, create migration playbooks for each legacy system, build the ETL pipelines, and deliver a fully documented architecture with runbooks.

**Kai Novak**: My board needs to see the architecture plan by end of Q2. Can you commit to that timeline?

**Sam Okoro**: If we start the assessment on March 17 and it runs four weeks, we'd begin architecture in mid-April. That gives us about 10 weeks to complete the architecture phase, which is comfortable.

**Kai Novak**: Good. I'll have the legacy system access credentials ready by March 14. You'll need access to the Oracle instance, the PostgreSQL databases, and the Hadoop cluster.

**Marcus Chen**: I'll onboard Polaris in our project management systems by the 14th as well. We want everything ready for a smooth kickoff.

**Sam Okoro**: I'll have the contract paperwork finalized this week. Our legal team has already reviewed the data handling agreement you sent over.

**Kai Novak**: Perfect. I'm excited to get started. The data platform consolidation is long overdue and I'm confident Meridian is the right partner for this.

**Marcus Chen**: We're looking forward to it, Kai. This is going to be a great engagement.""",
    },
    {
        "num": 14,
        "date": "2026-03-17",
        "title": "Atlas Recovery & Status Update",
        "participants": ["Marcus Chen", "Priya Kapoor", "Elena Voss", "Derek Osman"],
        "accounts": ["Northstar Retail"],
        "projects": ["Project Atlas"],
        "signals": [
            {"type": "key_point", "content": "Atlas project is back on track after Priya identified and resolved root cause of data corruption", "entities": ["project-atlas", "person-priya-kapoor"]},
            {"type": "key_point", "content": "Northstar Retail confirmed satisfaction with revised timeline and recovery effort", "entities": ["account-northstar-retail"]},
            {"type": "key_point", "content": "Derek Osman demonstrated strong growth handling Atlas crisis independently", "entities": ["person-derek-osman"]},
            {"type": "insight", "content": "Priya's return was the key to resolving the data corruption crisis confirming the single-point-of-failure risk", "entities": ["person-priya-kapoor", "project-atlas"]},
            {"type": "key_point", "content": "Root cause identified as legacy encoding mismatch in the customer record ETL pipeline", "entities": ["project-atlas"]},
            {"type": "insight", "content": "The transparent communication with Northstar actually strengthened the client relationship through the crisis", "entities": ["account-northstar-retail", "project-atlas"]},
            {"type": "action_item", "content": "Priya to document root cause analysis for knowledge base", "entities": ["person-priya-kapoor"], "owner": "Priya Kapoor", "status": "open"},
            {"type": "action_item", "content": "Derek to present lessons learned at next team meeting", "entities": ["person-derek-osman"], "owner": "Derek Osman", "status": "open"},
            {"type": "key_point", "content": "All corrupted records successfully remediated and validated with 100% accuracy", "entities": ["project-atlas", "account-northstar-retail"]},
        ],
        "body": """# Atlas Recovery & Status Update

## Meeting Overview
Atlas recovery status update showing the project is back on track after Priya identified and resolved the root cause of the data corruption. Northstar Retail expressed satisfaction with the recovery effort and Derek Osman's growth was noted.

## Key Discussion Points
- Priya identified root cause: legacy encoding mismatch in customer record ETL pipeline
- All corrupted records successfully remediated and validated
- Northstar Retail confirmed satisfaction with revised timeline and transparency
- Derek Osman handled much of the crisis independently before Priya's return
- Project back on track for delivery within the extended timeline

## Decisions Made
1. Atlas remediation phase complete, returning to standard delivery cadence
2. Derek Osman to receive expanded responsibilities on Atlas going forward

## Action Items
1. Priya Kapoor to document root cause analysis for knowledge base
2. Derek Osman to present lessons learned at next team meeting
3. Elena Voss to send final recovery report to Northstar stakeholders

## Important Insights
- Priya's return was the key to resolving the data corruption crisis
- Derek's growth through the crisis demonstrates junior talent development
- The transparent communication with Northstar actually strengthened the relationship

## Transcript

**Marcus Chen**: All right team, give me the good news I've been waiting for.

**Priya Kapoor**: Atlas is back on track. I found the root cause within 48 hours of coming back. It was a legacy encoding mismatch in the customer record ETL pipeline. Specifically, the legacy Oracle export uses Latin-1 encoding for a subset of customer address fields, but our dbt model was treating everything as UTF-8.

**Derek Osman**: In my defense, that encoding path wasn't documented. But I should have tested more thoroughly before running the full batch.

**Marcus Chen**: Derek, you did solid work under pressure. The fact that you had 92% of the records mapped correctly before Priya even came back shows real growth.

**Elena Voss**: I have good news from the client side too. Northstar confirmed they're satisfied with the revised timeline and the recovery effort. The VP of Operations specifically praised our transparency.

**Priya Kapoor**: All corrupted records have been remediated and validated. We're at 100% accuracy across the full dataset now. I triple-checked the encoding transforms.

**Marcus Chen**: How long did the full remediation take once you found the root cause?

**Priya Kapoor**: Three days for the fix, two days for validation. The dbt model change was small but the validation needed to be thorough. We re-ran every quality check.

**Derek Osman**: I learned a huge amount through this crisis. The pipeline architecture makes a lot more sense to me now than it did during the four-day handoff.

**Elena Voss**: I've been drafting the final recovery report for Northstar stakeholders. I want to frame it as both a transparent post-mortem and a demonstration of our resilience.

**Marcus Chen**: Good framing. The fact that Northstar is satisfied and the relationship actually seems stronger tells me our crisis response was solid, even if the crisis itself was avoidable.

**Priya Kapoor**: I'll document the full root cause analysis for our knowledge base. Other teams need to know about this encoding issue. It could affect any legacy Oracle migration.

**Derek Osman**: I'd like to present the lessons learned at the next team meeting, if that's okay. I think there are good takeaways about knowledge transfer and testing.

**Marcus Chen**: Absolutely, Derek. I want the whole team to hear it. Now, let's talk about next steps. Atlas is returning to standard delivery cadence. Derek, you're getting expanded responsibilities on the project going forward.

**Elena Voss**: I'll have the final recovery report sent to Northstar by end of week.

**Priya Kapoor**: And I can start ramping up on Polaris next week. The Atlas remediation is fully closed.

**Marcus Chen**: Perfect. This is exactly the turnaround we needed. Great work, everyone. Especially you, Priya. Your expertise was the difference.""",
    },
    {
        "num": 15,
        "date": "2026-03-21",
        "title": "Q1 Retrospective & Q2 Planning",
        "participants": ["Marcus Chen", "Priya Kapoor", "Elena Voss", "Derek Osman", "Rachel Lin", "Sam Okoro"],
        "accounts": ["Northstar Retail", "Vanguard Group", "Polaris Health", "Meridian Internal"],
        "projects": ["Project Atlas", "Vanguard Replatform", "Polaris Onboarding", "Project Beacon"],
        "signals": [
            {"type": "key_point", "content": "Q1 financial performance shows 92 percent budget utilization across all active projects", "entities": ["project-atlas", "project-vanguard-replatform", "project-polaris-onboarding"]},
            {"type": "decision", "content": "Q2 priorities confirmed as stabilize Vanguard and complete Atlas and ramp Polaris and evaluate Beacon restart", "entities": ["project-atlas", "project-vanguard-replatform", "project-polaris-onboarding", "project-beacon"]},
            {"type": "key_point", "content": "Marcus Chen successfully leading as Interim Managing Partner during Nadia sabbatical", "entities": ["person-marcus-chen", "person-nadia-reeves"]},
            {"type": "key_point", "content": "Nadia Reeves return date remains uncertain with original plan for June", "entities": ["person-nadia-reeves"]},
            {"type": "insight", "content": "Q1 was turbulent but the team navigated multiple crises effectively demonstrating organizational resilience", "entities": ["project-atlas", "project-vanguard-replatform"]},
            {"type": "key_point", "content": "Vanguard API compatibility fixes 80% complete under Elena's leadership", "entities": ["project-vanguard-replatform", "person-elena-voss"]},
            {"type": "insight", "content": "Marcus's leadership during the transition exceeded expectations and may influence permanent leadership decisions", "entities": ["person-marcus-chen"]},
            {"type": "action_item", "content": "Marcus to draft Q2 strategic plan by March 28", "entities": ["person-marcus-chen"], "owner": "Marcus Chen", "status": "open"},
            {"type": "action_item", "content": "Rachel to prepare Q2 budget proposals", "entities": ["person-rachel-lin"], "owner": "Rachel Lin", "status": "open"},
            {"type": "action_item", "content": "Sam to develop Q2 sales pipeline forecast", "entities": ["person-sam-okoro"], "owner": "Sam Okoro", "status": "open"},
        ],
        "body": """# Q1 Retrospective & Q2 Planning

## Meeting Overview
Comprehensive Q1 retrospective and Q2 planning session led by Marcus Chen as Interim Managing Partner. Reviewed performance across all projects, financial metrics, and established Q2 strategic priorities.

## Key Discussion Points
- Q1 budget utilization at 92% across all active projects
- Atlas recovered from crisis and back on track for delivery
- Vanguard recovery ongoing with Elena building client trust
- Polaris assessment phase launched on schedule
- Beacon paused but deliverables preserved for potential Q2 restart
- Nadia Reeves return date uncertain, original plan was June
- Marcus Chen performing strongly as Interim Managing Partner

## Decisions Made
1. Q2 priorities: stabilize Vanguard, complete Atlas, ramp Polaris, evaluate Beacon restart
2. Monthly retrospectives to continue through Q2
3. Resource allocation review scheduled for April

## Action Items
1. Marcus Chen to draft Q2 strategic plan by March 28
2. Rachel Lin to prepare Q2 budget proposals
3. Sam Okoro to develop Q2 sales pipeline forecast
4. Elena Voss to present Vanguard recovery progress at April review

## Important Insights
- Q1 was turbulent but the team navigated multiple crises effectively
- Marcus's leadership during the transition exceeded expectations
- The firm is in a stronger position entering Q2 despite Q1 challenges

## Transcript

**Marcus Chen**: Thank you all for being here. This is our Q1 retrospective and Q2 planning session. Let's start with the numbers. Rachel?

**Rachel Lin**: Q1 budget utilization came in at 92% across all active projects. Atlas consumed $175K of the $210K total allocation including the emergency reallocation. Vanguard is at $185K of the expanded $205K budget. Polaris assessment started with $15K committed so far.

**Marcus Chen**: And Beacon?

**Rachel Lin**: Beacon consumed $35K before the pause. The remaining $10K was reallocated. Total Q1 revenue across all client engagements exceeded targets by 3%.

**Elena Voss**: On the Vanguard front, the API compatibility fixes are 80% complete. The payment endpoint fix is deployed and tested. The transaction history compatibility layer ships next week. The authentication token fix is on track for end of March.

**Sam Okoro**: Jess Nolan's tone has shifted noticeably. She's still cautious but the weekly reports are building trust. Elena's done an incredible job with the relationship recovery.

**Priya Kapoor**: Atlas is fully stabilized. All corrupted records remediated. We're back on the original delivery cadence and Derek is handling the day-to-day operations confidently.

**Derek Osman**: I want to acknowledge the learning curve was steep, but I'm in a much stronger position now. The crisis taught me more about data pipeline architecture than months of normal work would have.

**Marcus Chen**: Let's talk about what went wrong this quarter so we learn from it.

**Elena Voss**: The biggest lesson is single points of failure. When Priya moved to Beacon, Atlas lost its only data remediation expert. We need cross-training protocols.

**Priya Kapoor**: Agreed. And the knowledge transfer window was too short. Four days isn't enough for complex pipeline work. We should establish a minimum two-week handoff for critical roles.

**Sam Okoro**: On the business side, Polaris is a great win. Kai Novak is engaged and the healthcare vertical has strong potential. I'm developing a Q2 pipeline forecast with two more healthcare leads.

**Marcus Chen**: Good. Now, Q2 priorities. I'm proposing we focus on four things. One, stabilize Vanguard and complete the API compatibility fixes. Two, complete the Atlas delivery and close out the Northstar engagement successfully. Three, ramp the Polaris assessment and position for the architecture phase. Four, evaluate a Beacon restart depending on Q2 budget.

**Rachel Lin**: That's a solid priority stack. I'll prepare Q2 budget proposals that support those priorities.

**Marcus Chen**: One more topic. Nadia's return. I spoke with her last week. The return date remains uncertain. The original plan was June but she's evaluating.

**Elena Voss**: How does that affect our planning?

**Marcus Chen**: We plan as if I'm continuing as Interim Managing Partner through Q2. If Nadia returns earlier, great. But we can't plan around uncertainty.

**Derek Osman**: For what it's worth, Marcus, you've done an amazing job this quarter. Managing the Atlas crisis, the Vanguard escalation, the Beacon pause, and the Polaris onboarding. All while stepping into the leadership role.

**Sam Okoro**: Seconded. Marcus's leadership during the transition exceeded expectations. The team is in a stronger position entering Q2 despite all the Q1 challenges.

**Marcus Chen**: I appreciate that. It's been a team effort. All right, action items. I'll draft the Q2 strategic plan by March 28. Rachel, Q2 budget proposals. Sam, pipeline forecast. Elena, prepare to present Vanguard recovery progress at the April review.

**Rachel Lin**: Got it. I'll also include a Beacon restart cost analysis in the Q2 proposals.

**Marcus Chen**: Perfect. Q1 was turbulent but we made it through. Let's make Q2 the quarter where we deliver on everything we've set up. Thank you, everyone.""",
    },
]

# ---------------------------------------------------------------------------
# Meeting-to-project mapping for project frontmatter
# ---------------------------------------------------------------------------


def get_project_meetings(project_id: str) -> list[str]:
    """Get meeting UUIDs where a project was discussed."""
    name_map = {
        "project-atlas": "Project Atlas",
        "project-beacon": "Project Beacon",
        "project-vanguard-replatform": "Vanguard Replatform",
        "project-polaris-onboarding": "Polaris Onboarding",
    }
    proj_name = name_map.get(project_id, "")
    result = []
    for m in MEETINGS:
        if proj_name in m["projects"]:
            result.append(meeting_uuid(m["num"]))
    return result


# ---------------------------------------------------------------------------
# Collaborators for people
# ---------------------------------------------------------------------------


def get_collaborators(person_name: str) -> list[str]:
    """Derive collaborators from shared meeting participation."""
    collabs = set()
    for m in MEETINGS:
        if person_name in m["participants"]:
            for p in m["participants"]:
                if p != person_name:
                    collabs.add(f"person-{slug(p)}")
    return sorted(collabs)


def get_last_seen(person_name: str) -> str:
    """Get the date of the last meeting a person participated in."""
    last = None
    for m in MEETINGS:
        if person_name in m["participants"]:
            last = m["date"]
    return last or "2026-01-06"


def get_person_accounts(person: dict) -> list[str]:
    """Get accounts a person manages based on their projects."""
    acct_map = {
        "project-atlas": "account-northstar-retail",
        "project-vanguard-replatform": "account-vanguard-group",
        "project-polaris-onboarding": "account-polaris-health",
        "project-beacon": "account-meridian-internal",
    }
    accounts = set()
    for proj in person["projects"]:
        if proj in acct_map and person["title"] in ("Managing Partner", "Director Client Delivery / Interim Managing Partner", "VP Sales", "Engagement Lead, Vanguard"):
            accounts.add(acct_map[proj])
    return sorted(accounts)


# ---------------------------------------------------------------------------
# Person body generation
# ---------------------------------------------------------------------------

PERSON_BIOS = {
    "Nadia Reeves": {
        "role_desc": "Managing Partner at Meridian Partners, responsible for overall firm strategy, client relationships, and team leadership. Currently on sabbatical effective March 1, 2026.",
        "involvement": [
            ("Project Atlas", "Executive sponsor overseeing Northstar Retail data strategy engagement"),
            ("Project Beacon", "Champion of internal AI tooling initiative"),
            ("Vanguard Replatform", "Senior relationship holder with Vanguard Group"),
        ],
        "activities": "Led Q1 kickoff, confirmed sabbatical during February restructuring meeting. Appointed Marcus Chen as Interim Managing Partner. Return date tentatively set for June 2026.",
        "reports_to": "Board of Partners",
    },
    "Marcus Chen": {
        "role_desc": "Director of Client Delivery, appointed Interim Managing Partner effective March 1, 2026. Leads Atlas squad and oversees technical delivery across all client engagements.",
        "involvement": [
            ("Project Atlas", "Technical lead and squad leader for Northstar Retail data strategy"),
            ("Vanguard Replatform", "Technical oversight and API compatibility management"),
            ("Polaris Onboarding", "Proposal author and engagement architect for new healthcare client"),
        ],
        "activities": "Led Atlas through Phase 1 and Phase 2, managed data quality crisis response, produced Polaris Health proposal, took over as Interim Managing Partner. Managing multiple concurrent crises while maintaining delivery quality.",
        "reports_to": "Nadia Reeves",
    },
    "Priya Kapoor": {
        "role_desc": "Senior Consultant with deep expertise in data quality, remediation, and analytics. Key resource whose reassignments have had significant project impact.",
        "involvement": [
            ("Project Atlas", "Led data remediation workstream, resolved critical data corruption crisis"),
            ("Project Beacon", "Temporarily assigned Feb 14 - Mar 7 during team restructuring"),
            ("Polaris Onboarding", "Designated for Polaris assessment once Atlas stabilizes"),
        ],
        "activities": "Completed Atlas Phase 1 data remediation, reassigned to Beacon Feb 14, returned to Atlas Mar 7 to resolve data corruption crisis. Identified root cause as legacy encoding mismatch in ETL pipeline.",
        "reports_to": "Marcus Chen",
    },
    "Tom Halstead": {
        "role_desc": "Former Engagement Lead for the Vanguard Replatform project. Departed Meridian Partners on February 28, 2026.",
        "involvement": [
            ("Vanguard Replatform", "Engagement lead from project inception through departure"),
        ],
        "activities": "Managed Vanguard client relationship, facilitated scope expansion discussions, expressed concerns about workload. Departed firm on February 28 during critical API compatibility issues.",
        "reports_to": "Marcus Chen",
    },
    "Elena Voss": {
        "role_desc": "Senior Consultant who stepped up as Vanguard engagement lead after Tom Halstead's departure. Strong change management and client communication skills.",
        "involvement": [
            ("Project Atlas", "Drafted and implemented change management playbook for Northstar Retail"),
            ("Vanguard Replatform", "Appointed engagement lead March 3, leading recovery effort"),
        ],
        "activities": "Created change management playbook approved by Northstar, communicated Atlas timeline revision to client, appointed Vanguard engagement lead replacing Tom Halstead, presenting recovery plan to Jess Nolan.",
        "reports_to": "Marcus Chen",
    },
    "Derek Osman": {
        "role_desc": "Junior Consultant showing strong growth potential. Moved between projects during Q1 restructuring and demonstrated resilience during Atlas crisis.",
        "involvement": [
            ("Project Beacon", "Initial assignment Jan 13 - Feb 14"),
            ("Project Atlas", "Reassigned Feb 14, handled data crisis, demonstrated strong growth"),
            ("Polaris Onboarding", "Designated for Polaris assessment once Atlas stabilizes"),
        ],
        "activities": "Started on Beacon, reassigned to Atlas during restructuring, struggled initially with data corruption debugging without Priya's expertise but demonstrated strong independent growth through the crisis.",
        "reports_to": "Marcus Chen",
    },
    "Rachel Lin": {
        "role_desc": "Finance & Operations Manager responsible for budget tracking, financial reporting, and operational efficiency across all Meridian Partners engagements.",
        "involvement": [
            ("Project Beacon", "Budget oversight and financial analysis"),
        ],
        "activities": "Managed Q1 budget tracking, reported Beacon budget overrun concerns, prepared budget reallocation analysis for Beacon pause decision. Reported 92% Q1 budget utilization.",
        "reports_to": "Nadia Reeves",
    },
    "Sam Okoro": {
        "role_desc": "VP Sales responsible for business development, client acquisition, and pipeline management. Successfully brought in Polaris Health as new client.",
        "involvement": [
            ("Polaris Onboarding", "Sourced and facilitated Polaris Health relationship"),
        ],
        "activities": "Identified Polaris Health warm lead, scheduled introductory meeting, facilitated proposal review with Kai Novak, managing sales pipeline and Q2 forecast.",
        "reports_to": "Nadia Reeves",
    },
    "Jess Nolan": {
        "role_desc": "VP Product at Vanguard Group, external client stakeholder. Key technical decision-maker for the Vanguard Replatform engagement.",
        "involvement": [
            ("Vanguard Replatform", "Client-side technical lead and product owner"),
        ],
        "activities": "First engaged with Meridian team Jan 27, validated technical architecture, later reported API backward compatibility failures, cautiously accepted recovery plan under Elena Voss's leadership.",
        "reports_to": "Vanguard Group Leadership",
    },
    "Kai Novak": {
        "role_desc": "CTO at Polaris Health, external client stakeholder. Driving data platform consolidation initiative across 3 legacy systems.",
        "involvement": [
            ("Polaris Onboarding", "Client-side technical lead and engagement sponsor"),
        ],
        "activities": "Presented data platform consolidation needs Feb 20, approved HIPAA compliance framework, approved assessment phase at $60K starting March 17.",
        "reports_to": "Polaris Health Leadership",
    },
}


def generate_person_body(person: dict) -> str:
    bio = PERSON_BIOS[person["name"]]
    lines = [f"# {person['name']}", "", "## Current Role & Responsibilities", bio["role_desc"], "", "## Project Involvement"]
    for proj_name, desc in bio["involvement"]:
        lines.append(f"- **{proj_name}**: {desc}")
    lines.extend(["", "## Recent Activities", bio["activities"], "", "## Key Relationships"])
    collabs = get_collaborators(person["name"])
    collab_names = [c.replace("person-", "").replace("-", " ").title() for c in collabs[:5]]
    lines.append(f"- Collaborates with: {', '.join(collab_names)}")
    lines.append(f"- Reports to: {bio['reports_to']}")
    if person["teams"]:
        team_names = [t.replace("team-", "").replace("-", " ").title() for t in person["teams"]]
        lines.append(f"- Member of: {', '.join(team_names)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------


def make_signal(sig: dict, meeting: dict) -> dict:
    """Create a full signal object from abbreviated spec."""
    entity_objects = []
    for eid in sig.get("entities", []):
        if eid.startswith("person-"):
            name = eid.replace("person-", "").replace("-", " ").title()
            entity_objects.append({"id": eid, "type": "person", "name": name})
        elif eid.startswith("project-"):
            name = eid.replace("project-", "").replace("-", " ").title()
            entity_objects.append({"id": eid, "type": "project", "name": name})
        elif eid.startswith("account-"):
            name = eid.replace("account-", "").replace("-", " ").title()
            entity_objects.append({"id": eid, "type": "account", "name": name})

    # Convert owner from plain string to EntityRef dict
    owner_value = sig.get("owner")
    if owner_value is not None:
        owner_value = entity_ref(owner_value)

    mid = meeting_uuid(meeting["num"])
    return {
        "id": signal_uuid(sig["content"]),
        "type": sig["type"],
        "content": sig["content"],
        "source_meeting_id": mid,
        "source_meeting_title": meeting["title"],
        "source_timestamp": f"{meeting['date']}T10:00:00Z",
        "participants": meeting["participants"],
        "entities": entity_objects,
        "confidence": 0.9,
        "status": sig.get("status"),
        "owner": owner_value,
        "due_date": sig.get("due_date"),
        "position": 0,
        "created_at": f"{meeting['date']}T10:30:00Z",
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# File generation
# ---------------------------------------------------------------------------


def generate(output_dir: str) -> None:
    base = Path(output_dir)

    dirs = [
        base / "meetings",
        base / "entities" / "person",
        base / "entities" / "project",
        base / "entities" / "team",
        base / "accounts",
        base / "signals",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    counts = {"meetings": 0, "people": 0, "projects": 0, "teams": 0, "accounts": 0, "signals": 0}

    # --- Meetings ---
    for m in MEETINGS:
        mid = meeting_uuid(m["num"])
        bid = bot_uuid(m["num"])
        fm = {
            "meeting_id": mid,
            "bot_id": bid,
            "updated_at": f"{m['date']}T10:00:00Z",
            "update_count": 1,
            "is_finalized": True,
            "status": "completed",
            "title": m["title"],
            "duration": 3600,
            "entities_mentioned": {
                "participants": [f"person-{slug(p)}" for p in m["participants"]],
                "account": m["accounts"],
                "project": m["projects"],
                "person": m["participants"],
            },
            "participants": m["participants"],
            "speakers": m["participants"],
        }
        content = render_frontmatter(fm) + "\n\n" + m["body"].strip() + "\n"
        path = base / "meetings" / f"meeting-{mid}.md"
        path.write_text(content)
        counts["meetings"] += 1

    # --- People ---
    for person in PEOPLE:
        pid = f"person-{slug(person['name'])}"
        fm = {
            "name": person["name"],
            "title": person["title"],
            "department": person["department"],
            "company": person["company"],
            "contact": person["contact"],
            "projects": person["projects"],
            "teams": person["teams"],
            "last_seen": f"{get_last_seen(person['name'])}T10:00:00Z",
            "canonical_name": person["name"],
            "collaborates_with": get_collaborators(person["name"]),
            "entity_type": "person",
            "id": pid,
            "manages_accounts": get_person_accounts(person),
            "member_of_team": person["teams"],
            "works_on_projects": person["projects"],
            "valid_from": person["valid_from"],
            "valid_to": person["valid_to"],
        }
        body = generate_person_body(person)
        content = render_frontmatter(fm) + "\n\n" + body + "\n"
        path = base / "entities" / "person" / f"{pid}.md"
        path.write_text(content)
        counts["people"] += 1

    # --- Projects ---
    for proj in PROJECTS:
        fm = {
            "name": proj["name"],
            "type": "project",
            "id": proj["id"],
            "status": proj["status"],
            "meetings": get_project_meetings(proj["id"]),
            "focus_areas": proj["focus_areas"],
            "key_technologies": proj["key_technologies"],
            "belongs_to_account": proj["belongs_to_account"],
            "valid_from": proj["valid_from"],
        }

        # Project body
        acct = proj["belongs_to_account"][0].replace("account-", "").replace("-", " ").title() if proj["belongs_to_account"] else "Internal"
        body_lines = [
            f"# {proj['name']}",
            "",
            "## Overview",
            f"{proj['name']} is a {'client' if acct != 'Meridian Internal' else 'internal'} engagement for {acct}.",
            f"Focus areas include {', '.join(proj['focus_areas'])}.",
            "",
            "## Status",
            f"Current status: **{proj['status']}**",
            "",
            "## Key Technologies",
            ", ".join(proj["key_technologies"]),
            "",
            "## Meeting History",
        ]
        for mid_str in get_project_meetings(proj["id"]):
            # find meeting title
            num = int(mid_str.split("-")[1])
            mtg = next(m for m in MEETINGS if m["num"] == num)
            body_lines.append(f"- [{mtg['title']}](../meetings/meeting-{mid_str}.md) ({mtg['date']})")

        content = render_frontmatter(fm) + "\n\n" + "\n".join(body_lines) + "\n"
        path = base / "entities" / "project" / f"{proj['id']}.md"
        path.write_text(content)
        counts["projects"] += 1

    # --- Teams ---
    for team in TEAMS:
        fm = {
            "name": team["name"],
            "type": "team",
            "id": team["id"],
            "members": team["members"],
            "lead": team["lead"],
            "projects": team["projects"],
        }
        lead_name = team["lead"].replace("person-", "").replace("-", " ").title()
        member_names = [m.replace("person-", "").replace("-", " ").title() for m in team["members"]]
        body_lines = [
            f"# {team['name']}",
            "",
            "## Overview",
            f"The {team['name']} is led by {lead_name}.",
            "",
            "## Members",
        ]
        for mn in member_names:
            body_lines.append(f"- {mn}")
        if team["projects"]:
            body_lines.extend(["", "## Projects"])
            for p in team["projects"]:
                pname = p.replace("project-", "").replace("-", " ").title()
                body_lines.append(f"- {pname}")

        content = render_frontmatter(fm) + "\n\n" + "\n".join(body_lines) + "\n"
        path = base / "entities" / "team" / f"{team['id']}.md"
        path.write_text(content)
        counts["teams"] += 1

    # --- Accounts ---
    for acct in ACCOUNTS:
        fm = {
            "name": acct["name"],
            "type": "account",
            "id": f"account-{acct['slug']}",
            "industry": acct["industry"],
            "projects": acct["projects"],
            "primary_contact": acct["primary_contact"],
        }
        body_lines = [
            f"# {acct['name']}",
            "",
            "## Overview",
            acct["description"],
            "",
            "## Industry",
            acct["industry"],
            "",
            "## Projects",
        ]
        for p in acct["projects"]:
            pname = p.replace("project-", "").replace("-", " ").title()
            body_lines.append(f"- {pname}")
        body_lines.extend([
            "",
            "## Primary Contact",
            acct["primary_contact"].replace("person-", "").replace("-", " ").title(),
        ])

        content = render_frontmatter(fm) + "\n\n" + "\n".join(body_lines) + "\n"
        path = base / "accounts" / f"{acct['slug']}.md"
        path.write_text(content)
        counts["accounts"] += 1

    # --- Signals ---
    for m in MEETINGS:
        mid = meeting_uuid(m["num"])
        bid = bot_uuid(m["num"])
        signals = [make_signal(s, m) for s in m["signals"]]

        # Set position index
        for i, s in enumerate(signals):
            s["position"] = i

        signal_file = {
            "meeting_id": mid,
            "bot_id": bid,
            "meeting_title": m["title"],
            "extracted_at": f"{m['date']}T10:30:00Z",
            "signal_count": len(signals),
            "signals": signals,
        }
        path = base / "signals" / f"meeting-{bid}.json"
        path.write_text(json.dumps(signal_file, indent=2) + "\n")
        counts["signals"] += 1

    # --- Summary ---
    print(f"Generated synthetic knowledge base in: {base}")
    print(f"  Meetings:  {counts['meetings']}")
    print(f"  People:    {counts['people']}")
    print(f"  Projects:  {counts['projects']}")
    print(f"  Teams:     {counts['teams']}")
    print(f"  Accounts:  {counts['accounts']}")
    print(f"  Signals:   {counts['signals']}")
    total = sum(counts.values())
    print(f"  Total:     {total} files")


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic knowledge base for Meridian Partners")
    parser.add_argument("output_dir", nargs="?", default="/tmp/meridian-partners-kb/", help="Output directory (default: /tmp/meridian-partners-kb/)")
    args = parser.parse_args()
    generate(args.output_dir)


if __name__ == "__main__":
    main()
