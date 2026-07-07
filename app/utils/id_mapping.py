"""
Deterministic ID mapping utilities
Generates display_id from bot_id without needing storage
"""

import uuid


def generate_display_id(bot_id: str) -> str:
    """
    Generate a deterministic display_id from a bot_id.
    This allows us to always recreate the same display_id without storage.

    Args:
        bot_id: The meeting identifier

    Returns:
        A deterministic UUID display_id
    """
    # Create a namespaced UUID - this will always generate the same UUID for the same input
    namespace = uuid.UUID(
        "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
    )  # Standard namespace UUID
    return str(uuid.uuid5(namespace, f"display-{bot_id}"))


def get_display_id_for_bot(bot_id: str) -> str:
    """Alias for generate_display_id for clarity"""
    return generate_display_id(bot_id)


# Test that it's deterministic
if __name__ == "__main__":
    test_bot_id = "fff1a87a-a4d5-4154-a1df-d5f31795b88b"
    display_id1 = generate_display_id(test_bot_id)
    display_id2 = generate_display_id(test_bot_id)
    print(f"Bot ID: {test_bot_id}")
    print(f"Display ID 1: {display_id1}")
    print(f"Display ID 2: {display_id2}")
    print(f"Are they the same? {display_id1 == display_id2}")
