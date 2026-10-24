"""Flask application for a JEE Previous Year Question practice platform."""

import csv
import hashlib
import io
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    send_file,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from flask_wtf.csrf import CSRFProtect
from database import SQLiteDatabase

CSV_FIELDS = [
    "exam",
    "year",
    "session",
    "subject",
    "chapter",
    "topic",
    "question_text",
    "image_url",
    "option_a",
    "option_b",
    "option_c",
    "option_d",
    "correct_answer",
    "solution",
    "marks_correct",
    "marks_incorrect",
]
EXAMS = {"JEE Main", "JEE Advanced"}
SUBJECTS = {"Physics", "Chemistry", "Mathematics"}
ANSWERS = {"A", "B", "C", "D"}
MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ROWS = 500


def validate_question(data):
    """Return (normalized question, errors) without writing anything."""
    errors = []
    exam = (data.get("exam") or "").strip()
    subject = (data.get("subject") or "").strip()
    raw_year = (data.get("year") or "").strip()
    if raw_year:
        try:
            year = int(raw_year)
            if year < 2002 or year > datetime.now().year:
                raise ValueError
        except (TypeError, ValueError):
            year = None
            errors.append("Year must be a valid JEE year.")
    else:
        year = None

    question_text = (data.get("question_text") or "").strip()
    solution = (data.get("solution") or "").strip()
    correct_answer = (data.get("correct_answer") or "").strip().upper()
    if exam and exam not in EXAMS:
        errors.append("Exam must be JEE Main or JEE Advanced when specified.")
    if subject not in SUBJECTS:
        errors.append("Subject must be Physics, Chemistry, or Mathematics.")
    for field in ("chapter", "question_text"):
        if not (data.get(field) or "").strip():
            errors.append(f"{field.replace('_', ' ').capitalize()} is required.")
    limits = {
        "exam": 20,
        "session": 120,
        "chapter": 120,
        "topic": 120,
        "question_text": 20000,
        "solution": 50000,
        "option_a": 10000,
        "option_b": 10000,
        "option_c": 10000,
        "option_d": 10000,
    }
    for field, maximum_length in limits.items():
        if len((data.get(field) or "").strip()) > maximum_length:
            errors.append(
                f"{field.replace('_', ' ').capitalize()} must be at most "
                f"{maximum_length} characters."
            )
    if correct_answer not in ANSWERS:
        errors.append("Correct answer must be A, B, C, or D.")

    options = {}
    for key in ("A", "B", "C", "D"):
        value = (data.get(f"option_{key.lower()}") or "").strip()
        if not value:
            errors.append(f"Option {key} is required.")
        options[key] = value

    try:
        marks_correct = float(data.get("marks_correct", ""))
        if not 0 < marks_correct <= 100:
            raise ValueError
    except (TypeError, ValueError):
        marks_correct = None
        errors.append("Correct marks must be greater than 0 and at most 100.")
    try:
        marks_incorrect = float(data.get("marks_incorrect", ""))
        if not 0 <= marks_incorrect <= 100:
            raise ValueError
    except (TypeError, ValueError):
        marks_incorrect = None
        errors.append("Incorrect marks must be between 0 and 100.")

    image_url = (data.get("image_url") or "").strip()
    if image_url and not re.match(r"^https?://", image_url, re.IGNORECASE):
        errors.append("Image URL must begin with http:// or https://.")
    if len(image_url) > 2048:
        errors.append("Image URL must be at most 2048 characters.")

    normalized = {
        "exam": exam or None,
        "year": year,
        "session": (data.get("session") or "").strip() or None,
        "subject": subject,
        "chapter": (data.get("chapter") or "").strip(),
        "topic": (data.get("topic") or "").strip() or None,
        "question_text": question_text,
        "image_url": image_url or None,
        "options": options,
        "correct_answer": correct_answer,
        "solution": solution or None,
        "marks_correct": marks_correct,
        "marks_incorrect": marks_incorrect,
    }
    return normalized, errors


