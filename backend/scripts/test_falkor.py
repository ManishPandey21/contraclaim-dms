import sys
import os
from dotenv import load_dotenv

# Add the current directory to Python path
sys.path.append(os.path.dirname(__file__))

load_dotenv()

from rbac_backend.services.falkor_graph_service import FalkorGraphService, normalize_letter_code

def test_falkor():
    svc = FalkorGraphService()
    print('Enabled:', svc.enabled)
    result = svc.get_thread(normalize_letter_code('6908464e3541dd7ac9f77f2b'))
    print('Thread:', result)

if __name__ == '__main__':
    test_falkor()