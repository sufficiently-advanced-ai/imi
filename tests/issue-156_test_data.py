"""Test data for issue #156: Domain Configuration Schema."""

# Valid consulting firm domain configuration
CONSULTING_FIRM_CONFIG = {
    "domain": {
        "id": "consulting_firm",
        "name": "Consulting Firm",
        "version": "1.0.0",
        "entities": {
            "account": {
                "attributes": [
                    {"name": "name", "type": "string", "required": True},
                    {"name": "industry", "type": "enum", "enum": ["tech", "finance", "healthcare", "retail", "other"]},
                    {"name": "revenue", "type": "number", "unit": "USD"},
                    {"name": "founded_date", "type": "date"},
                    {"name": "is_active", "type": "boolean"}
                ],
                "relationships": [
                    {"type": "has_projects", "target": "project", "cardinality": "one_to_many"},
                    {"type": "managed_by", "target": "person", "cardinality": "many_to_one"}
                ]
            },
            "project": {
                "attributes": [
                    {"name": "name", "type": "string", "required": True},
                    {"name": "budget", "type": "number", "unit": "USD"},
                    {"name": "start_date", "type": "date"},
                    {"name": "end_date", "type": "date"},
                    {"name": "status", "type": "enum", "enum": ["planning", "active", "completed", "on_hold"]}
                ],
                "relationships": [
                    {"type": "belongs_to_account", "target": "account", "cardinality": "many_to_one"},
                    {"type": "has_team_members", "target": "person", "cardinality": "many_to_many"},
                    {"type": "managed_by", "target": "person", "cardinality": "many_to_one"}
                ]
            },
            "person": {
                "attributes": [
                    {"name": "name", "type": "string", "required": True},
                    {"name": "email", "type": "string"},
                    {"name": "role", "type": "string"},
                    {"name": "department", "type": "string"}
                ],
                "relationships": [
                    {"type": "works_on_projects", "target": "project", "cardinality": "many_to_many"},
                    {"type": "manages_accounts", "target": "account", "cardinality": "one_to_many"},
                    {"type": "member_of_team", "target": "team", "cardinality": "many_to_one"}
                ]
            },
            "team": {
                "attributes": [
                    {"name": "name", "type": "string", "required": True},
                    {"name": "department", "type": "string"},
                    {"name": "focus_area", "type": "string"}
                ],
                "relationships": [
                    {"type": "has_members", "target": "person", "cardinality": "one_to_many"},
                    {"type": "works_on_projects", "target": "project", "cardinality": "many_to_many"}
                ]
            }
        },
        "intelligence_patterns": {
            "risk_detection": [
                {
                    "name": "scope_creep",
                    "triggers": [
                        {"entity": "project", "condition": "budget_variance > 20%"},
                        {"entity": "project", "condition": "timeline_extension > 30_days"}
                    ],
                    "priority": "high",
                    "actions": ["alert_pm", "escalate_to_director"]
                },
                {
                    "name": "resource_conflicts", 
                    "triggers": [
                        {"entity": "person", "condition": "assigned_projects > 3"},
                        {"entity": "person", "condition": "utilization > 120%"}
                    ],
                    "priority": "medium",
                    "actions": ["notify_resource_manager"]
                }
            ],
            "opportunity_detection": [
                {
                    "name": "upsell_opportunity",
                    "triggers": [
                        {"entity": "account", "condition": "project_success_rate > 80%"},
                        {"entity": "account", "condition": "revenue_growth > 15%"}
                    ],
                    "priority": "medium",
                    "actions": ["notify_sales"]
                }
            ]
        },
        "extraction_priorities": {
            "meetings": [
                {"pattern": "action_items_by_project", "priority": "high"},
                {"pattern": "risk_escalations", "priority": "high"},
                {"pattern": "budget_discussions", "priority": "medium"},
                {"pattern": "timeline_updates", "priority": "medium"}
            ],
            "documents": [
                {"pattern": "budget_changes", "priority": "high"},
                {"pattern": "scope_changes", "priority": "high"},
                {"pattern": "team_changes", "priority": "low"}
            ]
        },
        "success_metrics": [
            {
                "name": "project_success_rate",
                "type": "percentage",
                "calculation": "completed_on_time_on_budget / total_projects",
                "target": 0.85
            },
            {
                "name": "client_satisfaction",
                "type": "score",
                "range": [1, 10],
                "target": 8.5
            },
            {
                "name": "resource_utilization",
                "type": "percentage", 
                "calculation": "billable_hours / available_hours",
                "target": 0.75
            }
        ]
    }
}

