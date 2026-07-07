# Temporal Knowledge Graph — E2E Demo Report

**Date**: 2026-03-24 14:17
**Dataset**: Meridian Partners Q1 2026 (synthetic)
**Results**: 42/42 passed (100%)

## Results by Tool Category

| Category | Passed | Failed | Avg Latency |
|----------|--------|--------|-------------|
| `active_relationships_at_time` | 5/5 | 0 | 36.1s |
| `entity_at_time` | 6/6 | 0 | 30.4s |
| `find_contradictions` | 6/6 | 0 | 45.0s |
| `get_entity_provenance` | 5/5 | 0 | 39.6s |
| `graph_as_of` | 5/5 | 0 | 35.2s |
| `temporal_blast_radius` | 5/5 | 0 | 41.3s |
| `what_changed` | 5/5 | 0 | 38.1s |
| `what_changed_between` | 5/5 | 0 | 31.6s |

## Detailed Results

### [PASS] EAT-1 — entity_at_time

**Question**: What did we know about Priya Kapoor on January 15, 2026?

**Tools used**: active_relationships_at_time, entity_at_time, get_entity_by_name

**Temporal tools hit**: entity_at_time, get_entity_by_name

**Duration**: 32.3s | **Answer length**: 1614 chars

**Answer preview**:
> I'll help you find what was known about Priya Kapoor on January 15, 2026. Let me first look up this person in the knowledge graph.Now let me retrieve what was known about Priya Kapoor on January 15, 2026.Let me also check what relationships were active for Priya Kapoor on that date.Based on the historical data, here's what was known about **Priya Kapoor** on **January 15, 2026**:
> 
> ## Basic Information
> - **Title**: Senior Consultant
> - **Company**: Meridian Partners
> - **Department**: Consulting
> - 

---

### [PASS] EAT-2 — entity_at_time

**Question**: What was the state of Project Atlas on February 10, 2026?

**Tools used**: active_relationships_at_time, entity_at_time, get_entity_by_name, query_signals

**Temporal tools hit**: entity_at_time, get_entity_by_name

**Duration**: 39.8s | **Answer length**: 1698 chars

**Answer preview**:
> I'll help you find the historical state of Project Atlas on February 10, 2026. Let me first look up this project to get its entity ID.Now let me retrieve the state of Project Atlas as it was on February 10, 2026:Let me also check what relationships and connections were active for Project Atlas on that date:Let me also check what signals (decisions, action items, key points) were active around that date to get a fuller picture:## State of Project Atlas on February 10, 2026
> 
> Based on the temporal 

---

### [PASS] EAT-3 — entity_at_time

**Question**: What did we know about Kai Novak on February 15, 2026? Did he exist in our system at that time?

**Tools used**: entity_at_time, get_entity_by_name

**Temporal tools hit**: entity_at_time, get_entity_by_name

**Duration**: 21.4s | **Answer length**: 790 chars

**Answer preview**:
> I'll help you find out what we knew about Kai Novak on February 15, 2026. Let me start by looking up this entity.I found Kai Novak in the system. Now let me check what his state was on February 15, 2026.**No, Kai Novak did not exist in our system on February 15, 2026.**
> 
> Based on the temporal query results, Kai Novak was first added to the system on **February 20, 2026** - five days after the date you're asking about. 
> 
> His current profile shows:
> - **Title:** CTO, Polaris Health
> - **Company:** P

---

### [PASS] EAT-4 — entity_at_time

**Question**: What was Tom Halstead's role on March 1, 2026? Was he still with the firm?

**Tools used**: entity_at_time, get_entity_by_name

**Temporal tools hit**: entity_at_time, get_entity_by_name

**Duration**: 27.5s | **Answer length**: 940 chars

**Answer preview**:
> I'll help you find Tom Halstead's role on March 1, 2026. Let me start by looking up his entity information.Now let me check his state specifically on March 1, 2026:Let me check his status just before that date to understand what happened:Based on the temporal data in the knowledge graph:
> 
> **No, Tom Halstead was not with the firm on March 1, 2026.**
> 
> His entity record shows that his validity period ended on **February 28, 2026**. On February 28th, he held the role of **Engagement Lead, Vanguard**

---

### [PASS] EAT-5 — entity_at_time

**Question**: What did we know about the Vanguard Replatform project on January 30, 2026?

**Tools used**: active_relationships_at_time, entity_at_time, get_entity_by_name, get_entity_signals

