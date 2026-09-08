"""Disposable, private incremental index of local session files."""
from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import time
from pathlib import Path

from .session_parser import SessionParser

INDEX_VERSION = 3


def fingerprints(handle, offset, metrics):
    position = handle.tell()
    width = min(offset, 64)
    handle.seek(0)
    first = handle.read(width)
    handle.seek(max(0, offset - width))
    last = handle.read(width)
    metrics['read_bytes'] += len(first) + len(last)
    handle.seek(position)
    return [hashlib.sha256(first).hexdigest(), hashlib.sha256(last).hexdigest()]


def indexed_records(paths, cache_path, start, end, timezone, rebuild=False):
    began = time.monotonic()
    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    metrics = {"scanned_files": 0, "read_bytes": 0, "cache_hits": 0, "rebuilds": 0, "guardian_files": 0}
    db = None
    for attempt in range(2):
        try:
            db = sqlite3.connect(cache_path, timeout=60)
            os.chmod(cache_path, 0o600)
            db.execute('PRAGMA schema_version').fetchone()
            db.execute('CREATE TABLE IF NOT EXISTS meta (version INTEGER, timezone TEXT)')
            row = db.execute('SELECT version, timezone FROM meta').fetchone()
            if rebuild or row != (INDEX_VERSION, str(timezone)):
                db.execute('DROP TABLE IF EXISTS files')
                db.execute('DROP TABLE IF EXISTS ranges')
                db.execute('DELETE FROM meta')
                db.execute('INSERT INTO meta VALUES (?,?)', (INDEX_VERSION, str(timezone)))
            db.execute('''CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY, device INTEGER, inode INTEGER, size INTEGER,
                mtime INTEGER, offset INTEGER, state TEXT NOT NULL)''')
            db.execute('CREATE TABLE IF NOT EXISTS ranges (path TEXT PRIMARY KEY, first_day TEXT, last_day TEXT, excluded INTEGER)')
            db.commit()
            break
        except sqlite3.DatabaseError:
            if db is not None:
                db.close()
            if attempt:
                raise
            cache_path.unlink(missing_ok=True)
            rebuild = True
    records = []
    try:
        # One transaction prevents concurrent collectors from overwriting a newer cursor.
        db.execute('BEGIN IMMEDIATE')
        known = {row[0]: row[1:] for row in db.execute('SELECT path,device,inode,size,mtime,offset FROM files')}
        identities = {(row[0], row[1]): key for key,row in known.items()}
        ranges = {row[0]: row[1:] for row in db.execute('SELECT * FROM ranges')}
        seen = set()
        for path in paths:
            stat = path.stat()  # Fail visibly; inaccessible evidence is not an empty day.
            key = str(path.resolve())
            seen.add(key)
            metrics['scanned_files'] += 1
            origin = key if key in known else identities.get((stat.st_dev, stat.st_ino))
            row = known.get(origin)
            reusable = row and row[:2] == (stat.st_dev, stat.st_ino) and stat.st_size >= row[2]
            unchanged = reusable and stat.st_size == row[2] and stat.st_mtime_ns == row[3]
            if reusable and stat.st_size == row[2] and stat.st_mtime_ns != row[3]:
                reusable = False
            coverage = ranges.get(origin)
            if unchanged and coverage and (coverage[2] or not coverage[0] or coverage[0] >= end.date().isoformat() or coverage[1] < start.date().isoformat()):
                metrics['cache_hits'] += 1
                metrics['guardian_files'] += int(bool(coverage[2]))
                if key != origin:
                    db.execute('INSERT OR REPLACE INTO files SELECT ?,device,inode,size,mtime,offset,state FROM files WHERE path=?', (key,origin))
                    db.execute('INSERT OR REPLACE INTO ranges VALUES (?,?,?,?)', (key,*coverage))
                continue
            try:
                saved = json.loads(db.execute('SELECT state FROM files WHERE path=?', (origin,)).fetchone()[0]) if reusable else {}
                if reusable and not unchanged:
                    with path.open('rb') as check:
                        if fingerprints(check, row[4], metrics) != saved.get('_fingerprints'):
                            reusable = False
                parser = SessionParser(timezone, saved if reusable else None)
            except (ValueError, TypeError, KeyError):
                parser = SessionParser(timezone)
                reusable = unchanged = False
            offset = row[4] if reusable else 0
            if unchanged:
                metrics['cache_hits'] += 1
            elif not parser.excluded:
                if not reusable:
                    metrics['rebuilds'] += 1
                with path.open('rb') as handle:
                    handle.seek(offset)
                    while True:
                        line = handle.readline()
                        metrics['read_bytes'] += len(line)
                        if not line:
                            break
                        # Retry incomplete lines from their start on the next collection.
                        if not line.endswith(b'\n'):
                            break
                        parser.feed(line.decode('utf-8', errors='replace'))
                        offset = handle.tell()
                        if parser.excluded:
                            break
                    after = os.fstat(handle.fileno())
                    if (after.st_dev, after.st_ino) != (stat.st_dev, stat.st_ino) or after.st_size < stat.st_size:
                        raise OSError(f'Session changed identity or was truncated during collection: {path}')
                    marks = fingerprints(handle, offset, metrics)
                db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?)', (
                    key, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, offset,
                    json.dumps(dict(parser.dump(), _fingerprints=marks), ensure_ascii=False, separators=(',', ':'))))
            if parser.excluded:
                metrics['guardian_files'] += 1
            if unchanged and key not in known:
                db.execute('INSERT OR REPLACE INTO files SELECT ?,device,inode,size,mtime,offset,state FROM files WHERE path=?', (key,origin))
            dates = [turn['occurred_at'].date().isoformat() for turn in parser.turns.values() if turn.get('occurred_at')]
            db.execute('INSERT OR REPLACE INTO ranges VALUES (?,?,?,?)', (key,min(dates,default=''),max(dates,default=''),int(parser.excluded)))
            records.extend(parser.records(start, end))
        for key in known.keys() - seen:
            db.execute('DELETE FROM files WHERE path=?', (key,))
            db.execute('DELETE FROM ranges WHERE path=?', (key,))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
    metrics['elapsed_seconds'] = round(time.monotonic() - began, 3)
    return records, metrics
