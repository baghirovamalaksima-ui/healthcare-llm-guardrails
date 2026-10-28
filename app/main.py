"""Synthetic-data healthcare onboarding reference API."""
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from app.provider import select_quotes
from app.retrieval import Encoder, tokens, vector_rank
from app.runtime import BoundedRequests, Security, Telemetry

DATA = Path(__file__).resolve().parents[1] / 'data' / 'policies.json'
DISCLAIMER = 'Administrative demo only. A qualified clinician must assess medical concerns.'


class Turn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=4000, pattern=r'\S')
    consent: bool = False
    state: Literal['start', 'consented', 'ready'] = 'start'
    provider: Literal['extractive', 'openai'] = 'extractive'


class Reply(BaseModel):
    route: Literal['urgent_handoff', 'consent_required', 'blocked', 'clinical_handoff',
                   'privacy_handoff', 'answered', 'abstained']
    state: Literal['start', 'consented', 'ready']
    message: str
    claims: list[dict[str, str]] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


URGENT = re.compile(r'\b(chest pain|cannot breathe|can.t breathe|cannot get enough air|'
                    r'(swallowed|took).{0,25}too many (tablets|pills)|overdose|suicid\w*|'
                    r'kill myself|hurt myself|severe bleeding|stroke)\b', re.I)
INJECTION = re.compile(r'ignore.{0,30}(instructions|rules|previous)|system prompt|'
                       r'you are now|reveal.{0,20}(secret|key)|developer message', re.I)
CLINICAL = re.compile(r'\b(diagnos\w*|prescrib\w*|dosage|dose|medicat\w*|treatment|'
                     r'antibiotic\w*|symptom\w*|pain|depress\w*|anxi\w*)\b', re.I)
PII_PATTERNS = [r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', r'(?<!\w)\+?\d[\d ()-]{7,}\d',
                r'\b(?:my name is|patient name\s*:)\s*[\w-]+(?:\s+[\w-]+){0,2}',
                r'\b[\w.+-]+\s+(?:at|\[at\])\s+[\w.-]+\s+(?:dot|\[dot\])\s+\w+\b']


def redact(text):
    for pattern in PII_PATTERNS:
        text = re.sub(pattern, '[REDACTED]', text, flags=re.I)
    return text


def respond(turn, policies, encoder, selector=select_quotes):
    """Ordering matters: emergency handoff precedes consent, model never overrides policy."""
    text = turn.message.strip()
    if URGENT.search(text):
        return Reply(route='urgent_handoff', state='start', message=
                     'This may need urgent human assistance. Contact local emergency services or '
                     'an available clinician now. This service cannot assess emergencies.')
    if not turn.consent:
        return Reply(route='consent_required', state='start', message=
                     'Please confirm consent for this synthetic onboarding demonstration. '
                     'Do not submit real patient details.')
    if INJECTION.search(text):
        return Reply(route='blocked', state='consented', message=
                     'I can help with administrative onboarding questions only.')
    if CLINICAL.search(text):
        return Reply(route='clinical_handoff', state='consented', message=
                     'A qualified clinician must handle that question. I can help with booking '
                     'and other administrative information.')
    cleaned = redact(text)
    if cleaned != text:
        return Reply(route='privacy_handoff', state='consented', message=
                     'Please ask an administrative question without personal details. '
                     'Use the approved secure channel for patient information.')
    # Supported language is deliberately English; mixed-script input must not reach a model.
    if re.search(r'[^\x00-\x7f]', cleaned):
        return Reply(route='abstained', state='consented', message=
                     'This demo supports English only. Please contact a human for language support.')
    ranked = vector_rank(cleaned, policies, encoder, 3)
    evidence = [r for score, r in ranked if score >= 0.15 and
                len(set(tokens(cleaned)) & set(tokens(r['text']))) >= 2]
    if not evidence:
        return Reply(route='abstained', state='consented', message=
                     'I could not find enough administrative evidence. Please contact the service team.')
    claims = (selector(cleaned, evidence) if turn.provider == 'openai' else
              [{'source': evidence[0]['id'], 'quote': evidence[0]['text']}])
    # Defense in depth also validates injected/custom provider implementations.
    sources = {r['id']: r['text'] for r in evidence}
    if not claims or any(not isinstance(c, dict) or not isinstance(c.get('quote'), str) or
                         len(c['quote'].strip()) < 12 or c.get('source') not in sources or
                         c['quote'] not in sources[c['source']] for c in claims):
        return Reply(route='abstained', state='consented', message=
                     'No validated quotation was available. Please contact the service team.')
    return Reply(route='answered', state='ready', message='Here is the administrative policy information.',
                 claims=claims)


def create_app(keys=None, rpm=120):
    security = Security(keys, rpm)
    telemetry = Telemetry()

    @asynccontextmanager
    async def lifespan(api):
        api.state.encoder = Encoder(os.getenv('EMBEDDING_MODEL') or None)
        api.state.policies = json.loads(DATA.read_text(encoding='utf-8'))
        yield

    api = FastAPI(title='Healthcare LLM Guardrails', version='1.0.0', lifespan=lifespan)
    api.add_middleware(BoundedRequests)

    @api.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic errors may include the raw patient input; never echo them.
        return JSONResponse({'detail': 'Invalid request schema'}, status_code=422)

    @api.get('/healthz')
    def health():
        return {'status': 'ok'}

    @api.get('/readyz')
    def ready():
        return {'ready': bool(api.state.policies)}

    @api.get('/metrics')
    def metrics(tenant=Depends(security.tenant)):
        return telemetry.snapshot()

    @api.post('/v1/onboarding', response_model=Reply)
    def onboarding(turn: Turn, tenant=Depends(security.tenant)):
        result = respond(turn, api.state.policies, api.state.encoder)
        telemetry.increment(result.route)
        return result

    return api