def parse_csv(content):
    """Validate a complete CSV import and return normalized rows and errors."""
    errors = []
    try:
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text), strict=True)
    except (UnicodeDecodeError, csv.Error):
        return [], ["File must be a valid UTF-8 CSV."]
    try:
        fieldnames = reader.fieldnames
    except csv.Error as error:
        return [], [f"Could not read CSV header: {error}."]
    if not fieldnames or len(set(fieldnames)) != len(fieldnames) or set(CSV_FIELDS) - set(fieldnames):
        missing = sorted(set(CSV_FIELDS) - set(fieldnames or []))
        if missing:
            return [], [f"Missing required CSV columns: {', '.join(missing)}."]
        return [], ["CSV column names must not be duplicated."]

    questions = []
    try:
        for row_number, row in enumerate(reader, start=2):
            if row_number > MAX_IMPORT_ROWS + 1:
                errors.append(f"CSV may contain at most {MAX_IMPORT_ROWS} questions.")
                break
            if None in row:
                errors.append(f"Row {row_number}: too many values for the CSV columns.")
                continue
            question, row_errors = validate_question(row)
            if row_errors:
                errors.extend([f"Row {row_number}: {error}" for error in row_errors])
            else:
                questions.append(question)
    except csv.Error as error:
        errors.append(f"Could not parse CSV: {error}.")
    if not questions and not errors:
        errors.append("CSV contains no question rows.")
    return questions, errors


def question_fingerprint(question):
    """Build a stable content identity for duplicate detection."""
    return (
        question["subject"].casefold(),
        question["chapter"].casefold(),
        " ".join(question["question_text"].casefold().split()),
        tuple(
            " ".join(question["options"][key].casefold().split())
            for key in ("A", "B", "C", "D")
        ),
    )


def filter_existing_questions(client, questions):
    """Return questions not already stored and the number of duplicates found."""
    if not questions:
        return [], 0
    question_texts = list(dict.fromkeys(item["question_text"] for item in questions))
    existing = (
        client.table("questions")
        .select("subject,chapter,question_text,options")
        .in_("question_text", question_texts)
        .execute()
        .data
        or []
    )
    existing_keys = {
        question_fingerprint(item)
        for item in existing
        if item.get("subject") and item.get("chapter") and item.get("question_text")
    }
    unique = []
    seen = set(existing_keys)
    duplicates = 0
    for item in questions:
        key = question_fingerprint(item)
        if key in seen:
            duplicates += 1
            continue
        unique.append(item)
        seen.add(key)
    return unique, duplicates


def configure_local_admin(app):
    """Load or create a hashed local admin credential and reveal new passwords once."""
    if app.config.get("ADMIN_PASSWORD_HASH"):
        return None
    database = app.extensions["database_client"]
    password_hash = database.get_setting("admin_password_hash")
    password = None
    if password_hash is None:
        password = secrets.token_urlsafe(24)
        password_hash = generate_password_hash(password)
        database.set_setting("admin_password_hash", password_hash)
        print(
            "\nA local admin password was generated and stored as a hash in SQLite."
            f"\nAdmin username: {app.config['ADMIN_USERNAME']}"
            f"\nAdmin password (save it now): {password}\n",
            flush=True,
        )
    app.config["ADMIN_PASSWORD_HASH"] = password_hash
    return password


