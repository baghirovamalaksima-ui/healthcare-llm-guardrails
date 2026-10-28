"""Frozen synthetic development cases; not a clinical validation or held-out evaluation."""
import argparse
import hashlib
import json
import platform
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.main import DATA, Turn, respond  # noqa: E402
from app.retrieval import Encoder  # noqa: E402


def evaluate():
    dataset = ROOT / 'data/evaluation.json'
    cases = json.loads(dataset.read_text(encoding='utf-8'))
    policies = json.loads(DATA.read_text(encoding='utf-8'))
    encoder = Encoder()
    rows = []
    elapsed = []
    for case in cases:
        started = time.perf_counter()
        reply = respond(Turn(message=case['message'], consent=case['consent']), policies, encoder)
        elapsed.append((time.perf_counter() - started) * 1000)
        rows.append({'id': case['id'], 'expected': case['expected'], 'actual': reply.route,
                     'correct': case['expected'] == reply.route})
    digest = hashlib.sha256()
    for path in [*sorted((ROOT / 'app').glob('*.py')), DATA, dataset, Path(__file__)]:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return {'metadata': {'python': platform.python_version(), 'platform': platform.platform(),
                         'mode': 'offline-extractive', 'input_and_code_sha256': digest.hexdigest(),
                         'dataset': '30 synthetic development cases; overlaps unit-test scenarios'},
            'summary': {'cases': len(rows), 'correct': sum(r['correct'] for r in rows),
                        'route_accuracy': sum(r['correct'] for r in rows) / len(rows),
                        'mean_latency_ms': sum(elapsed) / len(elapsed),
                        'p95_latency_ms': sorted(elapsed)[int(.95 * (len(elapsed) - 1))],
                        'routes': dict(Counter(r['actual'] for r in rows))},
            'failures': [r for r in rows if not r['correct']], 'rows': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/evaluation.json')
    args = parser.parse_args()
    report = evaluate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report['summary'], indent=2))
