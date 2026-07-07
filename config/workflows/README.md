# Workflow Configuration

Workflows customize transcript-processing behavior for different meeting types. Each workflow defines which processors run and how the processing agent is configured.

## Overview

A workflow configuration controls:
- **Processors**: Which background processors analyze the transcript (decision detection, action items, key points)
- **Agent**: Whether the processing agent runs and which skills it uses

## Directory Structure

```text
config/workflows/
  README.md              # This file
  <workflow-id>.yaml     # One file per workflow
```

No workflow files ship with the community edition — until you add one, every meeting is
processed with the built-in default (`DEFAULT_WORKFLOW` in `app/services/workflow_loader.py`:
all three processors enabled, agent enabled). The example YAML below is illustrative.

## Schema Reference

### Top-Level Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `workflow_id` | string | Yes | Unique identifier (matches filename without extension) |
| `name` | string | Yes | Human-readable name shown in UI |
| `description` | string | No | Purpose and use case description |
| `processors` | object | No | Processor configuration |
| `agent` | object | No | Agent configuration |

### Processors Section

Controls which background processors analyze transcript chunks.

```yaml
processors:
  enabled:                    # List of enabled processors
    - decision_detector       # Detects decisions made in meeting
    - action_item_detector    # Extracts action items with owners
    - key_point_extractor     # Identifies key discussion points
  config:                     # Per-processor configuration
    decision_detector:
      confidence_threshold: 0.7    # Minimum confidence (0.0-1.0)
      system_prompt_path: null     # Custom prompt path (optional)
    action_item_detector:
      confidence_threshold: 0.7
    key_point_extractor:
      confidence_threshold: 0.6
```

**Available Processors**:
- `decision_detector`: Identifies decisions with owner, rationale, and timeline
- `action_item_detector`: Extracts tasks with assignee, deadline, and priority
- `key_point_extractor`: Captures important discussion points and themes

**Configuration Options**:
- `confidence_threshold`: Minimum confidence score (0.0-1.0) to report a signal. Lower values capture more signals but may include false positives.
- `system_prompt_path`: Path to custom system prompt file (optional)

### Agent Section

Controls the agent that processes signals.

```yaml
agent:
  enabled: true              # Whether agent runs (true/false)
  model: "claude-sonnet-4-5" # Model to use for agent
  system_prompt_path: null   # Custom system prompt (optional)
  skills:                    # Skill names passed to the agent (see note below)
    - decision-tracking      # illustrative — provide your own skills
    - action-item-tracking
```

**Skills**: the `skills` list is passed through to the processing agent's configuration
(`AgentConfig` in `app/models/workflow.py`). Entries should name skills available to the
agent runtime (e.g. under `.claude/skills/`). No meeting-processing skills ship with the
community edition — the names in the examples (`decision-tracking`, `action-item-tracking`)
are illustrative placeholders for skills you provide.

**When to Disable Agent**:
- Simple meetings that only need key point capture
- Meetings where processing overhead should be minimized
- Discovery calls focused on listening rather than tracking decisions

## Example Workflows

### client-discovery.yaml

Lightweight capture focused on client needs and pain points:

```yaml
workflow_id: "client-discovery"
name: "Client Discovery Call"
description: "Lightweight capture focused on client needs and pain points"

processors:
  enabled:
    - key_point_extractor     # Only capture key points
  config:
    key_point_extractor:
      confidence_threshold: 0.6

agent:
  enabled: false              # No agent for simpler meetings
```

**Use case**: Sales discovery calls where the focus is capturing client pain points without the overhead of decision/action tracking.

### internal-meeting.yaml

Full capture with action items and decisions:

```yaml
workflow_id: "internal-meeting"
name: "Internal Team Meeting"
description: "Full capture with action items and decisions"

processors:
  enabled:
    - decision_detector
    - action_item_detector
    - key_point_extractor

agent:
  enabled: true
  skills:
    - decision-tracking
    - action-item-tracking
```

**Use case**: Internal team meetings where tracking decisions, action items, and ensuring follow-through is critical.

## Creating a New Workflow

1. Create a new YAML file in `config/workflows/`:
   ```bash
   touch config/workflows/my-workflow.yaml
   ```

2. Define required fields:
   ```yaml
   workflow_id: "my-workflow"
   name: "My Custom Workflow"
   description: "Purpose of this workflow"
   ```

3. Configure processors based on meeting needs:
   - Discovery/listening meetings: `key_point_extractor` only
   - Decision-heavy meetings: All processors
   - Quick syncs: Minimal processors with lower thresholds

4. Set agent configuration:
   - Complex meetings: Enable with relevant skills
   - Simple meetings: Disable for lower latency

## Validation

Workflows are validated on load by the Pydantic models in `app/models/workflow.py`
(loader: `app/services/workflow_loader.py`):

- Required fields must be present (`workflow_id`, `name`)
- `confidence_threshold` must be between 0.0 and 1.0

Behavior on problems:
- **Missing file / unknown workflow id** → the built-in default workflow is used and a
  warning is logged.
- **Malformed YAML or failed validation** → a `ValueError` is raised (no silent fallback).

Skill names and prompt paths are **not** checked for existence at load time — a typo there
surfaces at processing time, not at load.

## Best Practices

1. **Match workflow to meeting type**: Don't over-process simple meetings
2. **Start with defaults**: Only customize what you need to change
3. **Test before deploying**: Load workflow in development to verify it works
4. **Document purpose**: Use description field to explain when to use
