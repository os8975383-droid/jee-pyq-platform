"""Small SQLite adapter with the Supabase query methods used by this app."""

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from types import SimpleNamespace


JSON_FIELDS = {
    "questions": {"options"},
    "tests": {"question_ids"},
    "attempts": {"answers"},
}


class SQLiteDatabase:
    """Persistent local SQLite database with automatic table creation."""

    def __init__(self, database_path):
        self.database_path = database_path
        if database_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(database_path)), exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self):
        schema = """
        CREATE TABLE IF NOT EXISTS questions (
            id TEXT PRIMARY KEY,
            exam TEXT,
            year INTEGER,
            session TEXT,
            subject TEXT NOT NULL,
            chapter TEXT NOT NULL,
            topic TEXT,
            question_text TEXT NOT NULL,
            image_url TEXT,
            options TEXT NOT NULL,
            correct_answer TEXT NOT NULL,
            solution TEXT,
            marks_correct REAL NOT NULL,
            marks_incorrect REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS tests (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL,
            question_ids TEXT NOT NULL DEFAULT '[]',
            is_published INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS attempts (
            id TEXT PRIMARY KEY,
            test_id TEXT NOT NULL REFERENCES tests(id) ON DELETE RESTRICT,
            test_title TEXT NOT NULL,
            student_name TEXT NOT NULL,
            answers TEXT NOT NULL DEFAULT '{}',
            score REAL NOT NULL,
            maximum_score REAL NOT NULL,
            percentage REAL NOT NULL,
            accuracy_percentage REAL NOT NULL DEFAULT 0,
            correct_count INTEGER NOT NULL,
            wrong_count INTEGER NOT NULL,
            unattempted_count INTEGER NOT NULL,
            started_at TEXT NOT NULL,
            submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS app_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS questions_search_idx
            ON questions (exam, year, subject, chapter, topic);
        CREATE INDEX IF NOT EXISTS attempts_test_submitted_idx
            ON attempts (test_id, submitted_at DESC);
        CREATE INDEX IF NOT EXISTS attempts_student_idx ON attempts (student_name);
        """
        with self._connect() as connection:
            connection.executescript(schema)

    def table(self, table_name):
        if table_name not in JSON_FIELDS:
            raise ValueError(f"Unknown database table: {table_name}")
        return SQLiteQuery(self, table_name)

    def get_setting(self, key):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT setting_value FROM app_settings WHERE setting_key = ?",
                (key,),
            ).fetchone()
        return row["setting_value"] if row else None

    def set_setting(self, key, value):
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO app_settings (setting_key, setting_value) VALUES (?, ?) "
                "ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value",
                (key, value),
            )


class SQLiteQuery:
    """Fluent query object implementing the subset used in app.py."""

    def __init__(self, database, table_name):
        self.database = database
        self.table_name = table_name
        self.fields = "*"
        self.filters = []
        self.ordering = None
        self.row_limit = None
        self.operation = "select"
        self.payload = None
        self.single_result = False

    def select(self, fields="*"):
        self.fields = fields
        self.operation = "select"
        return self

    def eq(self, field, value):
        self.filters.append(("eq", field, value))
        return self

    def in_(self, field, values):
        self.filters.append(("in", field, list(values)))
        return self

    def ilike(self, field, pattern):
        self.filters.append(("ilike", field, pattern))
        return self

    def order(self, field, desc=False):
        self.ordering = (field, bool(desc))
        return self

    def limit(self, count):
        self.row_limit = max(0, int(count))
        return self

    def maybe_single(self):
        self.single_result = True
        return self

    def insert(self, payload):
        self.operation = "insert"
        self.payload = payload if isinstance(payload, list) else [payload]
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def delete(self):
        self.operation = "delete"
        return self

    @staticmethod
    def _validate_field(field):
        if not field.replace("_", "").isalnum():
            raise ValueError(f"Invalid database field: {field}")
        return field

    def _where_clause(self):
        clauses = []
        parameters = []
        for operator, field, value in self.filters:
            field = self._validate_field(field)
            if operator == "eq":
                if value is None:
                    clauses.append(f"{field} IS NULL")
                else:
                    clauses.append(f"{field} = ?")
                    parameters.append(value)
            elif operator == "in":
                if not value:
                    clauses.append("0 = 1")
                else:
                    clauses.append(
                        f"{field} IN ({', '.join('?' for _ in value)})"
                    )
                    parameters.extend(value)
            elif operator == "ilike":
                clauses.append(f"{field} LIKE ? COLLATE NOCASE")
                parameters.append(value)
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), parameters

    def _serialize(self, record):
        output = dict(record)
        output.setdefault("id", str(uuid.uuid4()))
        for field in JSON_FIELDS[self.table_name]:
            if field in output and not isinstance(output[field], str):
                output[field] = json.dumps(output[field], ensure_ascii=False)
        if self.table_name == "tests" and "is_published" in output:
            output["is_published"] = int(bool(output["is_published"]))
        return output

    def _decode(self, row):
        output = dict(row)
        for field in JSON_FIELDS[self.table_name]:
            if field in output and output[field] is not None:
                output[field] = json.loads(output[field])
        if self.table_name == "tests" and "is_published" in output:
            output["is_published"] = bool(output["is_published"])
        return output

    def _execute_insert(self, connection):
        inserted = []
        for item in self.payload:
            record = self._serialize(item)
            columns = list(record)
            sql = (
                f"INSERT INTO {self.table_name} "
                f"({', '.join(columns)}) VALUES "
                f"({', '.join('?' for _ in columns)})"
            )
            connection.execute(sql, [record[column] for column in columns])
            inserted.append(record)
        return [self._decode(record) for record in inserted]

    def _execute_update(self, connection, where, parameters):
        record = self._serialize(self.payload)
        assignments = ", ".join(f"{field} = ?" for field in record if field != "id")
        values = [value for field, value in record.items() if field != "id"]
        cursor = connection.execute(
            f"UPDATE {self.table_name} SET {assignments}{where}",
            values + parameters,
        )
        return [{"updated": cursor.rowcount}] if cursor.rowcount else []

    def execute(self):
        where, parameters = self._where_clause()
        with self.database._connect() as connection:
            if self.operation == "insert":
                result = self._execute_insert(connection)
                return SimpleNamespace(data=result)
            if self.operation == "update":
                result = self._execute_update(connection, where, parameters)
                return SimpleNamespace(data=result)
            if self.operation == "delete":
                connection.execute(
                    f"DELETE FROM {self.table_name}{where}", parameters
                )
                return SimpleNamespace(data=[])

            fields = self.fields.strip()
            if fields != "*":
                selected = [self._validate_field(item.strip()) for item in fields.split(",")]
                fields = ", ".join(selected)
            sql = f"SELECT {fields} FROM {self.table_name}{where}"
            query_parameters = list(parameters)
            if self.ordering:
                field, descending = self.ordering
                direction = "DESC" if descending else "ASC"
                sql += f" ORDER BY {self._validate_field(field)} {direction}"
            if self.row_limit is not None:
                sql += " LIMIT ?"
                query_parameters.append(self.row_limit)
            records = [
                self._decode(row)
                for row in connection.execute(sql, query_parameters).fetchall()
            ]
            if self.single_result:
                records = records[0] if records else None
            return SimpleNamespace(data=records)