def score_submission(questions, submitted_answers):
    """Score answers on the server and produce review details."""
    review = []
    score = 0.0
    correct = wrong = unattempted = 0
    for question in questions:
        question_id = str(question["id"])
        chosen = (submitted_answers.get(question_id) or "").upper()
        answer = question["correct_answer"]
        if chosen not in ANSWERS:
            chosen = ""
            unattempted += 1
            points = 0.0
        elif chosen == answer:
            correct += 1
            points = float(question["marks_correct"])
            score += points
        else:
            wrong += 1
            points = -float(question["marks_incorrect"])
            score += points
        review.append(
            {
                "id": question_id,
                "question_text": question["question_text"],
                "options": question["options"],
                "image_url": question.get("image_url"),
                "chosen": chosen,
                "correct_answer": answer,
                "solution": question["solution"],
                "chapter": question["chapter"],
                "topic": question["topic"],
                "subject": question["subject"],
                "marks_awarded": points,
            }
        )
    maximum = sum(float(question["marks_correct"]) for question in questions)
    percentage = max(0.0, score) / maximum * 100 if maximum else 0.0
    attempted = correct + wrong
    accuracy = correct / attempted * 100 if attempted else 0.0
    return {
        "score": round(score, 2),
        "maximum": round(maximum, 2),
        "percentage": round(percentage, 2),
        "accuracy": round(accuracy, 2),
        "correct": correct,
        "wrong": wrong,
        "unattempted": unattempted,
        "review": review,
    }


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    if app.config.get("APP_ENV") == "production":
        required_production_settings = (
            "SECRET_KEY",
            "ADMIN_USERNAME",
            "ADMIN_PASSWORD_HASH",
            "SUPABASE_URL",
            "SUPABASE_SERVICE_ROLE_KEY",
        )
        missing = [
            key for key in required_production_settings if not app.config.get(key)
        ]
        if missing:
            raise RuntimeError(
                "Production requires configured environment variables: "
                + ", ".join(missing)
            )
        if len(app.config["SECRET_KEY"]) < 32:
            raise RuntimeError("Production FLASK_SECRET_KEY must be at least 32 characters.")
        if app.config.get("DATABASE_BACKEND") == "sqlite":
            raise RuntimeError("Public production deployment requires Supabase storage.")
        if app.config.get("DEBUG") or not app.config.get("SESSION_COOKIE_SECURE"):
            raise RuntimeError(
                "Production requires FLASK_DEBUG=false and SESSION_COOKIE_SECURE=true."
            )
        app.config["DATABASE_BACKEND"] = "supabase"
        app.wsgi_app = ProxyFix(
            app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
        )
    if app.config.get("MAX_CONTENT_LENGTH") is None:
        app.config["MAX_CONTENT_LENGTH"] = 3 * 1024 * 1024
    CSRFProtect(app)

    requested_backend = (
        str(app.config.get("DATABASE_BACKEND") or "auto").strip().lower() or "auto"
    )
    has_supabase_credentials = bool(
        app.config.get("SUPABASE_URL")
        and app.config.get("SUPABASE_SERVICE_ROLE_KEY")
    )
    if requested_backend not in {"auto", "sqlite", "supabase"}:
        raise ValueError(
            "DATABASE_BACKEND must be 'auto', 'sqlite', or 'supabase'."
        )
    if requested_backend == "sqlite" or (
        requested_backend in {"auto", "supabase"} and not has_supabase_credentials
    ):
        database_backend = "sqlite"
    else:
        database_backend = "supabase"
    app.config["DATABASE_BACKEND"] = database_backend

    if database_backend == "sqlite":
        app.extensions["database_client"] = SQLiteDatabase(
            app.config["SQLITE_DATABASE_PATH"]
        )
        if not app.config.get("ADMIN_USERNAME"):
            app.config["ADMIN_USERNAME"] = "admin"
        if not app.config.get("SECRET_KEY"):
            database = app.extensions["database_client"]
            secret_key = database.get_setting("flask_secret_key")
            if secret_key is None:
                secret_key = secrets.token_urlsafe(48)
                database.set_setting("flask_secret_key", secret_key)
            app.config["SECRET_KEY"] = secret_key
    else:
        from supabase import create_client

        app.extensions["database_client"] = create_client(
            app.config["SUPABASE_URL"], app.config["SUPABASE_SERVICE_ROLE_KEY"]
        )

    def db():
        client = app.extensions.get("supabase") or app.extensions.get("database_client")
        if client is None:
            raise RuntimeError(
                "Supabase is not configured. Set SUPABASE_URL and "
                "SUPABASE_SERVICE_ROLE_KEY in your .env file, or use "
                "DATABASE_BACKEND=sqlite for local testing."
            )
        return client

    def admin_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("admin_logged_in"):
                return redirect(url_for("admin_login", next=request.path))
            return view(*args, **kwargs)

        return wrapped

    @app.errorhandler(RuntimeError)
    def handle_configuration_error(error):
        app.logger.error("Application configuration error: %s", error)
        return render_template("error.html", message=str(error)), 503

    @app.get("/")
    def home():
        tests = db().table("tests").select("id,title,duration_minutes").eq(
            "is_published", True
        ).order("created_at", desc=True).execute().data
        return render_template("student.html", tests=tests or [])

    @app.get("/healthz")
    def health_check():
        return {"status": "ok"}, 200

    @app.post("/start")
    def start_test():
        active = session.get("active_test")
        if active:
            flash("Finish or submit your current test before starting another.", "error")
            return redirect(url_for("take_test"))
        name = (request.form.get("student_name") or "").strip()
        test_id = (request.form.get("test_id") or "").strip()
        if not name or len(name) > 100:
            flash("Enter your name (up to 100 characters).", "error")
            return redirect(url_for("home"))
        test = db().table("tests").select("*").eq("id", test_id).eq(
            "is_published", True
        ).maybe_single().execute().data
        if not test:
            flash("That test is unavailable.", "error")
            return redirect(url_for("home"))
        ids = test.get("question_ids") or []
        if not ids:
            flash("This test does not have any available questions.", "error")
            return redirect(url_for("home"))
        duration_minutes = int(test["duration_minutes"])
        session["student_name"] = name
        session["active_test"] = {
            "test_id": str(test["id"]),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "duration_minutes": duration_minutes,
        }
        return redirect(url_for("take_test"))

    @app.get("/test")
    def take_test():
        active = session.get("active_test")
        if not active or not session.get("student_name"):
            flash("Start a test before opening the test page.", "error")
            return redirect(url_for("home"))
        test = db().table("tests").select("*").eq(
            "id", active["test_id"]
        ).maybe_single().execute().data
        if not test:
            session.pop("active_test", None)
            flash("This test is no longer available.", "error")
            return redirect(url_for("home"))
        ids = [str(value) for value in (test.get("question_ids") or [])]
        questions = db().table("questions").select(
            "id,question_text,image_url,options,chapter,subject"
        ).in_("id", ids).execute().data if ids else []
        ordered = {str(question["id"]): question for question in (questions or [])}
        questions = [ordered[question_id] for question_id in ids if question_id in ordered]
        if not questions:
            session.pop("active_test", None)
            flash("This test does not have any available questions.", "error")
            return redirect(url_for("home"))
        try:
            started = datetime.fromisoformat(active["started_at"])
            duration = int(active["duration_minutes"])
            deadline = started + timedelta(minutes=duration)
        except (KeyError, TypeError, ValueError):
            abort(400, description="Invalid test start time.")
        remaining_seconds = max(
            0, int((deadline - datetime.now(timezone.utc)).total_seconds())
        )
        return render_template(
            "test.html",
            test=test,
            questions=questions,
            duration_seconds=remaining_seconds,
            attempt_key=active["started_at"],
        )

    @app.post("/submit")
    def submit_test():
        active = session.get("active_test")
        student_name = session.get("student_name")
        if not active or not student_name:
            flash("Start a test before submitting answers.", "error")
            return redirect(url_for("home"))
        test = db().table("tests").select("*").eq(
            "id", active["test_id"]
        ).maybe_single().execute().data
        if not test:
            abort(404)
        question_ids = [str(value) for value in (test.get("question_ids") or [])]
        questions = db().table("questions").select("*").in_(
            "id", question_ids
        ).execute().data or []
        ordered_questions = {str(question["id"]): question for question in questions}
        questions = [
            ordered_questions[question_id]
            for question_id in question_ids
            if question_id in ordered_questions
        ]
        try:
            started = datetime.fromisoformat(active["started_at"])
            deadline = started + timedelta(minutes=int(active["duration_minutes"]))
        except (KeyError, TypeError, ValueError):
            abort(400, description="Invalid test start time.")
        if datetime.now(timezone.utc) > deadline + timedelta(seconds=20):
            session.pop("active_test", None)
            flash("The time limit for this test has expired.", "error")
            return redirect(url_for("home"))
        answer_map = {
            question_id: request.form.get(f"answer_{question_id}", "")
            for question_id in question_ids
        }
        result = score_submission(questions, answer_map)
        answers = {entry["id"]: entry for entry in result["review"]}
        saved = db().table("attempts").insert(
            {
                "test_id": test["id"],
                "test_title": test["title"],
                "student_name": student_name,
                "answers": answers,
                "score": result["score"],
                "maximum_score": result["maximum"],
                "percentage": result["percentage"],
                "accuracy_percentage": result["accuracy"],
                "correct_count": result["correct"],
                "wrong_count": result["wrong"],
                "unattempted_count": result["unattempted"],
                "started_at": active["started_at"],
                "submitted_at": datetime.now(timezone.utc).isoformat(),
            }
        ).execute().data
        session.pop("active_test", None)
        return render_template(
            "result.html",
            test=test,
            student_name=student_name,
            result=result,
            attempt_id=saved[0]["id"] if saved else None,
        )

    @app.get("/admin/login")
    def admin_login():
        return render_template("login.html")

    @app.get("/admin/sample-questions.csv")
    @admin_required
    def sample_questions_csv():
        return send_file(
            os.path.join(app.root_path, "sample_questions.csv"),
            as_attachment=True,
            download_name="sample_questions.csv",
            mimetype="text/csv",
        )

    @app.post("/admin/login")
    def admin_login_post():
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        password_hash = app.config.get("ADMIN_PASSWORD_HASH", "")
        if (
            username == app.config.get("ADMIN_USERNAME")
            and password_hash
            and check_password_hash(password_hash, password)
        ):
            session.clear()
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Invalid admin username or password.", "error")
        return render_template("login.html"), 401

    @app.post("/admin/logout")
    @admin_required
    def admin_logout():
        session.clear()
        return redirect(url_for("admin_login"))

    @app.get("/admin")
    @admin_required
    def admin_dashboard():
        query = db().table("questions").select("*").order("year", desc=True)
        for field in ("exam", "year", "subject", "chapter", "topic"):
            value = request.args.get(field, "").strip()
            if value:
                if field == "year":
                    if not value.isdigit() or not 2002 <= int(value) <= datetime.now().year:
                        abort(400, description="Year filter must be a valid JEE year.")
                    value = int(value)
                query = query.eq(field, value)
        search = request.args.get("q", "").strip()
        if search:
            safe_search = search.replace("%", "").replace(",", " ")
            query = query.ilike("question_text", f"%{safe_search}%")
        questions = query.limit(300).execute().data or []
        tests = db().table("tests").select("*").order("created_at", desc=True).execute().data or []
        attempts = db().table("attempts").select(
            "*"
        ).order("submitted_at", desc=True).limit(300).execute().data or []
        return render_template(
            "admin.html",
            questions=questions,
            tests=tests,
            attempts=attempts,
            filters=request.args,
        )

    @app.route("/admin/questions/new", methods=["GET", "POST"])
    @app.route("/admin/questions/<question_id>/edit", methods=["GET", "POST"])
    @admin_required
    def question_form(question_id=None):
        question = None
        if question_id:
            question = db().table("questions").select("*").eq(
                "id", question_id
            ).maybe_single().execute().data
            if not question:
                abort(404)
        if request.method == "POST":
            data, errors = validate_question(request.form)
            if not errors:
                if question_id:
                    data["updated_at"] = datetime.now(timezone.utc).isoformat()
                    db().table("questions").update(data).eq("id", question_id).execute()
                    flash("Question updated.", "success")
                else:
                    db().table("questions").insert(data).execute()
                    flash("Question added.", "success")
                return redirect(url_for("admin_dashboard"))
            for error in errors:
                flash(error, "error")
        return render_template(
            "question_form.html",
            question=question,
            csv_fields=CSV_FIELDS,
        )

    @app.post("/admin/questions/<question_id>/delete")
    @admin_required
    def delete_question(question_id):
        tests = db().table("tests").select("id,question_ids").execute().data or []
        in_use = any(
            question_id in {str(value) for value in (test.get("question_ids") or [])}
            for test in tests
        )
        if in_use:
            flash(
                "This question is included in a custom test. Remove it from each test before deleting.",
                "error",
            )
        else:
            db().table("questions").delete().eq("id", question_id).execute()
            flash("Question deleted.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/import", methods=["GET", "POST"])
    @admin_required
    def import_questions():
        preview = False
        errors = []
        count = 0
        duplicate_count = 0
        if request.method == "POST":
            upload = request.files.get("file")
            if not upload or not upload.filename.lower().endswith(".csv"):
                errors = ["Choose a CSV file."]
            else:
                content = upload.stream.read(MAX_IMPORT_BYTES + 1)
                if len(content) > MAX_IMPORT_BYTES:
                    errors = ["CSV file must be 2 MB or smaller."]
                else:
                    questions, errors = parse_csv(content)
                    preview = True
                    upload_digest = hashlib.sha256(content).hexdigest()
                    if request.form.get("confirm") == "yes":
                        if not errors and session.get("pending_import_sha256") == upload_digest:
                            client = db()
                            new_questions, duplicate_count = filter_existing_questions(
                                client, questions
                            )
                            count = len(new_questions)
                            if new_questions:
                                client.table("questions").insert(new_questions).execute()
                                verified_questions = (
                                    client.table("questions")
                                    .select("subject,chapter,question_text,options")
                                    .in_(
                                        "question_text",
                                        list(dict.fromkeys(
                                            item["question_text"] for item in new_questions
                                        )),
                                    )
                                    .execute()
                                    .data
                                    or []
                                )
                                verified_keys = {
                                    question_fingerprint(item)
                                    for item in verified_questions
                                }
                                expected_keys = {
                                    question_fingerprint(item) for item in new_questions
                                }
                                if not expected_keys.issubset(verified_keys):
                                    raise RuntimeError(
                                        "Import verification failed: not all questions "
                                        "could be read back from Supabase."
                                    )
                            session.pop("pending_import_sha256", None)
                            flash(
                                f"Verified {count} newly imported question(s); "
                                f"skipped {duplicate_count} duplicate(s).",
                                "success",
                            )
                            return redirect(url_for("admin_dashboard"))
                        if not errors:
                            errors.append("This file differs from the validated preview. Validate it before importing.")
                        session.pop("pending_import_sha256", None)
                    elif not errors:
                        questions, duplicate_count = filter_existing_questions(
                            db(), questions
                        )
                        count = len(questions)
                        session["pending_import_sha256"] = upload_digest
                    else:
                        session.pop("pending_import_sha256", None)
        return render_template(
            "import_questions.html",
            preview=preview,
            errors=errors,
            count=count,
            duplicate_count=duplicate_count,
        )

    @app.route("/admin/tests/new", methods=["GET", "POST"])
    @app.route("/admin/tests/<test_id>/edit", methods=["GET", "POST"])
    @admin_required
    def create_test(test_id=None):
        test = None
        if test_id:
            test = db().table("tests").select("*").eq(
                "id", test_id
            ).maybe_single().execute().data
            if not test:
                abort(404)
        if request.method == "POST":
            title = (request.form.get("title") or "").strip()
            question_ids = request.form.getlist("question_ids")
            try:
                duration = int(request.form.get("duration_minutes", ""))
                if not 1 <= duration <= 600:
                    raise ValueError
            except ValueError:
                duration = None
            if not title or len(title) > 150 or not question_ids or duration is None:
                flash("Provide a title, select questions, and enter a duration (1-600 minutes).", "error")
            else:
                existing = db().table("questions").select("id").in_(
                    "id", question_ids
                ).execute().data or []
                valid_ids = {str(row["id"]) for row in existing}
                chosen_ids = list(dict.fromkeys(item for item in question_ids if item in valid_ids))
                if not chosen_ids:
                    flash("Select at least one valid question.", "error")
                else:
                    data = {
                        "title": title,
                        "duration_minutes": duration,
                        "question_ids": chosen_ids,
                        "is_published": request.form.get("is_published") == "on",
                    }
                    if test_id:
                        db().table("tests").update(data).eq("id", test_id).execute()
                        flash("Test updated.", "success")
                    else:
                        db().table("tests").insert(data).execute()
                        flash("Custom test created.", "success")
                    return redirect(url_for("admin_dashboard"))
        questions = db().table("questions").select(
            "id,exam,year,session,subject,chapter,topic,question_text"
        ).order("year", desc=True).limit(500).execute().data or []
        selected_ids = [
            str(value)
            for value in request.form.getlist("question_ids")
        ] if request.method == "POST" else [
            str(value) for value in (test.get("question_ids") or [])
        ] if test else []
        return render_template(
            "create_test.html",
            questions=questions,
            test=test,
            selected_ids=selected_ids,
        )

    @app.post("/admin/tests/<test_id>/delete")
    @admin_required
    def delete_test(test_id):
        existing_attempt = db().table("attempts").select("id").eq(
            "test_id", test_id
        ).limit(1).execute().data
        if existing_attempt:
            flash("This test has attempt history and cannot be deleted.", "error")
        else:
            db().table("tests").delete().eq("id", test_id).execute()
            flash("Test deleted.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.get("/admin/attempts/<attempt_id>")
    @admin_required
    def attempt_detail(attempt_id):
        attempt = db().table("attempts").select(
            "*"
        ).eq("id", attempt_id).maybe_single().execute().data
        if not attempt:
            abort(404)
        answers = attempt.get("answers") or {}
        review = []
        for answer in answers.values():
            review.append(
                {
                    **answer,
                    "chosen": answer.get("chosen", ""),
                    "correct_answer": answer.get("correct_answer", ""),
                    "is_correct": bool(answer.get("chosen"))
                    and answer.get("chosen") == answer.get("correct_answer"),
                }
            )
        chapter_counts = {}
        topic_counts = {}
        for item in review:
            chapter = item["chapter"]
            values = chapter_counts.setdefault(chapter, {"correct": 0, "total": 0})
            values["total"] += 1
            values["correct"] += int(item["is_correct"])
            topic = f"{item['subject']} · {item['chapter']} / {item['topic']}"
            topic_values = topic_counts.setdefault(topic, {"correct": 0, "total": 0})
            topic_values["total"] += 1
            topic_values["correct"] += int(item["is_correct"])
        weak_topics = sorted(
            topic_counts.items(),
            key=lambda item: item[1]["correct"] / item[1]["total"] if item[1]["total"] else 0,
        )
        return render_template(
            "attempt_detail.html",
            attempt=attempt,
            review=review,
            chapter_counts=chapter_counts,
            weak_topics=weak_topics,
        )

    return app


app = create_app()

if __name__ == "__main__":
    required_settings = {
        "FLASK_SECRET_KEY": app.config.get("SECRET_KEY"),
    }
    if app.config.get("DATABASE_BACKEND") == "supabase":
        required_settings.update(
            {
                "ADMIN_PASSWORD_HASH": app.config.get("ADMIN_PASSWORD_HASH"),
            }
        )
    missing_settings = [name for name, value in required_settings.items() if not value]
    if missing_settings:
        raise SystemExit(
            "Configure these required values in .env before starting Flask: "
            + ", ".join(missing_settings)
        )
    if app.config["DATABASE_BACKEND"] == "sqlite" and not app.config.get(
        "ADMIN_PASSWORD_HASH"
    ):
        configure_local_admin(app)
    app.run(
        host="127.0.0.1" if app.config.get("APP_ENV") != "production" else "0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=app.config.get("DEBUG", False),
    )
