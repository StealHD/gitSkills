"""Daily snapshots and sourced, idempotent item revisions."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from pathlib import Path

from .common import canonical_json_hash, load_json, redact_value
from .contracts import validate_bundles, grounding_value_present


class DailyRevisionError(ValueError):
    pass


def paths_for(report_date, profile):
    if date.fromisoformat(report_date).isoformat() != report_date:
        raise DailyRevisionError('report_date must use YYYY-MM-DD')
    root = Path(profile['output_root']).expanduser() / report_date[:7]
    return {name: root / f'codex-{stem}-{report_date}.json' for name, stem in (
        ('evidence', 'evidence'), ('items', 'work-items'), ('state', 'run-state'), ('revisions', 'daily-overrides'))}


def displayed_items(bundle):
    items = bundle.get('items', [])
    by_id = {item['id']: item for item in items}
    order = bundle.get('display_order', [item['id'] for item in items])
    return [by_id[key] for key in order if key in by_id and not by_id[key].get('daily_hidden')][:4]


def load_daily(report_date, profile):
    from .aggregation import validate_daily_run_state
    paths = paths_for(report_date, profile)
    present = [paths[k].exists() for k in ('evidence', 'items', 'state')]
    if not any(present):
        if paths['revisions'].exists():
            raise DailyRevisionError('Revision ledger exists without a validated daily snapshot')
        return None
    if not all(present):
        raise DailyRevisionError('Incomplete daily snapshot; recover the missing files before editing')
    evidence, items, state = [load_json(paths[k]) for k in ('evidence', 'items', 'state')]
    if not all(isinstance(value, dict) for value in (evidence, items, state)):
        raise DailyRevisionError('Daily snapshot files must be JSON objects')
    errors = validate_daily_run_state(report_date, state, evidence, items)
    ledger = load_json(paths['revisions']) if paths['revisions'].exists() else None
    if (ledger is not None or state.get('revisions_hash')) and state.get('revisions_hash') != canonical_json_hash(ledger):
        errors.append({'code': 'revision_hash_mismatch'})
    # Historical prose is checked when generating the new deliverable, not migrated on read.
    errors.extend(e for e in validate_bundles('source' if state.get('validation_policy_version') == 2 else 'existing', report_date, evidence, items, profile)
                  if e['code'] not in {'no_work_items', 'daily_item_count'})
    if errors:
        raise DailyRevisionError('Invalid existing snapshot: ' + ', '.join(e['code'] for e in errors))
    return evidence, items, state, ledger


def merge_evidence(existing, incoming):
    merged = deepcopy(incoming)
    records = {record['id']: deepcopy(record) for record in existing.get('records', [])}
    for record in incoming.get('records', []):
        current = deepcopy(record)
        old = records.get(record['id'])
        if old:
            # An older/incomplete collector must not erase the original user
            # request or completed tool output for a retained item.
            for field in ('user_text', 'result_text'):
                current[field] = current.get(field) or old.get(field, '')
            if old.get('user_text') and not record.get('user_text'):
                for field in ('source_kind', 'candidate_reason'):
                    current[field] = old[field]
            calls = {call['call_id']: deepcopy(call) for call in old.get('tool_evidence', [])}
            for call in current.get('tool_evidence', []):
                previous_call = calls.get(call['call_id'], {})
                calls[call['call_id']] = {key: value or previous_call.get(key, '') for key, value in call.items()}
            current['tool_evidence'] = list(calls.values())
        records[record['id']] = current
    merged['records'] = sorted(records.values(), key=lambda r: (r['occurred_at'], r['id']))
    return merged


def stable_items(incoming, previous=None, append=False):
    result = deepcopy(incoming)
    if not isinstance(result, dict) or not isinstance(result.get('items'), list):
        return result
    previous = previous or {'items': []}
    by_key = {(item['object_key'].strip().lower(), item['objective'].strip().lower()): item for item in previous['items']}
    existing = {item['id']: item for item in previous['items']}
    fresh = []
    for item in result['items']:
        if not isinstance(item, dict):
            fresh.append(item)
            continue
        key = (str(item.get('object_key', '')).strip().lower(), str(item.get('objective', '')).strip().lower())
        old = existing.get(item.get('id')) or by_key.get(key)
        if old:
            item['id'] = old['id']
            fact_fields = ('object_key', 'category', 'objective', 'key_facts', 'outcome', 'status', 'follow_up', 'supporting_actions')
            if all(item.get(field) == old.get(field) for field in fact_fields):
                for field in ('submitted_text', 'weekly_text'):
                    if field in old:
                        item[field] = old[field]
            for field in ('daily_text', 'daily_hidden'):
                if field in old:
                    item[field] = old[field]
        fresh.append(item)
    if append:
        fresh_ids = [item.get('id') for item in fresh if isinstance(item, dict)]
        if len(fresh_ids) != len(fresh) or len(set(fresh_ids)) != len(fresh_ids):
            raise DailyRevisionError('Incoming WorkItems must be objects with unique stable ids')
        positions = {item['id']: i for i, item in enumerate(previous['items'])}
        combined = deepcopy(previous['items'])
        for item in fresh:
            if item['id'] in positions:
                combined[positions[item['id']]] = item
            else:
                positions[item['id']] = len(combined)
                combined.append(item)
        fresh = combined
    result['items'] = fresh
    ids = [item.get('id') for item in fresh if isinstance(item, dict)]
    old_order = previous.get('display_order', [item['id'] for item in previous['items']])
    order = old_order + result.get('display_order', []) if append and previous['items'] else result.get('display_order', old_order)
    result['display_order'] = list(dict.fromkeys([key for key in order if key in ids] + ids))
    return result


def apply_ledger(evidence, bundle, ledger):
    if not ledger:
        return evidence, bundle
    evidence, bundle = deepcopy(evidence), deepcopy(bundle)
    records = {r['id']: r for r in evidence['records']}
    items = {item['id']: item for item in bundle['items']}
    order = list(bundle.get('display_order', items))
    for revision in ledger['revisions']:
        for source in revision.get('sources', []):
            if source['id'] in records and records[source['id']].get('user_text') != source['user_text']:
                raise DailyRevisionError('Conflicting revision evidence source')
            records.setdefault(source['id'], dict(source, cwd='', result_text='', tool_evidence=[],
                source_kind='explicit_user_revision', candidate_reason='explicit_marker', excluded_reason=''))
        for audit in revision['audit']:
            target = audit['target_id']
            if audit['after'] is None:
                items.pop(target, None)
            elif target not in items:
                items[target] = deepcopy(audit['after'])
            else:
                for field, value in audit['changes'].items():
                    items[target][field] = deepcopy(value)
            if target not in order:
                order.append(target)
    bundle['items'] = [items[key] for key in order if key in items]
    bundle['display_order'] = [key for key in order if key in items]
    evidence['records'] = sorted(records.values(), key=lambda r: (r['occurred_at'], r['id']))
    return evidence, bundle


def prepare_revision(report_date, evidence, bundle, existing, incoming):
    incoming = redact_value(incoming)
    if not isinstance(incoming, dict) or incoming.get('version') != 1 or incoming.get('report_date') != report_date or incoming.get('source_kind') != 'explicit_user_revision':
        raise DailyRevisionError('Revision requires version=1, report_date and source_kind=explicit_user_revision')
    revision_id = incoming.get('id')
    if not isinstance(revision_id, str) or not revision_id.strip():
        raise DailyRevisionError('Revision requires a stable id')
    ledger = deepcopy(existing or {'version': 1, 'report_date': report_date, 'revisions': []})
    request_hash = canonical_json_hash(incoming)
    for saved in ledger['revisions']:
        if saved['id'] == revision_id:
            if saved['request_hash'] != request_hash:
                raise DailyRevisionError('Revision id already exists with different content')
            return evidence, bundle, ledger
    sources = incoming.get('sources', [])
    if not isinstance(sources, list):
        raise DailyRevisionError('sources must be a list')
    source_map = {}
    for source in sources:
        if not isinstance(source, dict) or any(not isinstance(source.get(k), str) or not source[k].strip() for k in ('id','thread_id','turn_id','occurred_at','user_text')):
            raise DailyRevisionError('Revision sources require id/thread_id/turn_id/occurred_at/user_text')
        if source['id'] != source['thread_id'] + ':' + source['turn_id'] or source['id'] in source_map:
            raise DailyRevisionError('Invalid or duplicate source identity')
        if datetime.fromisoformat(source['occurred_at'].replace('Z','+00:00')).tzinfo is None:
            raise DailyRevisionError('Source timestamp must have timezone')
        source_map[source['id']] = source
    current = {item['id']: deepcopy(item) for item in bundle['items']}
    audits = []
    operations = incoming.get('items')
    if not isinstance(operations, list) or not operations:
        raise DailyRevisionError('Revision requires a non-empty items list')
    for op in operations:
        if not isinstance(op, dict) or op.get('kind') not in {'presentation', 'fact'}:
            raise DailyRevisionError('Each operation requires kind=presentation or fact')
        source_ref = op.get('source_ref')
        if source_ref not in source_map:
            raise DailyRevisionError('Each revision requires an explicit user source')
        action = op.get('operation')
        target_id = op.get('target_id')
        before = deepcopy(current.get(target_id))
        if action not in {'add','replace','remove'} or (action != 'add' and before is None):
            raise DailyRevisionError('Unknown action or target_id; resolve displayed item number to its id first')
        if op['kind'] == 'presentation':
            if action == 'add':
                raise DailyRevisionError('Adding work requires a fact revision and a complete WorkItem')
            changes = {'daily_hidden': True} if action == 'remove' else {'daily_text': op.get('text'), 'daily_hidden': False}
            after = dict(before, **changes)
        elif action == 'remove':
            changes, after = {}, None
        else:
            changes = op.get('fields') if action == 'replace' else op.get('item')
            allowed = {'object_key','category','objective','evidence_refs','key_facts','supporting_actions','outcome','status','follow_up','submitted_text','weekly_text','weekly_group','priority_signals'}
            if not isinstance(changes, dict) or not changes or set(changes) - (allowed | ({'id'} if action == 'add' else set())):
                raise DailyRevisionError('Fact revision must contain supported WorkItem fields')
            changes = deepcopy(changes)
            if action == 'add':
                target_id = changes.get('id')
                if not target_id or target_id in current:
                    raise DailyRevisionError('Added item requires a unique id')
                after = dict(changes)
            else:
                after = dict(before, **changes)
                if any(field in changes for field in ('object_key', 'key_facts', 'outcome', 'status', 'objective')):
                    if 'submitted_text' not in changes:
                        raise DailyRevisionError('Fact corrections require updated submitted_text for the corrected result')
                    changes['daily_text'] = changes['submitted_text']
                    changes.setdefault('weekly_text', changes['submitted_text'])
                    changes['fact_revision_at'] = source_map[source_ref]['occurred_at']
                    changes['fact_revision_ref'] = source_ref
                    after.update(changes)
                if after['object_key'] != before['object_key']:
                    from .weekly import OBJECT_CORRECTION_PATTERN
                    source_text = source_map[source_ref]['user_text']
                    if not OBJECT_CORRECTION_PATTERN.search(source_text) or not grounding_value_present(after['object_key'], source_text):
                        raise DailyRevisionError('Object change requires a user source explicitly stating the correction')
                    if 'key_facts' not in changes or 'evidence_refs' not in changes:
                        raise DailyRevisionError('Object change requires replacement facts and evidence; old metrics cannot be inherited')
                    old_metric_facts = [f for f in before['key_facts'] if f.get('name') not in {'instance','server','database','schema','table','object'}]
                    if any(f in old_metric_facts for f in after['key_facts']):
                        raise DailyRevisionError('Object change cannot reuse old-object metric evidence')
        audits.append({'target_id': target_id, 'kind': op['kind'], 'operation': action,
            'source_ref': source_ref, 'before': before, 'after': after, 'changes': changes})
        if after is None:
            current.pop(target_id, None)
        else:
            current[target_id] = after
    ledger['revisions'].append({'id': revision_id, 'request_hash': request_hash, 'sources': sources, 'audit': audits})
    evidence, bundle = apply_ledger(evidence, bundle, ledger)
    return evidence, bundle, ledger
