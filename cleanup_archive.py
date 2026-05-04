#!/usr/bin/env python3
"""
Delete article JSON files older than --days (default 7) from data/news_archive/raw/.
Age is determined by scraped_at field; falls back to the YYYYMMDD filename prefix.
Empty source directories are removed after cleanup.
"""
import argparse
import glob
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

JP = timezone(timedelta(hours=9))
BASE = Path(__file__).parent / 'data' / 'news_archive' / 'raw'


def file_age_dt(path, data):
    ts = data.get('scraped_at', '')
    if ts:
        try:
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            if dt.tzinfo:
                return dt.astimezone(JP).replace(tzinfo=None)
            return dt
        except ValueError:
            pass
    # Fallback: parse YYYYMMDD from filename
    stem = Path(path).stem
    date_part = stem.split('_')[0]
    if len(date_part) == 8 and date_part.isdigit():
        try:
            return datetime.strptime(date_part, '%Y%m%d')
        except ValueError:
            pass
    return None


def main():
    parser = argparse.ArgumentParser(description='Clean up old article JSON files')
    parser.add_argument('--days', type=int, default=7,
                        help='Delete files older than this many days (default: 7)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print what would be deleted without deleting')
    args = parser.parse_args()

    cutoff = datetime.now(JP).replace(tzinfo=None) - timedelta(days=args.days)
    label = 'DRY RUN — ' if args.dry_run else ''
    print(f'{label}Cleaning articles older than {args.days} days (before {cutoff.strftime("%Y-%m-%d %H:%M JST")})')
    print(f'Archive: {BASE}')

    deleted = skipped = errors = 0
    deleted_by_source = {}

    for src_dir in sorted(BASE.iterdir()):
        if not src_dir.is_dir():
            continue
        for fpath in sorted(src_dir.glob('*.json')):
            try:
                with open(fpath, encoding='utf-8') as fh:
                    data = json.load(fh)
            except Exception as e:
                print(f'  SKIP unreadable {fpath.name}: {e}')
                errors += 1
                continue

            age_dt = file_age_dt(fpath, data)
            if age_dt is None:
                skipped += 1
                continue

            if age_dt < cutoff:
                deleted += 1
                deleted_by_source[src_dir.name] = deleted_by_source.get(src_dir.name, 0) + 1
                if not args.dry_run:
                    fpath.unlink()
            else:
                skipped += 1

    # Remove empty source directories
    removed_dirs = 0
    if not args.dry_run:
        for src_dir in sorted(BASE.iterdir()):
            if src_dir.is_dir() and not any(src_dir.iterdir()):
                src_dir.rmdir()
                removed_dirs += 1

    print(f'\n{"Would delete" if args.dry_run else "Deleted"}: {deleted} files  kept: {skipped}  errors: {errors}')
    if removed_dirs:
        print(f'Removed {removed_dirs} empty source directories')
    if deleted_by_source:
        print('By source:')
        for src, n in sorted(deleted_by_source.items()):
            print(f'  {src}: {n}')


if __name__ == '__main__':
    main()
