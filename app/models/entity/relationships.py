"""
Models for entity.relationships
"""

from ..base import Enum

# Entity Enrichment Models - Issue #59


class RelationshipType(str, Enum):
    """Types of relationships between entities"""

    REPORTS_TO = "reports_to"
    MANAGES = "manages"
    COLLABORATES_WITH = "collaborates_with"
    MEMBER_OF = "member_of"
    HAS_MEMBER = "has_member"
    LEADS = "leads"
    LED_BY = "led_by"
    BELONGS_TO = "belongs_to"

    def get_inverse(self) -> "RelationshipType":
        """Get the inverse relationship type"""
        inverse_map = {
            RelationshipType.REPORTS_TO: RelationshipType.MANAGES,
            RelationshipType.MANAGES: RelationshipType.REPORTS_TO,
            RelationshipType.MEMBER_OF: RelationshipType.HAS_MEMBER,
            RelationshipType.HAS_MEMBER: RelationshipType.MEMBER_OF,
            RelationshipType.LEADS: RelationshipType.LED_BY,
            RelationshipType.LED_BY: RelationshipType.LEADS,
            RelationshipType.COLLABORATES_WITH: RelationshipType.COLLABORATES_WITH,
            RelationshipType.BELONGS_TO: RelationshipType.HAS_MEMBER,
        }
        return inverse_map.get(self, self)

