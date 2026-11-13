import uuid


def generate_uuid() -> str:
    """
    Generate a standard UUID4 string for documents and chunks.
    """
    return str(uuid.uuid4())