**Temporal tools hit**: entity_at_time, get_entity_by_name

**Duration**: 40.8s | **Answer length**: 1935 chars

**Answer preview**:
> I'll help you find what was known about the Vanguard Replatform project on January 30, 2026. Let me start by looking up this project.Now let me retrieve what was known about this project on January 30, 2026:Let me also check what relationships were active at that time:Let me also check what signals (decisions, action items, insights) were known about this project around that time:Based on the temporal query, here's what was known about the **Vanguard Replatform project on January 30, 2026**:
> 
> ##

---

### [PASS] EAT-6 — entity_at_time

**Question**: What was Marcus Chen's role on February 1, 2026 versus March 10, 2026?

**Tools used**: entity_at_time, get_entity_by_name

**Temporal tools hit**: entity_at_time, get_entity_by_name

**Duration**: 20.7s | **Answer length**: 788 chars

**Answer preview**:
> I'll help you find out what Marcus Chen's role was at those two different times. Let me start by looking up Marcus Chen's entity information.Now let me check Marcus Chen's role on February 1, 2026:Now let me check his role on March 10, 2026:Based on the temporal data from the knowledge graph, **Marcus Chen's role was the same on both dates**:
> 
> - **February 1, 2026**: Director Client Delivery / Interim Managing Partner
> - **March 10, 2026**: Director Client Delivery / Interim Managing Partner
> 
> Mar

---

### [PASS] WC-1 — what_changed

**Question**: What changed about Priya Kapoor since February 1, 2026?

**Tools used**: get_entity_by_name, list_meeting_documents, query_signals, what_changed

**Temporal tools hit**: get_entity_by_name, what_changed

**Duration**: 38.5s | **Answer length**: 2030 chars

**Answer preview**:
> I'll help you find what changed about Priya Kapoor since February 1, 2026. Let me start by looking up their entity ID.Now let me check what has changed about Priya Kapoor since February 1, 2026.The temporal query shows no changes to Priya Kapoor's core entity profile since February 1, 2026. However, let me check for recent activity like meetings and signals (action items, decisions) to give you a more complete picture.Perfect! Now I have a clear picture. Let me also check the recent meeting acti

---

### [PASS] WC-2 — what_changed

**Question**: What changed about Project Atlas since January 6, 2026?

**Tools used**: get_entity_by_name, get_entity_signals, what_changed

**Temporal tools hit**: get_entity_by_name, what_changed

**Duration**: 37.3s | **Answer length**: 2067 chars

**Answer preview**:
> I'll help you find what changed about Project Atlas since January 6, 2026. Let me first look up the entity and then check the changes.Now let me check what changed about Project Atlas since January 6, 2026:The temporal tracking shows **no structural changes** to Project Atlas since January 6, 2026 - meaning its properties and relationships in the knowledge graph have remained the same.
> 
> However, let me check if there have been any recent meetings, decisions, or action items related to Project At

---

### [PASS] WC-3 — what_changed

**Question**: What changed about Marcus Chen since February 15, 2026?

**Tools used**: get_entity_by_name, get_entity_context_summary, query_signals, read_document, what_changed

**Temporal tools hit**: get_entity_by_name, what_changed

**Duration**: 51.3s | **Answer length**: 1927 chars

**Answer preview**:
> I'll help you find what changed about Marcus Chen since February 15, 2026. Let me start by looking up Marcus Chen's entity ID.Now let me check what changed about Marcus Chen since February 15, 2026.The temporal tracking shows no structural changes to Marcus Chen's entity since February 15, 2026. Let me check for recent activity and signals related to him to see what's been happening.Let me check for recent signals (decisions, action items, etc.) related to Marcus Chen since mid-February.I found 

---

### [PASS] WC-4 — what_changed

**Question**: What changed about Tom Halstead since January 1, 2026?

**Tools used**: get_entity_by_name, get_entity_signals, read_document, what_changed

**Temporal tools hit**: get_entity_by_name, what_changed

**Duration**: 35.7s | **Answer length**: 1621 chars

**Answer preview**:
> I'll help you find what changed about Tom Halstead since January 1, 2026. Let me first look up Tom Halstead's entity information.Now let me check what changed about Tom Halstead since January 1, 2026:Let me get more details by reading Tom Halstead's profile document:Let me also check for any signals (decisions, action items) related to Tom Halstead to provide more context:Based on the knowledge base records, here's what changed about Tom Halstead since January 1, 2026:
> 
> ## Timeline of Changes
> 
> *

