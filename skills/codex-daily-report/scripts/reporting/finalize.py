from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from typing import Any

from .common import atomic_write_batch, canonical_json_hash, redact_value
from .contracts import validate_bundles, validate_submitted_text, validate_evidence_schema
from .daily import (DailyRevisionError, apply_ledger, displayed_items, load_daily,
                    merge_evidence, paths_for, prepare_revision, stable_items)
from .locking import locked_run_state
from .rendering import remove_daily, render_daily, replace_or_append_daily


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def load_existing_state(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    return value if isinstance(value, dict) else {}


def preserve_delivery_history(state, old_state, *, preserve_sent_at):
    for field in ('sent_hashes','empty_notification_hashes','failure_notification_hashes'):
        state[field] = old_state.get(field, [])
    for field in ('empty_notification_sent_at','failure_notification_sent_at'):
        if field in old_state:
            state[field] = old_state[field]
    if preserve_sent_at and 'sent_at' in old_state:
        state['sent_at'] = old_state['sent_at']


def same_markdown_content(left, right):
    return [x.rstrip() for x in left.splitlines() if x.strip()] == [x.rstrip() for x in right.splitlines() if x.strip()]


def save_attempt(report_date, evidence, items, profile, errors):
    paths = paths_for(report_date, profile)
    run_dir = Path(profile['output_root']).expanduser() / '.runs' / report_date / uuid4().hex
    path = run_dir / 'attempt.json'
    state = {'version': 1, 'report_date': report_date, 'report_type': 'daily',
             'content_hash': '', 'evidence_hash': canonical_json_hash(evidence),
             'work_items_hash': canonical_json_hash(items), 'send_state': 'validation_failed',
             'sent_hashes': [], 'validation_errors': errors,
             'validated_at': datetime.now(timezone.utc).isoformat()}
    atomic_write_batch({path: json_bytes(state), run_dir / 'evidence.json': json_bytes(redact_value(evidence)),
                        run_dir / 'items.json': json_bytes(redact_value(items))})
    previous = {}
    try:
        snapshot = load_daily(report_date, profile)
        if snapshot:
            previous = {'run_state': str(paths['state'])}
            daily_path = paths['state'].parent / f'codex-daily-submit-{report_date}.md'
            if daily_path.exists():
                previous['daily'] = str(daily_path)
    except (ValueError, OSError):
        pass
    return 2, {'status': 'validation_failed', 'output_files': {'attempt': str(path), 'run_state': str(path)},
               'previous_valid_report': previous, 'send_ready': False, 'content_hash': '', 'validation_errors': errors}


def finalize_daily(report_date, evidence, work_items, profile, *, mode='finalize', revision=None):
    # Same lock order for all writers: month, then date. The sender takes only date.
    paths = paths_for(report_date, profile)
    month_lock = paths['state'].parent / 'month-transaction'
    with locked_run_state(month_lock), locked_run_state(paths['state']):
        try:
            return _finalize_daily(report_date, evidence, work_items, profile, mode, revision)
        except (DailyRevisionError, ValueError, TypeError, KeyError) as exc:
            return save_attempt(report_date, evidence, work_items, profile,
                [{'code': 'invalid_daily_input', 'message': str(exc)}])
        except OSError as exc:
            errors = [{'code': 'daily_persistence_failed', 'message': str(exc)}]
            try:
                return save_attempt(report_date, evidence, work_items, profile, errors)
            except OSError:
                return 2, {'status': 'persistence_failed', 'output_files': {}, 'send_ready': False,
                           'content_hash': '', 'validation_errors': errors}


def _finalize_daily(report_date, evidence, work_items, profile, mode, revision):
    paths = paths_for(report_date, profile)
    snapshot = load_daily(report_date, profile)
    old_evidence, previous, old_state, ledger = snapshot or (None, None, {}, None)
    if mode == 'revise':
        if not snapshot:
            raise DailyRevisionError('Create a validated daily snapshot before revising it')
        evidence, work_items, ledger = prepare_revision(report_date, old_evidence, previous, ledger, revision)
    else:
        if not isinstance(evidence, dict) or not isinstance(work_items, dict):
            raise DailyRevisionError('Evidence and WorkItems must be JSON objects')
        if evidence.get('report_date') != report_date or work_items.get('report_date') != report_date:
            raise DailyRevisionError('Evidence and WorkItems must match the requested date')
        if not isinstance(evidence.get('records'), list):
            raise DailyRevisionError('EvidenceBundle.records must be a list')
        input_errors = validate_evidence_schema(evidence, report_date, evidence['records'])
        if input_errors:
            return save_attempt(report_date, evidence, work_items, profile, input_errors)
        # A generated candidate is not authorization to delete saved work.
        # Both record and finalize preserve the validated day; sourced removal
        # revisions are applied afterwards and remain authoritative on reruns.
        if old_evidence:
            evidence = merge_evidence(old_evidence, evidence)
        work_items = stable_items(work_items, previous, append=True)
        evidence, work_items = apply_ledger(evidence, work_items, ledger)
    errors = validate_bundles('daily', report_date, evidence, work_items, profile)
    if not work_items.get('items'):
        errors = [e for e in errors if e['code'] not in {'no_work_items','daily_item_count'}]
    if errors:
        return save_attempt(report_date, evidence, work_items, profile, errors)
    shown = displayed_items(work_items)
    no_report = not shown
    daily_text = render_daily(report_date, work_items) if shown else ''
    if shown:
        errors.extend(validate_submitted_text(daily_text, profile, 'daily'))
    if errors:
        return save_attempt(report_date, evidence, work_items, profile, errors)
    records = evidence['records']
    exclusions = Counter(r['excluded_reason'] for r in records if r.get('excluded_reason'))
    candidate_count = sum(not r.get('excluded_reason') and r.get('candidate_reason') != 'unclassified' for r in records)
    content_hash = hashlib.sha256(daily_text.encode()).hexdigest() if shown else ''
    send_enabled = bool((profile.get('send_policy') or {}).get('daily', True))
    state = {'version': 1, 'validation_policy_version': 2, 'report_date': report_date, 'report_type': 'daily',
        'content_hash': content_hash, 'evidence_hash': canonical_json_hash(evidence),
        'work_items_hash': canonical_json_hash(work_items), 'validation_errors': [],
        'validated_at': datetime.now(timezone.utc).isoformat(),
        'send_state': ('no_reportable_items' if no_report else 'sent' if content_hash in old_state.get('sent_hashes', [])
                       else 'pending' if send_enabled else 'disabled'),
        'displayed_item_ids': [item['id'] for item in shown],
        'metrics': {'record_count': len(records), 'candidate_count': candidate_count,
                    'model_context_count': len(evidence.get('model_context', [])),
                    'excluded_count': sum(exclusions.values()), 'exclusion_reasons': dict(exclusions),
                    'validated_item_count': len(work_items['items']),
                    'collection': evidence.get('collection_metrics', {})}}
    if ledger is not None:
        state['revisions_hash'] = canonical_json_hash(ledger)
    preserve_delivery_history(state, old_state, preserve_sent_at=content_hash in old_state.get('sent_hashes', []))
    if all(state.get(k) == old_state.get(k) for k in ('content_hash','evidence_hash','work_items_hash','revisions_hash')):
        state['validated_at'] = old_state.get('validated_at', state['validated_at'])
    daily_path = paths['state'].parent / f'codex-daily-submit-{report_date}.md'
    monthly_path = paths['state'].parent / f'codex-daily-submit-{report_date[:7]}.md'
    monthly = monthly_path.read_text() if monthly_path.exists() else ''
    files = {paths['evidence']: json_bytes(evidence), paths['items']: json_bytes(work_items), paths['state']: json_bytes(state)}
    if ledger is not None:
        files[paths['revisions']] = json_bytes(ledger)
    if shown:
        files[daily_path] = daily_text.encode()
        updated = replace_or_append_daily(monthly, report_date, daily_text)
        files[monthly_path] = (monthly if same_markdown_content(monthly, updated) else updated).encode()
    elif monthly_path.exists():
        files[monthly_path] = remove_daily(monthly, report_date).encode()
    atomic_write_batch(files, delete_paths=[daily_path] if no_report else ())
    outputs = {key: str(paths[key]) for key in ('evidence','items','state')}
    outputs['work_items'] = outputs.pop('items')
    outputs['run_state'] = outputs.pop('state')
    if shown:
        outputs.update(daily=str(daily_path), monthly_root=str(monthly_path))
    if ledger is not None:
        outputs['revisions'] = str(paths['revisions'])
    return 0, {'status': 'no_reportable_items' if no_report else 'ok', 'output_files': outputs,
               'send_ready': mode == 'finalize' and bool(shown) and send_enabled and content_hash not in state['sent_hashes'],
               'content_hash': content_hash, 'validation_errors': [], 'displayed_item_ids': state['displayed_item_ids']}
