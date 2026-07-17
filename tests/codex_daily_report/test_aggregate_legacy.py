from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[2] / "skills" / "codex-daily-report"
REPORTCTL = SKILL_ROOT / "scripts" / "reportctl.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
DBA_DIMENSIONS = [
    ("一", "日常工作【DBA】"),
    ("二", "平台稳定性【DBA】"),
    ("三", "项目质量【DBA】"),
    ("四", "进度与贡献【DBA】"),
    ("五", "成长与分享【DBA】"),
    ("六", "价值共创"),
]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REPORTCTL), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def item_lines_before_next_plan(text: str) -> list[str]:
    work_text = re.split(
        r"(?m)^#{1,6}\s*下周(?:重点工作|计划)\s*$|^下周(?:重点工作|计划)[:：]?\s*$",
        text,
        maxsplit=1,
    )[0]
    return [
        line.strip()
        for line in work_text.splitlines()
        if re.match(r"^(?:[1-6]\.\s+|第[一二三四五六123456]项[:：])", line.strip())
    ]


def explicit_plan_lines(text: str) -> list[str]:
    parts = re.split(
        r"(?m)^#{1,6}\s*下周(?:重点工作|计划)\s*$|^下周(?:重点工作|计划)[:：]?\s*$",
        text,
        maxsplit=1,
    )
    if len(parts) != 2:
        return []
    result: list[str] = []
    for line in parts[1].splitlines():
        stripped = line.strip()
        match = re.match(r"^(?:\d+\.\s+|第\d+项[:：]\s*|[-*]\s+)(.+)$", stripped)
        if match:
            result.append(match.group(1).strip().rstrip("。"))
    return result


class AggregateAndLegacyContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.output_root = self.root / "daily-report"
        self.profile = self.root / "report-profile.local.json"
        write_json(
            self.profile,
            {
                "version": 1,
                "timezone": "Asia/Shanghai",
                "output_root": str(self.output_root),
                "send_policy": {
                    "daily": True,
                    "weekly": False,
                    "monthly": False,
                    "performance": False,
                },
                "performance": {"score_mode": "pending"},
            },
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def seed_structured_days(self) -> tuple[set[str], set[str]]:
        fixture = json.loads((FIXTURES / "aggregation-valid.json").read_text(encoding="utf-8"))
        submitted_texts: set[str] = set()
        follow_ups: set[str] = set()
        for day in fixture["days"]:
            report_date = day["date"]
            month_dir = self.output_root / report_date[:7]
            evidence = {
                "version": 1,
                "report_date": report_date,
                "timezone": "Asia/Shanghai",
                "records": day["records"],
            }
            work_items = {
                "version": 1,
                "report_date": report_date,
                "items": day["items"],
            }
            write_json(month_dir / f"codex-evidence-{report_date}.json", evidence)
            write_json(month_dir / f"codex-work-items-{report_date}.json", work_items)
            source_hash = hashlib.sha256(
                json.dumps(work_items, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()
            write_json(
                month_dir / f"codex-run-state-{report_date}.json",
                {
                    "version": 1,
                    "report_date": report_date,
                    "report_type": "daily",
                    "content_hash": source_hash,
                    "evidence_hash": canonical_hash(evidence),
                    "work_items_hash": canonical_hash(work_items),
                    "send_state": "disabled",
                    "sent_hashes": [],
                    "validation_errors": [],
                },
            )
            submitted_texts.update(item["submitted_text"] for item in day["items"])
            follow_ups.update(item["follow_up"] for item in day["items"] if item["follow_up"])
        return submitted_texts, follow_ups

    def seed_one_structured_day(self, report_date: str, day: dict) -> None:
        month_dir = self.output_root / report_date[:7]
        evidence = {
            "version": 1,
            "report_date": report_date,
            "timezone": "Asia/Shanghai",
            "records": day["records"],
        }
        work_items = {
            "version": 1,
            "report_date": report_date,
            "items": day["items"],
        }
        write_json(month_dir / f"codex-evidence-{report_date}.json", evidence)
        write_json(month_dir / f"codex-work-items-{report_date}.json", work_items)
        write_json(
            month_dir / f"codex-run-state-{report_date}.json",
            {
                "version": 1, "report_date": report_date, "report_type": "daily",
                "content_hash": hashlib.sha256(
                    json.dumps(work_items, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                "evidence_hash": canonical_hash(evidence),
                "work_items_hash": canonical_hash(work_items),
                "send_state": "disabled", "sent_hashes": [], "validation_errors": [],
            },
        )

    def parse_ok(self, result: subprocess.CompletedProcess[str], report_type: str) -> tuple[dict, list[Path]]:
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.strip(), result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["report_type"], report_type)
        self.assertEqual(payload["validation_errors"], [])
        self.assertFalse(payload["send_ready"], f"{report_type} must be save-only by default")
        self.assertTrue(payload["content_hash"])
        output_paths = [Path(path) for path in payload["output_files"].values()]
        self.assertTrue(output_paths)
        self.assertTrue(all(path.exists() for path in output_paths))
        return payload, output_paths

    def markdown_output(self, output_paths: list[Path], prefix: str) -> Path:
        matches = [path for path in output_paths if path.suffix == ".md" and path.name.startswith(prefix)]
        self.assertTrue(matches, f"missing Markdown output with prefix {prefix}: {output_paths}")
        return matches[0]

    def test_weekly_aggregates_three_to_six_items_and_plans_only_explicit_follow_up(self) -> None:
        _, follow_ups = self.seed_structured_days()

        first = run_cli(
            "aggregate", "--type", "weekly", "--date", "2026-07-10", "--profile", str(self.profile)
        )

        first_payload, first_paths = self.parse_ok(first, "weekly")
        weekly = self.markdown_output(first_paths, "codex-weekly-submit-")
        weekly_text = weekly.read_text(encoding="utf-8")
        work_items = item_lines_before_next_plan(weekly_text)
        self.assertGreaterEqual(len(work_items), 3)
        self.assertLessEqual(len(work_items), 6)
        plans = explicit_plan_lines(weekly_text)
        self.assertTrue(plans, "explicit follow_up values must produce a next-week plan")
        self.assertTrue(set(plans).issubset(follow_ups), (plans, follow_ups))
        self.assertNotIn("核查数据库集群健康状态", "\n".join(plans))

        before = {path: path.read_bytes() for path in first_paths}
        second = run_cli(
            "aggregate", "--type", "weekly", "--date", "2026-07-10", "--profile", str(self.profile)
        )
        second_payload, second_paths = self.parse_ok(second, "weekly")
        self.assertEqual(first_payload["content_hash"], second_payload["content_hash"])
        self.assertEqual(first_payload["output_files"], second_payload["output_files"])
        self.assertEqual(before, {path: path.read_bytes() for path in second_paths})

    def test_weekly_revision_is_validated_persisted_and_show_is_byte_exact(self) -> None:
        self.seed_structured_days()
        revision = self.root / "weekly-revision.json"
        detailed_plan = (
            "推进慢SQL优化闭环，复核执行计划、索引、扫描行数、执行耗时、锁等待和磁盘临时表，"
            "形成优化前后对比及遗留风险清单"
        )
        write_json(
            revision,
            {
                "version": 1,
                "report_week": "2026-W28",
                "source_kind": "explicit_user_revision",
                "sources": [
                    {
                        "id": "thread-work:turn-detail", "thread_id": "thread-work",
                        "turn_id": "turn-detail", "occurred_at": "2026-07-10T10:00:00+08:00",
                        "user_text": "补充订单慢SQL的具体耗时、扫描和反馈开发状态。",
                    },
                    {
                        "id": "thread-work:turn-finance", "thread_id": "thread-work",
                        "turn_id": "turn-finance", "occurred_at": "2026-07-10T10:01:00+08:00",
                        "user_text": "新增配合财务开展服务器降本增效事项。password=fixture-password",
                    },
                    {
                        "id": "thread-work:turn-plan", "thread_id": "thread-work",
                        "turn_id": "turn-plan", "occurred_at": "2026-07-10T10:02:00+08:00",
                        "user_text": "下周复核慢SQL执行计划并形成前后对比。",
                    },
                ],
                "items": [
                    {
                        "operation": "replace",
                        "target_id": "slow-orders",
                        "group": "performance_incident",
                        "text": (
                            "完成db-a订单慢SQL专项排查，代表查询平均耗时8.4秒并存在大范围扫描，"
                            "执行计划与索引方向已反馈开发；当前等待改造后复核。"
                        ),
                        "priority_signals": ["production_risk"],
                        "source_ref": "thread-work:turn-detail",
                    },
                    {
                        "operation": "add",
                        "id": "finance-cost",
                        "group": "capacity_cost",
                        "text": (
                            "配合财务开展服务器降本增效，完成资源用途、配置规格和费用情况核对，"
                            "形成降配、回收及计费方式调整方向。"
                        ),
                        "priority_signals": ["financial_impact"],
                        "source_ref": "thread-work:turn-finance",
                    },
                ],
                "plans": [
                    {"text": detailed_plan, "source_ref": "thread-work:turn-plan"},
                ],
            },
        )

        revise = run_cli(
            "weekly-revise",
            "--date", "2026-07-10",
            "--revision", str(revision),
            "--profile", str(self.profile),
        )
        self.assertEqual(revise.returncode, 0, revise.stderr)
        revise_payload = json.loads(revise.stdout)
        persisted = Path(revise_payload["output_file"])
        self.assertEqual(persisted.name, "codex-weekly-overrides-2026-W28.json")
        self.assertTrue(persisted.exists())

        first = run_cli(
            "aggregate", "--type", "weekly", "--date", "2026-07-10", "--profile", str(self.profile)
        )
        second = run_cli(
            "aggregate", "--type", "weekly", "--date", "2026-07-10", "--profile", str(self.profile)
        )
        first_payload, first_paths = self.parse_ok(first, "weekly")
        second_payload, _ = self.parse_ok(second, "weekly")
        self.assertEqual(first_payload["content_hash"], second_payload["content_hash"])
        weekly = self.markdown_output(first_paths, "codex-weekly-submit-")
        text = weekly.read_text(encoding="utf-8")
        self.assertIn("配合财务开展服务器降本增效", text)
        self.assertIn(detailed_plan, text)

        shown = run_cli(
            "show", "--type", "weekly", "--date", "2026-07-10", "--profile", str(self.profile)
        )
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(shown.stdout.encode("utf-8"), weekly.read_bytes())

    def test_consecutive_weekly_revisions_are_merged_instead_of_overwritten(self) -> None:
        self.seed_structured_days()
        first_revision = self.root / "weekly-revision-first.json"
        write_json(
            first_revision,
            {
                "version": 1,
                "report_week": "2026-W28",
                "source_kind": "explicit_user_revision",
                "sources": [
                    {
                        "id": "thread-work:turn-finance", "thread_id": "thread-work",
                        "turn_id": "turn-finance", "occurred_at": "2026-07-10T10:00:00+08:00",
                        "user_text": "新增配合财务开展服务器降本增效事项。password=fixture-password",
                    },
                    {
                        "id": "thread-work:turn-plan-first", "thread_id": "thread-work",
                        "turn_id": "turn-plan-first", "occurred_at": "2026-07-10T10:01:00+08:00",
                        "user_text": "下周完成服务器处置清单复核。",
                    },
                ],
                "items": [{
                    "operation": "add",
                    "id": "finance-cost",
                    "group": "capacity_cost",
                    "text": (
                        "配合财务开展服务器降本增效，完成资源用途、配置规格和费用情况核对，"
                        "形成降配、回收及计费方式调整方向。"
                    ),
                    "priority_signals": ["financial_impact"],
                    "source_ref": "thread-work:turn-finance",
                }],
                "plans": [{
                    "text": "完成服务器处置清单复核，明确责任人、费用影响和预期交付",
                    "source_ref": "thread-work:turn-plan-first",
                }],
            },
        )
        first = run_cli(
            "weekly-revise", "--date", "2026-07-10", "--revision", str(first_revision),
            "--profile", str(self.profile),
        )
        self.assertEqual(first.returncode, 0, first.stderr)

        second_revision = self.root / "weekly-revision-second.json"
        write_json(
            second_revision,
            {
                "version": 1,
                "report_week": "2026-W28",
                "source_kind": "explicit_user_revision",
                "sources": [{
                    "id": "thread-work:turn-slow-detail", "thread_id": "thread-work",
                    "turn_id": "turn-slow-detail", "occurred_at": "2026-07-10T11:00:00+08:00",
                    "user_text": "补充db-a订单慢SQL的平均耗时和反馈开发状态。",
                }],
                "items": [{
                    "operation": "replace",
                    "target_id": "slow-orders",
                    "group": "performance_incident",
                    "text": (
                        "完成db-a订单慢SQL专项排查，代表查询平均耗时8.4秒并存在大范围扫描，"
                        "执行计划与索引方向已反馈开发；当前等待改造后复核。"
                    ),
                    "priority_signals": ["production_risk"],
                    "source_ref": "thread-work:turn-slow-detail",
                }],
            },
        )
        second = run_cli(
            "weekly-revise", "--date", "2026-07-10", "--revision", str(second_revision),
            "--profile", str(self.profile),
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        persisted = json.loads(Path(json.loads(second.stdout)["output_file"]).read_text(encoding="utf-8"))
        self.assertEqual(len(persisted["items"]), 2)
        self.assertIn("plans", persisted)
        self.assertNotIn("fixture-password", json.dumps(persisted, ensure_ascii=False))
        self.assertIn("[REDACTED]", json.dumps(persisted, ensure_ascii=False))

        aggregated = run_cli(
            "aggregate", "--type", "weekly", "--date", "2026-07-10", "--profile", str(self.profile)
        )
        payload, paths = self.parse_ok(aggregated, "weekly")
        text = self.markdown_output(paths, "codex-weekly-submit-").read_text(encoding="utf-8")
        self.assertTrue(payload["content_hash"])
        self.assertIn("配合财务开展服务器降本增效", text)
        self.assertIn("平均耗时8.4秒", text)
        self.assertIn("完成服务器处置清单复核", text)

    def test_cross_month_week_uses_one_canonical_storage_month(self) -> None:
        fixture = json.loads((FIXTURES / "aggregation-valid.json").read_text(encoding="utf-8"))
        self.seed_one_structured_day("2026-08-31", fixture["days"][0])
        self.seed_one_structured_day("2026-09-01", fixture["days"][1])

        first = run_cli(
            "aggregate", "--type", "weekly", "--date", "2026-08-31", "--profile", str(self.profile)
        )
        first_payload, first_paths = self.parse_ok(first, "weekly")
        weekly = self.markdown_output(first_paths, "codex-weekly-submit-")
        self.assertEqual(weekly.parent.name, "2026-09")
        self.assertEqual(weekly.name, "codex-weekly-submit-2026-W36.md")

        shown = run_cli(
            "show", "--type", "weekly", "--date", "2026-09-04", "--profile", str(self.profile)
        )
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(shown.stdout.encode("utf-8"), weekly.read_bytes())

        second = run_cli(
            "aggregate", "--type", "weekly", "--date", "2026-09-04", "--profile", str(self.profile)
        )
        second_payload, _ = self.parse_ok(second, "weekly")
        self.assertEqual(first_payload["content_hash"], second_payload["content_hash"])

    def test_weekly_revision_with_non_object_json_returns_validation_failure(self) -> None:
        self.seed_structured_days()
        revision = self.root / "invalid-weekly-revision.json"
        revision.write_text("[]\n", encoding="utf-8")
        result = run_cli(
            "weekly-revise", "--date", "2026-07-10", "--revision", str(revision),
            "--profile", str(self.profile),
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "validation_failed")
        self.assertEqual(payload["validation_errors"][0]["code"], "invalid_weekly_revision")

    def test_monthly_report_is_retained_separately_without_rewriting_root_data(self) -> None:
        self.seed_structured_days()
        month_dir = self.output_root / "2026-07"
        daily_root = month_dir / "codex-daily-submit-2026-07.md"
        daily_root.write_text("# 2026-07 日报汇总\n\n## 已有提交稿\n", encoding="utf-8")
        source_paths = sorted(month_dir.glob("codex-*-2026-07-*.json")) + [daily_root]
        before = {path: path.read_bytes() for path in source_paths}

        result = run_cli(
            "aggregate", "--type", "monthly", "--date", "2026-07-31", "--profile", str(self.profile)
        )

        _, output_paths = self.parse_ok(result, "monthly")
        monthly = self.markdown_output(output_paths, "codex-monthly-submit-2026-07")
        monthly_text = monthly.read_text(encoding="utf-8")
        self.assertIn("2026-07 月报", monthly_text)
        self.assertTrue(any(name in monthly_text for name in ("db-a", "audit.event_log", "sync-orders")))
        self.assertEqual(before, {path: path.read_bytes() for path in source_paths})

    def test_performance_has_six_dimensions_three_traceable_points_and_pending_scores(self) -> None:
        submitted_texts, _ = self.seed_structured_days()

        result = run_cli(
            "aggregate", "--type", "performance", "--date", "2026-07-31", "--profile", str(self.profile)
        )

        _, output_paths = self.parse_ok(result, "performance")
        performance = self.markdown_output(output_paths, "codex-performance-submit-2026-07")
        text = performance.read_text(encoding="utf-8")
        found_headings = re.findall(r"(?m)^([一二三四五六])、([^：\n]+)：(待填分)\s*$", text)
        self.assertEqual(found_headings, [(ordinal, title, "待填分") for ordinal, title in DBA_DIMENSIONS])
        sections = re.split(r"(?m)^[一二三四五六]、[^：\n]+：待填分\s*$", text)[1:]
        self.assertEqual(len(sections), 6)
        for (ordinal, title), section in zip(DBA_DIMENSIONS, sections):
            points = re.findall(
                r"第([123])点[:：](.*?)(?=第[123]点[:：]|\n\s*\n|\Z)",
                section,
                flags=re.S,
            )
            self.assertEqual([number for number, _ in points], ["1", "2", "3"], f"{title}: {points}")
            normalized = [point.strip().rstrip("。") for _, point in points]
            non_missing = [point for point in normalized if point != "待补充证据"]
            self.assertEqual(len(non_missing), len(set(non_missing)), f"{title} repeats one evidence point")
            for point in non_missing:
                self.assertTrue(
                    any(point == source.rstrip("。") for source in submitted_texts),
                    f"{ordinal}、{title} contains an untraceable point: {point}",
                )

    def test_aggregate_revalidates_items_and_rejects_invalid_sidecar_without_writing(self) -> None:
        self.seed_structured_days()
        invalid_path = self.output_root / "2026-07" / "codex-work-items-2026-07-07.json"
        invalid = json.loads(invalid_path.read_text(encoding="utf-8"))
        duplicate = json.loads(json.dumps(invalid["items"][0], ensure_ascii=False))
        duplicate["id"] = "duplicate-backup-db-c"
        invalid["items"].append(duplicate)
        write_json(invalid_path, invalid)

        result = run_cli(
            "aggregate", "--type", "weekly", "--date", "2026-07-10", "--profile", str(self.profile)
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue(result.stdout.strip(), result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "validation_failed")
        self.assertFalse(payload["send_ready"])
        self.assertTrue(any(error["code"] == "duplicate_work_item" for error in payload["validation_errors"]))
        self.assertFalse(list((self.output_root / "2026-07").glob("codex-weekly-submit-*.md")))

    def test_import_legacy_creates_idempotent_sidecars_and_never_rewrites_markdown(self) -> None:
        month_dir = self.output_root / "2026-07"
        month_dir.mkdir(parents=True)
        legacy = month_dir / "codex-daily-submit-2026-07.md"
        legacy.write_bytes((FIXTURES / "legacy-daily-submit-2026-07.md").read_bytes())
        original_markdown = legacy.read_bytes()

        first = run_cli("import-legacy", "--month", "2026-07", "--profile", str(self.profile))

        self.assertEqual(first.returncode, 0, first.stderr)
        payload = json.loads(first.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["source_kind"], "legacy_submitted")
        self.assertEqual(payload["imported_dates"], ["2026-07-01", "2026-07-02"])
        self.assertEqual(legacy.read_bytes(), original_markdown)
        sidecar_paths: list[Path] = []
        for report_date, expected_count in (("2026-07-01", 2), ("2026-07-02", 1)):
            evidence_path = month_dir / f"codex-evidence-{report_date}.json"
            items_path = month_dir / f"codex-work-items-{report_date}.json"
            self.assertTrue(evidence_path.exists())
            self.assertTrue(items_path.exists())
            sidecar_paths.extend((evidence_path, items_path))
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            work_items = json.loads(items_path.read_text(encoding="utf-8"))
            self.assertEqual(evidence["report_date"], report_date)
            self.assertEqual(work_items["report_date"], report_date)
            self.assertEqual(len(work_items["items"]), expected_count)
            self.assertTrue(evidence["records"])
            self.assertTrue(all(record["source_kind"] == "legacy_submitted" for record in evidence["records"]))
            evidence_ids = {record["id"] for record in evidence["records"]}
            self.assertTrue(
                all(set(item["evidence_refs"]).issubset(evidence_ids) for item in work_items["items"])
            )

        before = {path: path.read_bytes() for path in sidecar_paths}
        second = run_cli("import-legacy", "--month", "2026-07", "--profile", str(self.profile))
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(legacy.read_bytes(), original_markdown)
        self.assertEqual(before, {path: path.read_bytes() for path in sidecar_paths})


if __name__ == "__main__":
    unittest.main()
