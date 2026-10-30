import json
import re

with open('c:/SaaS/projectDMS/backend/rbac_backend/initial_data/default_roles.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace documents:read with new permissions in the list of permissions
def replace_permissions(match):
    perms = match.group(0)
    perms = perms.replace('\"documents:read\",', '\"documents:read\",\n            \"dms.document.view\",\n            \"dms.dashboard.view\",')
    perms = perms.replace('\"documents:create\",', '\"documents:create\",\n            \"dms.document.upload\",')
    return perms

content = re.sub(r'\"permissions\": \[.*?\]', replace_permissions, content, flags=re.DOTALL)

# Now remove drafting.request.create from DMS roles
import ast
lines = content.split('\n')
out_lines = []
in_drafting_role = False

for line in lines:
    if '\"_id\": \"superadmin\"' in line or '\"_id\": \"contraclaim' in line:
        in_drafting_role = True
    elif '\"_id\": ' in line:
        in_drafting_role = False

    if '\"drafting.request.create\"' in line and not in_drafting_role:
        continue # Skip this line
    out_lines.append(line)

with open('c:/SaaS/projectDMS/backend/rbac_backend/initial_data/default_roles.py', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out_lines))