# Personal CRM domain configuration
PERSONAL_CRM_CONFIG = {
    "domain": {
        "id": "personal_crm",
        "name": "Personal CRM",
        "version": "1.0.0",
        "entities": {
            "contact": {
                "attributes": [
                    {"name": "name", "type": "string", "required": True},
                    {"name": "email", "type": "string"},
                    {"name": "phone", "type": "string"},
                    {"name": "last_contact", "type": "date"},
                    {"name": "relationship_strength", "type": "enum", "enum": ["weak", "moderate", "strong"]}
                ],
                "relationships": [
                    {"type": "works_at", "target": "company", "cardinality": "many_to_one"},
                    {"type": "related_to", "target": "contact", "cardinality": "many_to_many"}
                ]
            },
            "company": {
                "attributes": [
                    {"name": "name", "type": "string", "required": True},
                    {"name": "industry", "type": "string"},
                    {"name": "size", "type": "enum", "enum": ["small", "medium", "large", "enterprise"]}
                ],
                "relationships": [
                    {"type": "has_employees", "target": "contact", "cardinality": "one_to_many"}
                ]
            },
            "activity": {
                "attributes": [
                    {"name": "type", "type": "enum", "enum": ["meeting", "call", "email", "event"]},
                    {"name": "date", "type": "datetime", "required": True},
                    {"name": "notes", "type": "string"}
                ],
                "relationships": [
                    {"type": "with_contact", "target": "contact", "cardinality": "many_to_many"}
                ]
            }
        },
        "intelligence_patterns": {
            "relationship_maintenance": [
                {
                    "name": "follow_up_needed",
                    "triggers": [
                        {"entity": "contact", "condition": "days_since_contact > 90"}
                    ],
                    "priority": "medium"
                }
            ]
        },
        "extraction_priorities": {
            "emails": [
                {"pattern": "contact_mentions", "priority": "high"},
                {"pattern": "meeting_requests", "priority": "high"}
            ]
        },
        "success_metrics": [
            {
                "name": "network_growth",
                "type": "percentage",
                "calculation": "new_contacts_this_quarter / total_contacts"
            }
        ]
    }
}

# Minimal valid configuration
MINIMAL_CONFIG = {
    "domain": {
        "id": "minimal",
        "name": "Minimal Config"
    }
}

# Edge case: Empty configuration
EMPTY_CONFIG = {}

# Edge case: Circular relationships
CIRCULAR_RELATIONSHIPS_CONFIG = {
    "domain": {
        "id": "circular",
        "name": "Circular Test",
        "entities": {
            "a": {
                "attributes": [],
                "relationships": [{"type": "links_to", "target": "b", "cardinality": "one_to_one"}]
            },
            "b": {
                "attributes": [],
                "relationships": [{"type": "links_to", "target": "c", "cardinality": "one_to_one"}]
            },
            "c": {
                "attributes": [],
                "relationships": [{"type": "links_to", "target": "a", "cardinality": "one_to_one"}]
            }
        }
    }
}

# Edge case: Invalid entity references
INVALID_REFERENCES_CONFIG = {
    "domain": {
        "id": "invalid_refs",
        "name": "Invalid References",
        "entities": {
            "project": {
                "attributes": [],
                "relationships": [
                    {"type": "belongs_to", "target": "nonexistent_entity", "cardinality": "many_to_one"}
                ]
            }
        }
    }
}

# Edge case: Invalid attribute types
INVALID_TYPES_CONFIG = {
    "domain": {
        "id": "invalid_types",
        "name": "Invalid Types",
        "entities": {
            "test": {
                "attributes": [
                    {"name": "field1", "type": "invalid_type"},
                    {"name": "field2", "type": "complex_object"}
                ],
                "relationships": []
            }
        }
    }
}

# Edge case: Invalid pattern references
INVALID_PATTERN_CONFIG = {
    "domain": {
        "id": "invalid_patterns",
        "name": "Invalid Patterns",
        "entities": {
            "project": {"attributes": [], "relationships": []}
        },
        "intelligence_patterns": {
            "detection": [
                {
                    "name": "test_pattern",
                    "triggers": [
                        {"entity": "nonexistent_entity", "condition": "test"}
                    ],
                    "priority": "high"
                }
            ]
        }
    }
}