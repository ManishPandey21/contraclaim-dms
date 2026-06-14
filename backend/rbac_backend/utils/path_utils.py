import re
from datetime import datetime

def sanitize_name(name: str, max_len: int = 10) -> str:
    """
    Sanitize organization/project name according to spec:
    - Lowercase
    - Replace non-alphanumeric with hyphens
    - Trim leading/trailing hyphens
    - Limit to max_len chars
    """
    if not name:
        return "untitled"
    sanitized = re.sub(r'[^a-z0-9]', '-', name.lower())
    sanitized = sanitized.strip('-')
    sanitized = re.sub(r'-+', '-', sanitized)
    return sanitized[:max_len]

def generate_path_structures(org_name: str, project_name: str, date_str: str) -> tuple[str, str]:
    """
    Generate pathStructure and pathStructure1 from inputs.
    
    Args:
        org_name: Organization name
        project_name: Project name
        date_str: Date string (YYYY-MM-DD or parsable)
    
    Returns:
        tuple: (pathStructure, pathStructure1)
    """
    # Parse date
    try:
        date_obj = datetime.strptime(date_str, '%Y-%m-%d') if len(date_str) == 10 else datetime.fromisoformat(date_str)
        year = str(date_obj.year)
        month = date_obj.strftime('%m')
    except ValueError:
        # Fallback to current date
        date_obj = datetime.now()
        year = str(date_obj.year)
        month = date_obj.strftime('%m')
    
    sanitized_org = sanitize_name(org_name)
    sanitized_project = sanitize_name(project_name)
    
    path_structure = f"{sanitized_org}/{sanitized_project}/{year}/{month}"
    path_structure1 = f"{sanitized_org}/{sanitized_project}"
    
    return path_structure, path_structure1