---

### [PASS] WC-5 — what_changed

**Question**: What changed about Project Beacon since January 6, 2026?

**Tools used**: get_entity_by_name, get_entity_signals, what_changed

**Temporal tools hit**: get_entity_by_name, what_changed

**Duration**: 27.5s | **Answer length**: 1681 chars

**Answer preview**:
> I'll help you find what changed about Project Beacon since January 6, 2026. Let me first look up the project to get its ID.Now let me check what changed about Project Beacon since January 6, 2026.The change tracking shows no structural changes to Project Beacon's entity data since January 6, 2026. However, let me check if there have been any signals (decisions, action items, or key points) related to the project during this time period to give you a more complete picture.Based on the signals in 

---

### [PASS] WCB-1 — what_changed_between

**Question**: What changed about Project Atlas between February 1 and February 28, 2026?

**Tools used**: get_entity_by_name, get_entity_signals, what_changed_between

**Temporal tools hit**: get_entity_by_name, what_changed_between

**Duration**: 30.9s | **Answer length**: 1531 chars

**Answer preview**:
> I'll help you find what changed about Project Atlas between February 1 and February 28, 2026. Let me first look up the entity ID for Project Atlas.Great! I found Project Atlas. Now let me check what changed about it between February 1 and February 28, 2026.The temporal tracking shows no recorded changes to Project Atlas's entity data between February 1 and February 28, 2026. Let me check if there were any signals (decisions, action items, or key points) related to Project Atlas during that time 

---

### [PASS] WCB-2 — what_changed_between

**Question**: What changed about Elena Voss between January 6 and March 10, 2026?

**Tools used**: get_entity_by_name, what_changed_between

**Temporal tools hit**: get_entity_by_name, what_changed_between

**Duration**: 22.0s | **Answer length**: 978 chars

**Answer preview**:
> I'll help you find what changed about Elena Voss between those dates. Let me first look up her entity ID.Now let me check what changed about Elena between January 6 and March 10, 2026.Based on the temporal data, **Elena Voss was created in the knowledge base on January 6, 2026**, and **no changes were made to her profile between January 6 and March 10, 2026**.
> 
> Here's what we know about her state during that period:
> - **Title**: Senior Consultant
> - **Company**: Meridian Partners
> - **Department**

---

### [PASS] WCB-3 — what_changed_between

**Question**: What changed about the Beacon project between February 3 and March 14, 2026?

**Tools used**: get_entity_by_name, get_entity_signals, what_changed_between

**Temporal tools hit**: get_entity_by_name, what_changed_between

**Duration**: 27.4s | **Answer length**: 1628 chars

**Answer preview**:
> I'll help you find out what changed about the Beacon project between those dates. Let me first look up the Beacon project to get its entity ID.Now let me check what changed about Project Beacon between February 3 and March 14, 2026.The temporal change tracking shows no property or relationship changes were recorded for Project Beacon between those dates. Let me check if there were any signals (decisions, action items, or key points) related to the project during that period.Based on the signals 

---

### [PASS] WCB-4 — what_changed_between

**Question**: What changed about Sam Okoro between January 1 and March 1, 2026?

**Tools used**: get_entity_by_name, what_changed_between

**Temporal tools hit**: get_entity_by_name, what_changed_between

**Duration**: 22.9s | **Answer length**: 1294 chars

**Answer preview**:
> I'll help you find what changed about Sam Okoro between those dates. Let me first look up Sam Okoro's entity ID, then check what changed during that period.Now let me check what changed about Sam Okoro between January 1 and March 1, 2026.Based on the temporal query, **Sam Okoro was newly added to the knowledge base during this period**. Specifically:
> 
> ## Key Change: Entity Creation
> - **Created:** January 6, 2026
> - Sam Okoro's profile was first added to the system on this date
> 
> ## Initial State (

---

### [PASS] WCB-5 — what_changed_between

**Question**: How did the Vanguard Group account situation change between January 27 and March 3, 2026?

**Tools used**: get_entity_by_name, list_meeting_documents, read_document, what_changed_between

**Temporal tools hit**: get_entity_by_name, what_changed_between

**Duration**: 54.8s | **Answer length**: 2815 chars

