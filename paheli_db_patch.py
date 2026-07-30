# This file shows the ONE function to add to paheli_db.py
# Add this function anywhere after the existing get_used_riddle_ids() function

def get_group_session_count(group_id: int) -> int:
    """Return total number of paheli sessions started in this group.
    Used to determine the next difficulty in the rotation cycle."""
    return _get_db().paheli_sessions.count_documents({"group_id": group_id})
