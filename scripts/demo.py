"""Run a complete synthetic API conversation in-process, without credentials or network."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402

DEMO_KEY = 'in-process-demo-key-not-for-deployment'
with TestClient(create_app({DEMO_KEY: 'demo'})) as client:
    for request in [
        {'message': 'How does appointment booking work?', 'consent': False},
        {'message': 'How does appointment booking work?', 'consent': True},
        {'message': 'Please prescribe medication', 'consent': True},
        {'message': 'I cannot breathe', 'consent': False},
    ]:
        response = client.post('/v1/onboarding', headers={'X-API-Key': DEMO_KEY}, json=request)
        response.raise_for_status()
        print(json.dumps({'request': request, 'response': response.json()}, indent=2))
