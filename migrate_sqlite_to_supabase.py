"""Copy local SQLite questions, tests, and attempts into Supabase without overwriting."""

import argparse
import json
import sqlite3
import uuid

from supabase import create_client

from config import Config

TABLES = ("questions", "tests", "attempts")
BATCH_SIZE = 100


def stable_database_id(table, value):
    """Preserve UUID identifiers; deterministically convert any legacy non-UUID IDs."""
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"jee-exam:{table}:{value}"))


def read_local_rows(sqlite_path):
    with sqlite3.connect(sqlite_path) as connection:
        connection.row_factory = sqlite3.Row
        available = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = set(TABLES) - available
        if missing:
            raise RuntimeError(
                "SQLite database is missing tables: " + ", ".join(sorted(missing))
            )
        result = {
            table: [
                dict(row)
                for row in connection.execute(f"SELECT * FROM {table}").fetchall()
            ]
            for table in TABLES
        }

    for row in result["questions"]:
        if isinstance(row.get("options"), str):
            row["options"] = json.loads(row["options"])
    for row in result["tests"]:
        if isinstance(row.get("question_ids"), str):
            row["question_ids"] = json.loads(row["question_ids"])
    for row in result["attempts"]:
        if isinstance(row.get("answers"), str):
            row["answers"] = json.loads(row["answers"])
    return result


def prepare_migration(rows):
    """Remap local IDs to UUIDs while maintaining question/test/attempt references."""
    id_maps = {
        table: {
            str(row["id"]): stable_database_id(table, row["id"])
            for row in rows[table]
        }
        for table in TABLES
    }
    prepared = {table: [] for table in TABLES}

    for row in rows["questions"]:
        record = dict(row)
        record["id"] = id_maps["questions"][str(row["id"])]
        prepared["questions"].append(record)

    for row in rows["tests"]:
        record = dict(row)
        record["id"] = id_maps["tests"][str(row["id"])]
        record["question_ids"] = [
            id_maps["questions"].get(str(question_id), str(question_id))
            for question_id in (row.get("question_ids") or [])
        ]
        prepared["tests"].append(record)

    for row in rows["attempts"]:
        record = dict(row)
        record["id"] = id_maps["attempts"][str(row["id"])]
        record["test_id"] = id_maps["tests"].get(str(row["test_id"]), str(row["test_id"]))
        answers = {}
        for question_id, answer in (row.get("answers") or {}).items():
            new_question_id = id_maps["questions"].get(str(question_id), str(question_id))
            answer_record = dict(answer)
            if answer_record.get("id") is not None:
                answer_record["id"] = new_question_id
            answers[new_question_id] = answer_record
        record["answers"] = answers
        prepared["attempts"].append(record)
    return prepared


def batches(items, size=BATCH_SIZE):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def migrate_rows(client, rows, dry_run=False):
    """Insert missing rows by primary key and verify each migrated table."""
    summary = {}
    for table in TABLES:
        planned = rows[table]
        ids = [row["id"] for row in planned]
        found = set()
        for id_batch in batches(ids):
            if not id_batch:
                continue
            existing = (
                client.table(table).select("id").in_("id", id_batch).execute().data
                or []
            )
            found.update(row["id"] for row in existing)
        missing = [row for row in planned if row["id"] not in found]
        summary[table] = {"source": len(planned), "already_present": len(found), "to_insert": len(missing)}
        if dry_run:
            continue
        for row_batch in batches(missing):
            if row_batch:
                client.table(table).insert(row_batch).execute()

        verified = set()
        for id_batch in batches(ids):
            if not id_batch:
                continue
            records = (
                client.table(table).select("id").in_("id", id_batch).execute().data
                or []
            )
            verified.update(row["id"] for row in records)
        if not set(ids).issubset(verified):
            raise RuntimeError(
                f"Migration verification failed for {table}: not all source IDs "
                "were found in Supabase."
            )
        summary[table]["verified"] = len(verified.intersection(ids))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite-path",
        default=Config.SQLITE_DATABASE_PATH,
        help="Path to the existing SQLite database file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report source/target counts without writing to Supabase.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Allow insertions. Existing primary-key records are never overwritten.",
    )
    args = parser.parse_args()
    if not Config.SUPABASE_URL or not Config.SUPABASE_SERVICE_ROLE_KEY:
        raise SystemExit(
            "Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env before migration."
        )
    if not args.dry_run and not args.confirm:
        raise SystemExit("Run with --dry-run first, then use --confirm to write.")

    source = read_local_rows(args.sqlite_path)
    prepared = prepare_migration(source)
    client = create_client(Config.SUPABASE_URL, Config.SUPABASE_SERVICE_ROLE_KEY)
    summary = migrate_rows(client, prepared, dry_run=args.dry_run)
    for table, counts in summary.items():
        print(
            f"{table}: source={counts['source']}, "
            f"already present={counts['already_present']}, "
            f"to insert={counts['to_insert']}"
            + (
                ""
                if args.dry_run
                else f", verified={counts['verified']}"
            )
        )
    if args.dry_run:
        print("Dry run only; no Supabase records were changed.")
    else:
        print("Migration finished and source record IDs were verified.")


if __name__ == "__main__":
    main()