**Answer preview**:
> I'll help you understand how the Vanguard Group account changed between those dates. Let me start by looking up the Vanguard Group entity.Now let me check what changed for this account between January 27 and March 3, 2026.The entity wasn't found at those timestamps. I notice the Vanguard Group entity has a "valid_from" date of March 24, 2026, which suggests it was created very recently. Let me search for meeting documents and signals from that time period to see if there were discussions about V

---

### [PASS] GAO-1 — graph_as_of

**Question**: Show me the knowledge graph around Project Atlas as of January 15, 2026. What entities were connected to it?

**Tools used**: get_entity_by_name, graph_as_of

**Temporal tools hit**: get_entity_by_name, graph_as_of

**Duration**: 36.0s | **Answer length**: 2037 chars

**Answer preview**:
> I'll help you visualize the knowledge graph around Project Atlas as of January 15, 2026. Let me start by finding the entity ID for Project Atlas.Now let me reconstruct the knowledge graph around Project Atlas as it existed on January 15, 2026:The result is quite large. Let me try with a smaller depth to get a more focused view of the immediate connections:Perfect! Here's what the knowledge graph around **Project Atlas** looked like on **January 15, 2026**:
> 
> ## Core Project Information
> - **Status

---

### [PASS] GAO-2 — graph_as_of

**Question**: What was the graph around Tom Halstead as of February 15, 2026?

**Tools used**: get_entity_by_name, graph_as_of

**Temporal tools hit**: get_entity_by_name, graph_as_of

**Duration**: 44.9s | **Answer length**: 2001 chars

**Answer preview**:
> I'll help you reconstruct the graph around Tom Halstead as of February 15, 2026. Let me first look up Tom Halstead's entity ID, then retrieve the historical graph state.Now let me reconstruct the graph around Tom Halstead as it existed on February 15, 2026:The result is quite large. Let me try with a smaller depth to focus on Tom Halstead's immediate connections:## Graph Around Tom Halstead as of February 15, 2026
> 
> Here's what the knowledge graph looked like around Tom Halstead on that date:
> 
> ##

---

### [PASS] GAO-3 — graph_as_of

**Question**: Show the graph around Marcus Chen as of March 15, 2026. How many entities are connected to him?

**Tools used**: get_entity_by_name, graph_as_of

**Temporal tools hit**: get_entity_by_name, graph_as_of

**Duration**: 46.2s | **Answer length**: 1773 chars

**Answer preview**:
> I'll help you visualize the graph around Marcus Chen as of March 15, 2026. Let me first find his entity ID, then reconstruct the temporal subgraph.The result was too large. Let me try with a smaller depth to see Marcus Chen's direct connections as of March 15, 2026:## Graph Around Marcus Chen as of March 15, 2026
> 
> Here's the temporal subgraph showing Marcus Chen's direct connections as they existed on March 15, 2026:
> 
> ### **Connected Entities: 19 total**
> 
> **People (9):**
> - Derek Osman - Junior C

---

### [PASS] GAO-4 — graph_as_of

**Question**: What was the graph around Polaris Health as of February 25, 2026?

**Tools used**: get_entity_by_name, graph_as_of

**Temporal tools hit**: get_entity_by_name, graph_as_of

**Duration**: 24.5s | **Answer length**: 1114 chars

**Answer preview**:
> I'll help you reconstruct the knowledge graph around Polaris Health as it existed on February 25, 2026. Let me first look up this entity.Now let me reconstruct the graph around Polaris Health as it existed on February 25, 2026:Based on the temporal query results, **Polaris Health did not exist in the knowledge graph on February 25, 2026**.
> 
> The entity "Polaris Health" was first added to the knowledge base on **March 24, 2026** (today), as indicated by its `valid_from` timestamp. Since you're ask

---

### [PASS] GAO-5 — graph_as_of

**Question**: Show the graph around the Beacon Squad team as of March 1, 2026.

**Tools used**: get_entity_by_name, graph_as_of

**Temporal tools hit**: get_entity_by_name, graph_as_of

**Duration**: 24.6s | **Answer length**: 883 chars

