"""Regression coverage for explicit entries surviving desktop collection and reruns."""
import json
import sqlite3
import unittest
from copy import deepcopy
from pathlib import Path

import test_daily_report as base
from test_daily_report import (
    DAY, fixture, revision, finalize_daily,
    load_daily, paths_for, aggregate_report,
)
from reporting.weekly import validate_weekly_item_detail, is_cross_week_plan
from reporting.contracts import resolved_outcome_grounded


class DesktopMessageTests(unittest.TestCase):
    setUp = base.DailyTests.setUp
    session = base.CollectionTests.session
    collect = base.CollectionTests.collect

    def desktop(self, mirrors=True):
        text = '已完成 monitor-agent WAL 恢复。' + '故障分析及验证。' * 100 + '\n整理写入日报'
        rows = [
            {'type': 'session_meta', 'payload': {'id': 'desktop', 'cwd': '/personal', 'source': 'vscode'}},
            {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'turn'}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [
                {'type': 'input_text', 'text': '<recommended_plugins>配置说明</recommended_plugins>'}]}},
            {'type': 'turn_context', 'payload': {'turn_id': 'turn', 'cwd': '/personal'}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [
                {'type': 'input_text', 'text': text}, {'type': 'input_image', 'image_url': 'data:image/png;base64,ignore'}]}},
        ]
        if mirrors:
            rows.extend([
                {'type': 'event_msg', 'payload': {'type': 'item_completed', 'turn_id': 'turn', 'item': {
                    'type': 'UserMessage', 'id': 'message', 'content': [{'type': 'text', 'text': text}]}}},
                {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': text}},
            ])
        return text, ''.join(json.dumps(dict(timestamp=DAY+'T12:00:00+08:00', **row), ensure_ascii=False)+'\n' for row in rows)

    def test_desktop_explicit_tail_is_collected_once_without_context(self):
        text, raw = self.desktop()
        home, _ = self.session(raw)
        result = self.collect(home)
        self.assertEqual(result['records'][0]['user_text'], text)
        self.assertEqual(result['records'][0]['source_kind'], 'explicit_record')
        self.assertEqual(result['records'][0]['excluded_reason'], '')
        self.assertEqual(len(result['model_context']), 1)
        self.assertEqual(self.collect(home)['records'], result['records'])

    def test_completed_user_event_works_without_response_message(self):
        text, raw = self.desktop()
        raw = '\n'.join(line for line in raw.splitlines() if json.loads(line)['type'] != 'response_item'
                        and json.loads(line)['payload'].get('type') != 'user_message')+'\n'
        home, _ = self.session(raw)
        self.assertEqual(self.collect(home)['records'][0]['user_text'], text)

    def test_response_user_message_works_without_mirrors(self):
        text, raw = self.desktop(mirrors=False)
        home, _ = self.session(raw)
        self.assertEqual(self.collect(home)['records'][0]['user_text'], text)

    def test_explicit_instruction_before_short_completion_followup_beats_maintenance_filter(self):
        text, raw = self.desktop(mirrors=False)
        self.profile['report_maintenance_patterns'] = ['整理.*日报']
        raw += json.dumps({'timestamp': DAY+'T12:01:00+08:00', 'type': 'response_item',
            'payload': {'type': 'message', 'role': 'user', 'content': [
                {'type': 'input_text', 'text': '我做完了啊'}]}}, ensure_ascii=False)+'\n'
        home, _ = self.session(raw)
        result = self.collect(home)
        self.assertEqual(result['records'][0]['user_text'], text+'\n我做完了啊')
        self.assertEqual(result['records'][0]['source_kind'], 'explicit_record')
        self.assertEqual(result['records'][0]['excluded_reason'], '')

    def test_negative_or_proposed_recording_is_not_explicit_authorization(self):
        _, raw = self.desktop(mirrors=False)
        home, path = self.session(raw)
        for instruction in ['不要整理写入日报', '后续可以整理写入日报', '讨论如何整理写入日报']:
            path.write_text(raw.replace('整理写入日报', instruction))
            self.assertNotEqual(self.collect(home)['records'][0]['source_kind'], 'explicit_record')

    def test_mirror_arriving_after_cache_resume_does_not_duplicate(self):
        text, raw = self.desktop(mirrors=False)
        home, path = self.session(raw)
        self.collect(home)
        with path.open('a') as handle:
            handle.write(json.dumps({'timestamp': DAY+'T12:01:00+08:00', 'type': 'event_msg',
                'payload': {'type': 'user_message', 'message': text}}, ensure_ascii=False)+'\n')
        self.assertEqual(self.collect(home)['records'][0]['user_text'], text)

    def test_parser_upgrade_rebuilds_unchanged_old_cache(self):
        text, raw = self.desktop()
        home, _ = self.session(raw)
        self.collect(home)
        cache = Path(self.profile['output_root'])/'.cache/report-index.sqlite3'
        with sqlite3.connect(cache) as db:
            db.execute('UPDATE meta SET version=2')
            state = json.loads(db.execute('SELECT state FROM files').fetchone()[0])
            state['turns']['turn']['user_text'] = ''
            db.execute('UPDATE files SET state=?', (json.dumps(state),))
        result = self.collect(home)
        self.assertEqual(result['records'][0]['user_text'], text)
        self.assertEqual(result['collection_metrics']['rebuilds'], 1)


class RetentionTests(unittest.TestCase):
    setUp = base.DailyTests.setUp
    save = base.DailyTests.save

    def record_explicit(self):
        evidence, items = fixture(obj='db.audit', identifier='audit')
        evidence['records'][0].update(source_kind='explicit_record', candidate_reason='explicit_marker')
        evidence['records'][0]['user_text'] += '，整理写入日报'
        rc, result = finalize_daily(DAY, evidence, items, self.profile, mode='record')
        self.assertEqual(rc, 0, result)
        self.assertFalse(result['send_ready'])
        return evidence, items

    def test_record_then_incomplete_finalize_preserves_item_evidence_order_and_weekly(self):
        self.save()
        evidence, items = self.record_explicit()
        rc, result = finalize_daily(DAY, self.ev, self.items, self.profile)
        self.assertEqual(rc, 0, result)
        snapshot = load_daily(DAY, self.profile)
        self.assertEqual(snapshot[1]['display_order'], ['orders', 'audit'])
        self.assertEqual(snapshot[1]['items'][1], items['items'][0])
        self.assertIn(evidence['records'][0], snapshot[0]['records'])
        self.assertIn('db.audit', Path(result['output_files']['daily']).read_text())
        rc, weekly = aggregate_report('weekly', DAY, self.profile)
        self.assertEqual(rc, 0, weekly)
        self.assertIn('db.audit', Path(weekly['output_files']['weekly']).read_text())

    def test_empty_rerun_does_not_erase_saved_report(self):
        self.save()
        rc, result = finalize_daily(DAY, dict(self.ev, records=[]), dict(self.items, items=[]), self.profile)
        self.assertEqual(rc, 0, result)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(load_daily(DAY, self.profile)[1]['items'], self.items['items'])

    def test_rerun_preserves_existing_order_even_if_candidate_reorders(self):
        self.save()
        ev, items = self.record_explicit()
        incoming = dict(self.items, items=items['items']+self.items['items'], display_order=['audit', 'orders'])
        rc, result = finalize_daily(DAY, dict(self.ev, records=ev['records']+self.ev['records']), incoming, self.profile)
        self.assertEqual(rc, 0, result)
        self.assertEqual(load_daily(DAY, self.profile)[1]['display_order'], ['orders', 'audit'])

    def test_merge_does_not_hide_duplicate_evidence_or_items(self):
        self.save()
        before = paths_for(DAY, self.profile)['state'].read_bytes()
        for ev, items in [(dict(self.ev, records=self.ev['records']*2), self.items),
                          (self.ev, dict(self.items, items=self.items['items']*2))]:
            rc, result = finalize_daily(DAY, ev, items, self.profile)
            self.assertEqual(rc, 2, result)
            self.assertEqual(paths_for(DAY, self.profile)['state'].read_bytes(), before)

    def test_old_collector_cannot_blank_saved_user_request_and_tool_result(self):
        self.ev['records'][0]['source_kind'] = 'explicit_record'
        self.ev['records'][0]['candidate_reason'] = 'explicit_marker'
        self.ev['records'][0]['tool_evidence'] = [dict(tool_name='sql', call_id='call', input_text='select 1', output_text='1')]
        self.save()
        stale = deepcopy(self.ev)
        stale['records'][0].update(user_text='', result_text='', source_kind='session', candidate_reason='work_cwd')
        stale['records'][0]['tool_evidence'][0]['output_text'] = ''
        rc, result = finalize_daily(DAY, stale, self.items, self.profile)
        self.assertEqual(rc, 0, result)
        self.assertEqual(load_daily(DAY, self.profile)[0]['records'][0], self.ev['records'][0])

    def test_explicit_fact_removal_still_survives_stale_rerun(self):
        self.save()
        edit = revision()
        edit['sources'][0]['user_text'] = '删除日报里误记的 db.orders 工作'
        edit['items'] = [dict(operation='remove', kind='fact', target_id='orders', source_ref='editor:turn')]
        rc, result = finalize_daily(DAY, {}, {}, self.profile, mode='revise', revision=edit)
        self.assertEqual(rc, 0, result)
        rc, result = finalize_daily(DAY, self.ev, self.items, self.profile)
        self.assertEqual(rc, 0, result)
        self.assertEqual(result['status'], 'no_reportable_items')
        self.assertFalse(load_daily(DAY, self.profile)[1]['items'])

    def test_omitted_retained_item_with_new_scope_conflict_fails_without_overwrite(self):
        self.save()
        self.record_explicit()
        before = {p: p.read_bytes() for p in paths_for(DAY, self.profile).values() if p.exists()}
        directive = deepcopy(self.ev['records'][0])
        directive.update(id='editor:scope', thread_id='editor', turn_id='scope',
            occurred_at=DAY+'T18:00:00+08:00', user_text='只保留 db.orders', result_text='',
            candidate_reason='scope_override')
        rc, result = finalize_daily(DAY, dict(self.ev, records=self.ev['records']+[directive]), self.items, self.profile)
        self.assertEqual(rc, 2, result)
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_colloquial_completion_revision_survives_rerun(self):
        self.save()
        edit = revision()
        edit['sources'][0]['user_text'] = 'db.orders 处理完了'
        text = '已完成 db.orders 慢 SQL 处理，问题已解决。'
        edit['items'] = [dict(operation='replace', kind='fact', target_id='orders', source_ref='editor:turn',
            fields=dict(status='resolved', outcome='已处理完成', follow_up='', submitted_text=text,
                        evidence_refs=['orders:turn', 'editor:turn']))]
        rc, result = finalize_daily(DAY, {}, {}, self.profile, mode='revise', revision=edit)
        self.assertEqual(rc, 0, result)
        rc, result = finalize_daily(DAY, self.ev, self.items, self.profile)
        self.assertEqual(rc, 0, result)
        self.assertEqual(load_daily(DAY, self.profile)[1]['items'][0]['status'], 'resolved')


class DownstreamValidationTests(unittest.TestCase):
    def test_concrete_session_scope_is_not_negated_by_generic_conclusion(self):
        text = '已完成 SQL Server 长事务锁风险排查与处理，定位 SPID 42 存在未提交事务，相关问题已处理完成。'
        self.assertEqual(validate_weekly_item_detail({'submitted_text': text}), [])

    def test_verified_key_cleanup_is_a_completed_action(self):
        text = '对 service-cache 命名空间中的模板 key 做了核对与清理，先按 TYPE/GET 核验对象类型，再 DEL 删除并用 EXISTS 复核，最终确认 key 已清除。'
        self.assertEqual(validate_weekly_item_detail({'submitted_text': text}), [])

    def test_generic_scope_and_unfinished_work_remain_rejected(self):
        self.assertTrue(validate_weekly_item_detail({'submitted_text': '已完成数据库相关问题分析，确认存在异常并形成详细处置方案。'}))
        for source in ['尚未处理完成', '没有处理完了', '待处理完成']:
            self.assertFalse(resolved_outcome_grounded('问题已处理完成', source))
        self.assertTrue(resolved_outcome_grounded('问题已处理完成', 'sqlserver 处理完了'))

    def test_unscheduled_followup_is_not_automatically_next_week_work(self):
        self.assertFalse(is_cross_week_plan('验证 monitor-agent 下一轮 checkpoint 正常，确认稳定后清理备份并复核空间释放结果。'))
        self.assertFalse(is_cross_week_plan('对高危与可行动项按实例建立处置清单并安排复查窗口，补齐处置结果。'))
        self.assertTrue(is_cross_week_plan('下周验证 monitor-agent 下一轮 checkpoint 正常，复核空间释放结果。'))


if __name__ == '__main__':
    unittest.main()
