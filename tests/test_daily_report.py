"""Public regression fixtures; no private sessions or generated reports are packaged."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

SKILL_ROOT = Path(os.environ.get('DAILY_REPORT_SKILL_ROOT', Path(__file__).resolve().parents[1] / 'skills/codex-daily-report'))
sys.path.insert(0, str(SKILL_ROOT / 'scripts'))
from reporting.common import canonical_json_hash, atomic_write_batch
from reporting.contracts import validate_bundles, validate_submitted_text
from reporting.daily import load_daily, paths_for
from reporting.evidence import classify_record, collect_evidence, scan_session_file
from reporting.finalize import finalize_daily
from reporting.aggregation import load_validated_items, aggregate_report, import_legacy
from send_wecom_report import send_report, WeComSendError

DAY = '2026-09-04'
TZ = ZoneInfo('Asia/Shanghai')


def fixture(day=DAY, obj='db.orders', identifier='orders'):
    ref = identifier + ':turn'
    record = dict(id=ref, thread_id=identifier, turn_id='turn', occurred_at=day+'T12:00:00+08:00',
        cwd='/work/db', user_text=f'分析 {obj} 慢 SQL：select * from {obj}，扫描 120 行',
        result_text=f'已完成 {obj} 执行计划分析，确认缺少过滤索引，形成优化方案。',
        tool_evidence=[], source_kind='session', candidate_reason='work_cwd', excluded_reason='')
    text = f'已完成 {obj} 慢 SQL 执行计划分析，确认扫描 120 行及过滤索引缺失，形成索引优化方案。'
    item = dict(id=identifier, object_key=obj, category='slow_sql', objective='慢 SQL 分析',
        evidence_refs=[ref], key_facts=[dict(name='table', value=obj, source_ref=ref),
        dict(name='sql', value=f'select * from {obj}', source_ref=ref),dict(name='rows_examined',value='120',source_ref=ref)],
        supporting_actions=[], outcome='已完成执行计划分析，确认缺少过滤索引。', status='analysis_complete',follow_up='',
        submitted_text=text, weekly_text=text, weekly_group='performance_incident',priority_signals=['production_risk'])
    return dict(version=1,report_date=day,timezone='Asia/Shanghai',records=[record]), dict(version=1,report_date=day,items=[item])


def revision(target='orders', text='已完成 db.orders 执行计划分析，确认过滤索引缺失。', identifier='rev-1'):
    return dict(version=1,report_date=DAY,source_kind='explicit_user_revision',id=identifier,
        sources=[dict(id='editor:turn',thread_id='editor',turn_id='turn',occurred_at='2026-09-05T09:00:00+08:00',
                      user_text='日报第二条精简，保留已确认的分析结果。')],
        items=[dict(operation='replace',kind='presentation',target_id=target,source_ref='editor:turn',text=text)])


def concurrent_save(args):
    root, day, identifier = args
    ev, items = fixture(day, 'db.'+identifier, identifier)
    return finalize_daily(day,ev,items,{'output_root':root,'send_policy':{'daily':False}},mode='record')[0]


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = {'output_root':str(self.root / 'reports'), 'work_cwd_patterns':['/work/'],
                        'work_keywords':['DBA','SQL','数据库','巡检'], 'send_policy':{'daily':True},
                        'include_markers':['日报记录'], 'exclude_turn_patterns':['Notion|已记录到'],
                        'report_maintenance_patterns':['automation']}
        self.ev,self.items=fixture()

    def save(self):
        rc,result=finalize_daily(DAY,self.ev,self.items,self.profile)
        self.assertEqual(rc,0,result)
        return result

    def test_finalize_persists_complete_snapshot_and_policy(self):
        out=self.save();snapshot=load_daily(DAY,self.profile)
        self.assertEqual(snapshot[2]['validation_policy_version'],2)
        self.assertEqual(snapshot[2]['displayed_item_ids'],['orders'])
        self.assertTrue(Path(out['output_files']['evidence']).exists())

    def test_conditional_future_rejected_by_finalizer_and_sender_gate(self):
        bad='已完成 db.orders 权限核查，完成授予后可重试位点初始化'
        self.items['items'][0]['submitted_text']=bad
        rc,result=finalize_daily(DAY,self.ev,self.items,self.profile)
        self.assertEqual(rc,2)
        self.assertTrue(any(e.get('field')=='submitted_text' for e in result['validation_errors']))
        self.assertTrue(validate_submitted_text('1. '+bad, self.profile))

    def test_failure_keeps_all_validated_bytes_and_has_private_attempt(self):
        out=self.save();paths=paths_for(DAY,self.profile)
        before={p:p.read_bytes() for p in paths.values() if p.exists()}
        daily=Path(out['output_files']['daily']);before[daily]=daily.read_bytes()
        self.items['items'][0]['key_facts'][0]['value']='wrong.table'
        rc,result=finalize_daily(DAY,self.ev,self.items,self.profile)
        self.assertEqual(rc,2)
        self.assertIn('.runs',result['output_files']['attempt'])
        self.assertEqual(result['previous_valid_report']['daily'],str(daily))
        for p,content in before.items():self.assertEqual(p.read_bytes(),content)
        self.assertIsNotNone(load_daily(DAY,self.profile))

    def test_revision_is_local_idempotent_and_survives_regeneration(self):
        e2,i2=fixture(obj='db.payments',identifier='payments')
        self.ev['records']+=e2['records'];self.items['items']+=i2['items']
        self.save();before=deepcopy(load_daily(DAY,self.profile)[1]['items'][1])
        rev=revision()
        rc,out=finalize_daily(DAY,{}, {},self.profile,mode='revise',revision=rev)
        self.assertEqual(rc,0,out);self.assertFalse(out['send_ready'])
        snapshot=load_daily(DAY,self.profile)
        self.assertEqual(snapshot[1]['items'][1],before)
        self.assertEqual(snapshot[1]['items'][0]['submitted_text'],self.items['items'][0]['submitted_text'])
        self.assertEqual(snapshot[1]['items'][0]['daily_text'],rev['items'][0]['text'])
        ledger_path=paths_for(DAY,self.profile)['revisions'];data=ledger_path.read_bytes()
        self.assertEqual(finalize_daily(DAY,{}, {},self.profile,mode='revise',revision=rev)[0],0)
        self.assertEqual(ledger_path.read_bytes(),data)
        # A model-generated replacement id and prose cannot displace unchanged confirmed work.
        self.items['items'][0]['id']='model-new-id'
        self.items['items'][1]['submitted_text']='已完成 db.payments 分析。'
        self.assertEqual(finalize_daily(DAY,self.ev,self.items,self.profile)[0],0)
        snapshot=load_daily(DAY,self.profile)
        self.assertEqual(snapshot[1]['items'][0]['id'],'orders')
        self.assertEqual(snapshot[1]['items'][0]['daily_text'],rev['items'][0]['text'])
        self.assertEqual(snapshot[1]['items'][1],before)
        agg,errors=load_validated_items('monthly',date.fromisoformat(DAY),self.profile)
        self.assertFalse(errors,errors)
        self.assertEqual(agg[0]['submitted_text'],self.items['items'][0]['submitted_text'])

    def test_revision_wrong_metric_fails_without_losing_previous(self):
        self.save();p=paths_for(DAY,self.profile)['items'];old=p.read_bytes()
        rc,out=finalize_daily(DAY,{}, {},self.profile,mode='revise',revision=revision(text='已完成 db.orders 分析，确认扫描 999 行。'))
        self.assertEqual(rc,2,out);self.assertEqual(old,p.read_bytes())

    def test_revision_hash_tampering_is_rejected(self):
        self.save();finalize_daily(DAY,{}, {},self.profile,mode='revise',revision=revision())
        p=paths_for(DAY,self.profile)['revisions'];d=json.loads(p.read_text());d['revisions'][0]['id']='changed';p.write_text(json.dumps(d))
        with self.assertRaisesRegex(ValueError,'revision_hash'):load_daily(DAY,self.profile)
        self.assertTrue(load_validated_items('weekly',date.fromisoformat(DAY),self.profile)[1])

    def test_duplicate_revision_id_conflict_does_not_mutate(self):
        self.save();rev=revision();self.assertEqual(finalize_daily(DAY,{}, {},self.profile,mode='revise',revision=rev)[0],0)
        p=paths_for(DAY,self.profile)['revisions'];old=p.read_bytes();rev['items'][0]['text']='已完成 db.orders 分析。'
        self.assertEqual(finalize_daily(DAY,{}, {},self.profile,mode='revise',revision=rev)[0],2)
        self.assertEqual(p.read_bytes(),old)

    def test_record_preserves_other_items_and_never_sends(self):
        self.save();ev,items=fixture(obj='db.payments',identifier='payments')
        rc,out=finalize_daily(DAY,ev,items,self.profile,mode='record')
        self.assertEqual(rc,0,out);self.assertFalse(out['send_ready'])
        self.assertEqual([i['id'] for i in load_daily(DAY,self.profile)[1]['items']],['orders','payments'])

    def test_hide_only_daily_item_preserves_aggregation(self):
        self.save();rev=revision();rev['items'][0]['operation']='remove'
        rc,out=finalize_daily(DAY,{}, {},self.profile,mode='revise',revision=rev)
        self.assertEqual(rc,0,out);self.assertEqual(out['status'],'no_reportable_items')
        items,errors=load_validated_items('monthly',date.fromisoformat(DAY),self.profile)
        self.assertFalse(errors,errors);self.assertEqual(len(items),1)

    def test_fact_correction_uses_new_object_evidence_in_aggregation(self):
        self.save();rev=revision();source=rev['sources'][0]
        source['user_text']='更正为 test.orders，已完成 test.orders 的 select * from test.orders 执行计划分析，确认扫描 80 行，形成索引方案。'
        rev['items']=[dict(operation='replace',kind='fact',target_id='orders',source_ref=source['id'],fields=dict(
            object_key='test.orders',evidence_refs=[source['id']],
            key_facts=[dict(name='table',value='test.orders',source_ref=source['id']),dict(name='sql',value='select * from test.orders',source_ref=source['id']),dict(name='rows_examined',value='80',source_ref=source['id'])],
            submitted_text='已完成 test.orders 执行计划分析，确认扫描 80 行，形成索引方案。',weekly_text='已完成 test.orders 执行计划分析，确认扫描 80 行，形成索引方案。'))]
        rc,out=finalize_daily(DAY,{}, {},self.profile,mode='revise',revision=rev)
        self.assertEqual(rc,0,out)
        items,errors=load_validated_items('monthly',date.fromisoformat(DAY),self.profile)
        self.assertFalse(errors,errors);self.assertEqual(items[0]['object_key'],'test.orders')

    def test_object_correction_cannot_inherit_metrics(self):
        self.save();rev=revision();rev['sources'][0]['user_text']='对象更正为 test.orders'
        rev['items']=[dict(operation='replace',kind='fact',target_id='orders',source_ref='editor:turn',fields={'object_key':'test.orders'})]
        self.assertEqual(finalize_daily(DAY,{}, {},self.profile,mode='revise',revision=rev)[0],2)

    def test_new_daily_fact_correction_supersedes_old_weekly_prose(self):
        from reporting.weekly import apply_weekly_revision
        item=deepcopy(self.items['items'][0]);item['fact_revision_at']='2026-09-05T12:00:00+08:00';item['fact_revision_ref']='editor:fact'
        old=dict(version=1,report_week='2026-W36',source_kind='explicit_user_revision',
            sources=[dict(id='editor:old',thread_id='editor',turn_id='old',occurred_at='2026-09-04T12:00:00+08:00',user_text='精简周报这项分析')],
            items=[dict(operation='replace',target_id=item['id'],source_ref='editor:old',text='已完成 db.orders 分析。',group='performance_incident',priority_signals=['production_risk'])])
        result,_=apply_weekly_revision([item],old,'2026-W36')
        self.assertEqual(result[0]['_weekly_text'],item['weekly_text'])

    def test_invalid_snapshot_type_fails_without_traceback(self):
        self.save();paths_for(DAY,self.profile)['state'].write_text('[]')
        with self.assertRaises(ValueError):load_daily(DAY,self.profile)
        self.assertTrue(load_validated_items('monthly',date.fromisoformat(DAY),self.profile)[1])

    def test_atomic_failure_rolls_back_complete_daily_snapshot(self):
        self.save();paths=paths_for(DAY,self.profile);old={p:p.read_bytes() for p in paths.values() if p.exists()}
        import reporting.common as common
        original=common.os.replace;calls=0
        def fail_once(src,dst):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('injected transaction failure')
            return original(src,dst)
        self.items['items'][0]['outcome']='已完成分析并形成过滤索引方案。'
        with mock.patch.object(common.os,'replace',side_effect=fail_once):
            rc,result=finalize_daily(DAY,self.ev,self.items,self.profile)
            self.assertEqual(rc,2,result)
            self.assertEqual(result['validation_errors'][0]['code'],'daily_persistence_failed')
        for p,data in old.items():self.assertEqual(p.read_bytes(),data)

    def test_concurrent_dates_and_records_preserve_monthly_root(self):
        root=self.profile['output_root']
        tasks=[(root,DAY,'a'),(root,DAY,'b'),(root,'2026-09-03','c')]
        with ProcessPoolExecutor(max_workers=3) as pool:
            self.assertEqual(list(pool.map(concurrent_save,tasks)),[0,0,0])
        self.assertEqual(len(load_daily(DAY,self.profile)[1]['items']),2)
        text=(Path(root)/'2026-09/codex-daily-submit-2026-09.md').read_text()
        for obj in ('db.a','db.b','db.c'):self.assertIn(obj,text)

    def test_show_daily_monthly_prints_saved_bytes(self):
        out=self.save();profile_path=self.root/'profile.json';profile_path.write_text(json.dumps(self.profile))
        for kind in ('daily','monthly'):
            if kind=='monthly':
                rc,out=aggregate_report('monthly',DAY,self.profile);self.assertEqual(rc,0,out)
            cmd=[sys.executable,'-B',str(SKILL_ROOT/'scripts/reportctl.py'),'show','--type',kind,'--date',DAY,'--profile',str(profile_path)]
            result=subprocess.run(cmd,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr+result.stdout)
            self.assertEqual(result.stdout,Path(out['output_files'][kind]).read_bytes())

    def test_legacy_import_keeps_original_markdown(self):
        root=Path(self.profile['output_root'])/'2026-08';root.mkdir(parents=True)
        path=root/'codex-daily-submit-2026-08.md'
        path.write_text('# 2026-08 日报汇总\n\n## 2026-08-24（周一，工作日）\n\n1. 已完成订单数据库巡检，确认实例运行正常。\n')
        old=path.read_bytes();rc,out=import_legacy('2026-08',self.profile)
        self.assertEqual(rc,0,out);self.assertEqual(path.read_bytes(),old)

    def test_sent_hash_preserved_and_duplicate_send_skipped(self):
        out=self.save();content=Path(out['output_files']['daily']).read_text();calls=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return b'{"errcode":0}'
        def opener(*args,**kwargs):calls.append(1);return Response()
        kwargs=dict(content=content,webhook_url='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fake',msgtype='text',run_state_path=Path(out['output_files']['run_state']),content_hash=out['content_hash'],report_date=DAY,opener=opener)
        self.assertTrue(send_report(**kwargs)['sent'])
        self.assertFalse(self.save()['send_ready'])
        self.assertTrue(send_report(**kwargs)['skipped']);self.assertEqual(len(calls),1)

    def test_sender_refuses_future_actions_before_network(self):
        with self.assertRaisesRegex(WeComSendError, 'leadership_'):
            send_report(content='1. 已完成 db.orders 权限核查，完成授予后可重试位点初始化',
                webhook_url='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fake',msgtype='text',
                opener=mock.Mock(side_effect=AssertionError('must not send')))

    def test_separate_failed_attempts_share_notification_deduplication(self):
        self.save();self.items['items'][0]['submitted_text']='状态为进行中'
        self.items['items'][0]['key_facts'][0]['value']='wrong.table'
        attempts=[finalize_daily(DAY,self.ev,self.items,self.profile)[1]['output_files']['attempt'] for _ in range(2)]
        calls=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return b'{"errcode":0}'
        def opener(*args,**kwargs):calls.append(1);return Response()
        results=[send_report(content='日报校验失败，请检查本地运行日志。',
            webhook_url='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=fake',msgtype='text',
            run_state_path=Path(path),report_date=DAY,notification_kind='failure',opener=opener) for path in attempts]
        self.assertTrue(results[0]['sent']);self.assertTrue(results[1]['skipped']);self.assertEqual(len(calls),1)
        self.assertIsNotNone(load_daily(DAY,self.profile))


class CollectionTests(unittest.TestCase):
    setUp = DailyTests.setUp
    save = DailyTests.save
    # Collection fixtures are raw minimal JSONL, not production session copies.
    def raw(self, source='user', parent=None, guardian=False):
        meta=dict(id='session',cwd='/work/db',thread_source=source,source={'subagent':{'other':'guardian'}} if guardian else 'vscode')
        if parent:meta['parent_thread_id']=parent
        data=[{'type':'session_meta','payload':meta},
              {'type':'event_msg','payload':{'type':'task_started','turn_id':'turn'}},
              {'type':'event_msg','payload':{'type':'user_message','message':'分析数据库 SQL'}},
              {'type':'event_msg','payload':{'type':'task_complete','last_agent_message':'已完成 132 个数据库实例巡检，形成风险汇总报告。'}}]
        return ''.join(json.dumps(dict(timestamp=DAY+'T12:00:00+08:00',**row),ensure_ascii=False)+'\n' for row in data)

    def session(self, text):
        home=self.root/'home';folder=home/'sessions';folder.mkdir(parents=True,exist_ok=True)
        path=folder/'rollout.jsonl';path.write_text(text)
        return home,path

    def collect(self,home,**kwargs):return collect_evidence(DAY,'Asia/Shanghai',home,self.profile,**kwargs)

    def test_guardian_metadata_skips_transcript_and_has_no_candidates(self):
        home,path=self.session(self.raw('guardian_review',guardian=True)+'not valid JSON\n')
        result=self.collect(home)
        self.assertEqual(result['records'],[]);self.assertEqual(result['collection_metrics']['guardian_files'],1)
        self.assertLess(result['collection_metrics']['read_bytes'],path.stat().st_size)

    def test_automation_delivery_is_candidate_and_scheduling_only_is_not(self):
        home,path=self.session(self.raw('automation'))
        result=self.collect(home);self.assertEqual(len(result['model_context']),1)
        self.assertEqual(result['records'][0]['source_kind'],'automation')
        path.write_text(self.raw('automation').replace('已完成 132 个数据库实例巡检，形成风险汇总报告。','启动巡检，等待结果。'))
        self.assertEqual(self.collect(home)['model_context'],[])

    def test_structured_dbc_risk_summary_is_a_deliverable_without_keyword_report(self):
        raw=self.raw('automation').replace('已完成 132 个数据库实例巡检，形成风险汇总报告。',
            'DBC 2026-09-04 | 关注 3 实例/3 次 | actionable 4（未展 1） | 批次 1/1 done 失败 0\\nCRITICAL pmm3:mysql:database-a | 慢 SQL 21 条')
        home,path=self.session(raw)
        self.assertEqual(len(self.collect(home)['model_context']),1)
        path.write_text(raw.replace('分析数据库 SQL','使用 $codex-daily-report 生成日报'))
        result=self.collect(home)
        self.assertEqual(result['records'][0]['excluded_reason'],'report_generation')
        self.assertFalse(result['model_context'])

    def test_keyword_boundary_and_storage_postscript(self):
        r=self.ev['records'][0];r=dict(r,occurred_at=datetime.fromisoformat(r['occurred_at']))
        r['result_text']+='\n已记录到 Notion 工作台账。'
        self.assertEqual(classify_record(r,self.profile)['excluded_reason'],'')
        r['user_text']='查看 /example/adba/path';r['result_text']='已完成查看'
        self.assertEqual(classify_record(r,self.profile)['candidate_reason'],'unclassified')
        r['cwd']='/personal';r['user_text']='日报记录：完成数据库分析'
        self.assertEqual(classify_record(r,self.profile)['candidate_reason'],'explicit_marker')
        r['cwd']='/work/db';r['user_text']='处理这个问题';r['result_text']='已完成数据库巡检，已记录到 Notion 台账。'
        self.assertEqual(classify_record(r,self.profile)['candidate_reason'],'work_cwd')
        self.assertEqual(classify_record(r,self.profile)['excluded_reason'],'')

    def test_child_is_associated_not_independent_candidate(self):
        home,path=self.session(self.raw())
        (path.parent/'child.jsonl').write_text(self.raw(parent='session').replace('"id": "session"','"id": "child"'))
        result=self.collect(home)
        self.assertEqual(len(result['model_context']),1)
        primary=next(r for r in result['records'] if r['thread_id']=='session')
        self.assertEqual(primary['associated_evidence_refs'],['child:turn'])

    def test_warm_cache_reads_zero_and_matches_full_scan(self):
        home,path=self.session(self.raw());cold=self.collect(home);warm=self.collect(home)
        self.assertEqual(cold['records'],warm['records']);self.assertEqual(warm['collection_metrics']['read_bytes'],0)
        full=scan_session_file(path,datetime.fromisoformat(DAY+'T00:00:00+08:00'),datetime.fromisoformat('2026-09-05T00:00:00+08:00'),TZ)
        self.assertEqual([classify_record(r,self.profile) for r in full],cold['records'])
        self.assertEqual(self.collect(home,rebuild_index=True)['records'],cold['records'])

    def test_partial_line_and_late_tool_output(self):
        text=self.raw();home,path=self.session(text)
        call={'timestamp':DAY+'T12:01:00+08:00','type':'response_item','payload':{'type':'function_call','call_id':'call','name':'sql','arguments':'select 1'}}
        output={'timestamp':DAY+'T12:02:00+08:00','type':'response_item','payload':{'type':'function_call_output','call_id':'call','output':'result: 1'}}
        with path.open('a') as f:f.write(json.dumps(call)+'\n'+json.dumps(output)[:30])
        first=self.collect(home);self.assertEqual(first['records'][0]['tool_evidence'][0]['output_text'],'')
        with path.open('a') as f:f.write(json.dumps(output)[30:]+'\n')
        second=self.collect(home);self.assertEqual(second['records'][0]['tool_evidence'][0]['output_text'],'result: 1')
        self.assertLess(second['collection_metrics']['read_bytes'],path.stat().st_size)

    def test_archive_move_and_truncation(self):
        home,path=self.session(self.raw());cold=self.collect(home)
        archived=home/'archived_sessions';archived.mkdir();moved=archived/path.name;path.rename(moved)
        result=self.collect(home);self.assertEqual(result['collection_metrics']['read_bytes'],0);self.assertEqual(result['records'],cold['records'])
        moved.write_text(self.raw().replace('分析数据库 SQL','日报记录：数据库巡检'))
        self.assertIn('日报记录',self.collect(home)['records'][0]['user_text'])

    def test_corrupt_index_rebuild_and_unreadable_file_fail(self):
        home,path=self.session(self.raw());before=self.collect(home)
        cache=Path(self.profile['output_root'])/'.cache/report-index.sqlite3';cache.write_bytes(b'not sqlite')
        self.assertEqual(self.collect(home)['records'],before['records'])
        with mock.patch.object(Path,'stat',side_effect=OSError('unreadable')):
            with self.assertRaises(OSError):self.collect(home)

    def test_complete_corrupt_json_is_collection_failure(self):
        home,path=self.session(self.raw()+'invalid JSON\n')
        with self.assertRaisesRegex(ValueError,'Invalid complete session JSON'):self.collect(home)

    def test_cache_never_persists_raw_secret(self):
        home,path=self.session(self.raw().replace('分析数据库 SQL','分析数据库 SQL password=example-sensitive-value'))
        self.collect(home)
        import sqlite3
        with sqlite3.connect(Path(self.profile['output_root'])/'.cache/report-index.sqlite3') as db:
            value=db.execute('SELECT state FROM files').fetchone()[0]
        self.assertNotIn('example-sensitive-value',value);self.assertIn('REDACTED',value)

    def test_targeted_collection_requires_pair_and_returns_one_turn(self):
        home,path=self.session(self.raw())
        with self.assertRaises(ValueError):self.collect(home,thread_id='session')
        self.assertEqual(len(self.collect(home,thread_id='session',turn_id='turn')['records']),1)
        with self.assertRaises(ValueError):self.collect(home,thread_id='session',turn_id='missing')

    def test_collect_cli_stages_instead_of_overwriting_formal_evidence(self):
        self.save();home,path=self.session(self.raw());profile=self.root/'profile.json';profile.write_text(json.dumps(self.profile))
        formal=paths_for(DAY,self.profile)['evidence'];before=formal.read_bytes()
        result=subprocess.run([sys.executable,'-B',str(SKILL_ROOT/'scripts/reportctl.py'),'collect','--date',DAY,'--profile',str(profile),'--codex-home',str(home),'--output',str(formal)],capture_output=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        output=json.loads(result.stdout);self.assertIn('.runs',output['output_file']);self.assertEqual(before,formal.read_bytes())


if __name__=='__main__':unittest.main()