**Answer preview**:
> I'll help you visualize the graph around the Beacon Squad team as it existed on March 1, 2026. Let me first find the team entity.Now let me reconstruct the graph around Beacon Squad as it existed on March 1, 2026:The graph reconstruction shows that **Beacon Squad did not exist on March 1, 2026**. 
> 
> According to the entity metadata, the Beacon Squad team was created on **March 24, 2026** (today), which is after the requested date of March 1, 2026. This means:
> 
> - **Nodes**: None (the team and its 

---

### [PASS] FC-1 — find_contradictions

**Question**: Are there any contradicting signals about Project Atlas?

**Tools used**: find_contradictions, get_entity_by_name

**Temporal tools hit**: find_contradictions, get_entity_by_name

**Duration**: 22.0s | **Answer length**: 756 chars

**Answer preview**:
> I'll help you find any contradicting signals about Project Atlas. Let me first look up the project to get its entity ID.Now let me check for contradicting signals about Project Atlas.Good news! I analyzed 8 signals related to Project Atlas and **found no contradicting signals**. This means that all the recorded decisions, action items, key points, and insights about Project Atlas are consistent with each other - there are no conflicting statements like "project on track" followed by "project del

---

### [PASS] FC-2 — find_contradictions

**Question**: Are there contradictions about the Vanguard Replatform project? Any conflicting promises?

**Tools used**: find_contradictions, get_entity_by_name, get_entity_signals, list_meeting_documents, read_document

**Temporal tools hit**: find_contradictions, get_entity_by_name

**Duration**: 70.2s | **Answer length**: 2955 chars

**Answer preview**:
> I'll help you find any contradictions about the Vanguard Replatform project. Let me start by looking up this project.Now let me check for contradictions in signals about this project.The automated contradiction detection didn't find direct conflicts. Let me review all the signals about this project to look for any conflicting promises or commitments.I found some signals that suggest potential conflicts. Let me read the project details to understand the full context of commitments made.I can see 

---

### [PASS] FC-3 — find_contradictions

**Question**: Are there contradicting signals about the Beacon project budget?

**Tools used**: find_contradictions, get_entity_by_name, get_entity_signals, list_meeting_documents, read_document

**Temporal tools hit**: find_contradictions, get_entity_by_name

**Duration**: 50.7s | **Answer length**: 2147 chars

**Answer preview**:
> I'll help you find any contradicting signals about the Beacon project budget. Let me start by looking up the Beacon project entity.Now let me check for contradicting signals about the Beacon project:The contradiction detection didn't find automatic conflicts. Let me examine all the signals about the Beacon project to look for budget-related contradictions manually:Based on the signals, I can see a potential contradiction around the Beacon project budget. Let me read the budget review meeting doc

---

### [PASS] FC-4 — find_contradictions

**Question**: Are there contradictions about Nadia Reeves? Any conflicting information about her plans?

**Tools used**: find_contradictions, get_entity_by_name, get_entity_signals

**Temporal tools hit**: find_contradictions, get_entity_by_name

**Duration**: 32.7s | **Answer length**: 1590 chars

**Answer preview**:
> I'll help you check for contradictions about Nadia Reeves. Let me start by looking up her entity information.Now let me check for contradictions in signals about Nadia Reeves:The contradiction detection found **no conflicting information** among the 3 signals analyzed for Nadia Reeves. Let me get more details about what signals we have for her to give you a complete picture:## No Direct Contradictions Found
> 
> The analysis of signals about Nadia Reeves shows **no explicit contradictions**, but the

---

### [PASS] FC-5 — find_contradictions

**Question**: Are there contradictions about Derek Osman's performance? Has the assessment changed over time?

**Tools used**: find_contradictions, get_entity_by_name, get_entity_signals, list_meeting_documents, read_document, search_meeting_transcripts

**Temporal tools hit**: find_contradictions, get_entity_by_name

**Duration**: 61.9s | **Answer length**: 2332 chars

**Answer preview**:
> I'll help you investigate whether there are contradictions about Derek Osman's performance and how the assessment has changed over time. Let me start by finding Derek's entity information.Now let me check for contradictions in the signals about Derek's performance:The system didn't detect direct contradictions, but let me examine all the signals about Derek to see how assessments have evolved over time:Let me search for discussions about Derek's performance in meeting transcripts to get a more c

---

### [PASS] FC-6 — find_contradictions

**Question**: Are there any conflicting signals about Tom Halstead's situation at the firm?

**Tools used**: find_contradictions, get_entity_by_name, get_entity_signals

**Temporal tools hit**: find_contradictions, get_entity_by_name

**Duration**: 32.4s | **Answer length**: 1721 chars

**Answer preview**:
> I'll help you find any conflicting signals about Tom Halstead. Let me start by looking up his entity information.Now let me check for any conflicting signals about Tom Halstead:The contradiction detection found no conflicting signals. Let me look at all the signals related to Tom Halstead to give you a complete picture:Based on my analysis, there are **no direct contradictions** in the signals about Tom Halstead - the system analyzed 3 signals and found no conflicting information. However, there

---

### [PASS] TBR-1 — temporal_blast_radius

**Question**: What is the blast radius of Tom Halstead's departure? What entities were affected when he left on February 28?

**Tools used**: get_entity_by_name, temporal_blast_radius

**Temporal tools hit**: get_entity_by_name, temporal_blast_radius

**Duration**: 37.3s | **Answer length**: 2960 chars

**Answer preview**:
> I'll help you understand the impact of Tom Halstead's departure. Let me first find his entity ID, then analyze what entities were connected to him at the time of his departure.Found Tom Halstead's entity. Now let me trace the blast radius of his departure on February 28, 2026.## Blast Radius of Tom Halstead's Departure (February 28, 2026)
> 
> Based on the temporal graph analysis, Tom Halstead's departure created a significant blast radius affecting **22 entities** across multiple organizational lay

---

### [PASS] TBR-2 — temporal_blast_radius

**Question**: What is the blast radius around Nadia Reeves as of March 5, 2026? What depends on her?

**Tools used**: get_entity_by_name, temporal_blast_radius

**Temporal tools hit**: get_entity_by_name, temporal_blast_radius

**Duration**: 39.4s | **Answer length**: 2536 chars

**Answer preview**:
> I'll find the blast radius around Nadia Reeves as of March 5, 2026. Let me start by looking up her entity ID.Now let me find the blast radius around Nadia Reeves as of March 5, 2026:## Blast Radius Around Nadia Reeves as of March 5, 2026
> 
> As of March 5, 2026, Nadia Reeves had **extensive dependencies** across Meridian Partners. Notably, she had just started her sabbatical on March 1, 2026, so this blast radius represents what had been depending on her and what needed transition coverage.
> 
> ### **

---

### [PASS] TBR-3 — temporal_blast_radius

**Question**: What entities are connected to Priya Kapoor as of March 10? Show me her impact radius.

**Tools used**: get_entity_by_name, temporal_blast_radius

**Temporal tools hit**: get_entity_by_name, temporal_blast_radius

**Duration**: 43.2s | **Answer length**: 2653 chars

**Answer preview**:
> I'll help you find the entities connected to Priya Kapoor as of March 10 and show her impact radius. Let me start by looking up her entity ID.Perfect! I found Priya Kapoor's entity. Now let me use the temporal blast radius tool to see what entities were connected to her as of March 10, 2026.Perfect! I now have a complete temporal blast radius showing all entities connected to Priya Kapoor as of March 10, 2026. Let me organize this by depth to show her impact radius clearly.
> 
> ## Priya Kapoor's Im

---

### [PASS] TBR-4 — temporal_blast_radius

**Question**: What is the blast radius of Project Atlas as of February 21, the crisis point?

**Tools used**: get_entity_by_name, temporal_blast_radius

**Temporal tools hit**: get_entity_by_name, temporal_blast_radius

**Duration**: 46.0s | **Answer length**: 3070 chars

**Answer preview**:
> I'll help you find the blast radius of Project Atlas as of February 21, 2026. Let me first look up the entity ID for Project Atlas.Now let me find the blast radius as of February 21, 2026 (the crisis point):## Blast Radius of Project Atlas as of February 21, 2026 (Crisis Point)
> 
> The temporal blast radius analysis reveals **23 entities** connected to Project Atlas at the crisis point, spanning 3 degrees of separation:
> 
> ### **Immediate Impact Zone (Depth 1):**
> 
> **People Directly Working on Atlas:*

---

### [PASS] TBR-5 — temporal_blast_radius

**Question**: What is the blast radius around the Beacon project as of March 14 when it was paused?

**Tools used**: get_entity_by_name, temporal_blast_radius

**Temporal tools hit**: get_entity_by_name, temporal_blast_radius

**Duration**: 40.5s | **Answer length**: 2867 chars

**Answer preview**:
> I'll help you find the blast radius around the Beacon project as of March 14 when it was paused. Let me start by looking up the Beacon project to get its entity ID.Now let me find all entities that were connected to the Beacon project as of March 14, 2026 when it was paused.## Blast Radius Analysis: Project Beacon as of March 14, 2026
> 
> The temporal blast radius around Project Beacon when it was paused on March 14 reveals significant interconnections across Meridian Partners' portfolio. Here's wh

---

### [PASS] ART-1 — active_relationships_at_time

**Question**: What relationships did Priya Kapoor have on January 15, 2026?

**Tools used**: active_relationships_at_time, get_entity_by_name

**Temporal tools hit**: active_relationships_at_time, get_entity_by_name

**Duration**: 26.5s | **Answer length**: 1564 chars

**Answer preview**:
> I'll help you find what relationships Priya Kapoor had on January 15, 2026. Let me start by looking up Priya Kapoor's entity ID.Now let me query what relationships Priya Kapoor had active on January 15, 2026.Based on the historical data, here are the relationships Priya Kapoor had active on **January 15, 2026**:
> 
> ## **Team Membership**
> - **Member of**: Atlas Squad
> 
> ## **Project Work** (3 projects)
> - Project Atlas
> - Project Beacon  
> - Polaris Onboarding
> 
> ## **Direct Collaborations** (7 people)
> Pr

---

### [PASS] ART-2 — active_relationships_at_time

**Question**: What relationships did Priya Kapoor have on March 1, 2026? How did they change?

**Tools used**: active_relationships_at_time, get_entity_by_name, what_changed

**Temporal tools hit**: active_relationships_at_time, get_entity_by_name

**Duration**: 31.4s | **Answer length**: 1452 chars

**Answer preview**:
> I'll help you find Priya Kapoor's relationships on March 1, 2026 and see how they changed. Let me start by looking up Priya Kapoor's entity ID.Now let me check what relationships Priya had on March 1, 2026:Now let me see what changed for Priya since March 1, 2026:Based on the temporal data, here's what Priya Kapoor's relationships looked like on **March 1, 2026**:
> 
> ## Primary Relationships
> 
> **Collaborations (7 people):**
> - Derek Osman
> - Tom Halstead
> - Sam Okoro
> - Marcus Chen
> - Nadia Reeves
> - Rac

---

### [PASS] ART-3 — active_relationships_at_time

**Question**: What relationships did Elena Voss have on March 10, 2026?

**Tools used**: active_relationships_at_time, get_entity_by_name

**Temporal tools hit**: active_relationships_at_time, get_entity_by_name

**Duration**: 28.6s | **Answer length**: 1598 chars

**Answer preview**:
> I'll help you find what relationships Elena Voss had on March 10, 2026. Let me start by looking up Elena Voss to get her entity ID.Great! I found Elena Voss (ID: person-elena-voss). Now let me check what relationships she had active on March 10, 2026.Based on the temporal query for March 10, 2026, here are the relationships Elena Voss had at that time:
> 
> ## **Project Relationships**
> - **Works on Projects:** Project Atlas, Vanguard Replatform
> - **Co-occurrence with Projects:** Polaris Onboarding, 

---

### [PASS] ART-4 — active_relationships_at_time

**Question**: What relationships did Marcus Chen have on January 10 versus March 15? How did his role expand?

**Tools used**: active_relationships_at_time, get_entity_by_name, read_document, what_changed_between

**Temporal tools hit**: active_relationships_at_time, get_entity_by_name

**Duration**: 52.9s | **Answer length**: 2575 chars

**Answer preview**:
> I'll help you understand how Marcus Chen's relationships and role evolved between those two dates. Let me start by finding his entity ID.Now let me check what changed in Marcus Chen's relationships between January 10 and March 15, 2026.Let me check his active relationships at each specific date to see the comparison.Now let me check his relationships on March 15, 2026:I can see the relationship data at both dates. The key finding is a new decision signal that appeared between those dates. Let me

---

### [PASS] ART-5 — active_relationships_at_time

**Question**: What relationships did the Vanguard Group account have on February 1 versus March 5?

**Tools used**: active_relationships_at_time, get_entity_by_name

**Temporal tools hit**: active_relationships_at_time, get_entity_by_name

**Duration**: 40.9s | **Answer length**: 1812 chars

**Answer preview**:
> I'll help you compare the relationships for the Vanguard Group account between those two dates. Let me start by finding the entity.Now let me check what relationships were active on February 1, 2026:Now let me check what relationships were active on March 5, 2026:Based on the temporal relationship data, I can see that the Vanguard Group account had **very similar relationships** on both dates, with **one key addition** by March 5:
> 
> ## Relationships Present on Both Dates
> 
> ### Outgoing Relationshi

---

### [PASS] GEP-1 — get_entity_provenance

**Question**: Where did information about Kai Novak come from? What are the original sources?

**Tools used**: get_entity_by_name, get_entity_provenance, list_meeting_documents, read_document

**Temporal tools hit**: get_entity_by_name, get_entity_provenance

**Duration**: 39.0s | **Answer length**: 1812 chars

**Answer preview**:
> I'll help you trace the provenance of information about Kai Novak. Let me first look up this entity and then get its source attribution.## Provenance of Kai Novak Information
> 
> The information about Kai Novak originated from **two meeting sources**:
> 
> ### 1. **Primary Source: Introductory Meeting (February 20, 2026)**
>    - **Meeting**: "Polaris Health: Introductory Meeting"
>    - **Meeting ID**: a1b2c3d4-0008-4000-8000-000000000001
>    - **Date**: February 20, 2026
>    - **Entity created**: This is w

---

### [PASS] GEP-2 — get_entity_provenance

**Question**: What is the provenance trail for the Polaris Onboarding project? When was it first mentioned?

**Tools used**: get_entity_by_name, get_entity_provenance

**Temporal tools hit**: get_entity_by_name, get_entity_provenance

**Duration**: 26.1s | **Answer length**: 1839 chars

**Answer preview**:
> I'll help you find the provenance trail for the Polaris Onboarding project. Let me first look up this project to get its entity ID.Perfect! I found the Polaris Onboarding project. Now let me retrieve its provenance trail to see when it was first mentioned and how it evolved.## Provenance Trail for Polaris Onboarding Project
> 
> Based on the provenance data, here's the complete timeline of when the Polaris Onboarding project was first mentioned and how it evolved:
> 
> ### **First Mention: March 7, 2026

---

### [PASS] GEP-3 — get_entity_provenance

**Question**: Where did information about Jess Nolan come from? What meetings mention her?

**Tools used**: get_entity_by_name, get_entity_provenance, get_entity_signals, list_meeting_documents, read_document

**Temporal tools hit**: get_entity_by_name, get_entity_provenance

**Duration**: 33.9s | **Answer length**: 1600 chars

**Answer preview**:
> I'll help you find the provenance of information about Jess Nolan and discover what meetings mention her.Based on the provenance data and meeting records, here's where information about **Jess Nolan** comes from:
> 
> ## Source Information
> 
> **Entity Profile Created:** January 27, 2026 (valid_from date)
> 
> **Provenance Trail:**
> 1. **Key Point Signal** (March 3, 2026): "Jess Nolan cautiously accepted recovery plan for API compatibility"
> 2. **Signal ID Reference**: a0a60747-770a-5eda-beb8-59bc690aa1dd - 

---

### [PASS] GEP-4 — get_entity_provenance

**Question**: What is the provenance of information about Derek Osman? How has our understanding of him evolved?

**Tools used**: get_entity_by_name, get_entity_provenance, read_document

**Temporal tools hit**: get_entity_by_name, get_entity_provenance

**Duration**: 33.8s | **Answer length**: 2754 chars

**Answer preview**:
> I'll help you understand the provenance of information about Derek Osman and how our understanding of him has evolved. Let me start by looking up his entity.Now let me retrieve the provenance chain to see how information about Derek Osman was created and evolved over time.Let me also read Derek's full profile to provide more context about what we know about him now.## Provenance of Information About Derek Osman
> 
> Our understanding of Derek Osman has evolved through **three key moments** captured 

---

### [PASS] GEP-5 — get_entity_provenance

**Question**: Where did the information about the Atlas project come from? Trace its sources.

**Tools used**: get_entity_by_name, get_entity_provenance, get_entity_relationships, get_entity_signals, list_meeting_documents, query_graph_cypher, read_document, search_knowledge_graph, search_meeting_transcripts

**Temporal tools hit**: get_entity_by_name, get_entity_provenance

**Duration**: 65.1s | **Answer length**: 519 chars

**Answer preview**:
> I'll help you trace the sources of information about the Atlas project. Let me start by finding the Atlas project in the knowledge graph.Found it! The project is called "Coral Islands Atlas". Now let me trace its provenance to see where the information came from.The provenance history is empty, but I can see from the metadata that the Atlas project information came from a meeting. Let me read the entity document and check the meeting it references.Let me check the meeting document where this pro

---

