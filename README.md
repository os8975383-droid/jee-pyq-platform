# JEE PYQ Practice Platform

A Flask and Supabase application for managing an authorized JEE Main / JEE Advanced question bank, assembling timed practice tests, and reviewing saved student attempts. This project does not include or claim to provide authentic Previous Year Questions: only add question content that you wrote or are authorized to use.

## Project structure

```text
jee-pyq-platform/
├── app.py                       # Flask routes, validation, scoring, admin access
├── config.py                    # Environment-backed configuration
├── database.py                  # Local SQLite adapter for existing database calls
├── migrate_sqlite_to_supabase.py # Non-destructive local-to-cloud data migration
├── render.yaml                  # Render web-service deployment configuration
├── requirements.txt             # Python dependencies
├── .env.example                 # Safe configuration template
├── .gitignore                   # Excludes local secrets and Python artifacts
├── schema.sql                   # Supabase tables, indexes, and RLS policies
├── sample_questions.csv         # 30 supplied practice questions (not verified PYQs)
├── static/
│   ├── css/style.css            # Responsive student and admin styling
│   ├── js/test.js               # Question navigation and countdown display
│   └── sample_questions.csv     # Same import-ready 30-question CSV
├── templates/
│   ├── base.html                # Shared layout
│   ├── student.html             # Student test selection
│   ├── test.html                # Timed question interface
│   ├── result.html              # Score and post-submission solutions
│   ├── login.html               # Secure admin login
│   ├── admin.html               # Question bank, tests, and attempt history
│   ├── question_form.html       # Add/edit question
│   ├── import_questions.html    # CSV validation and import confirmation
│   ├── create_test.html         # Custom test builder
│   ├── attempt_detail.html      # Chapter results and answer review
│   └── error.html               # Configuration error page
└── tests/
    └── test_app.py              # Scoring, validation, and access tests
```

## Setup on Windows in VS Code

### 1. Install Python and open the folder

Install Python 3.10 or newer and the Python extension in VS Code. Open this project folder, then open **Terminal → New Terminal** and use PowerShell.

### 2. Create a virtual environment and install packages

```powershell
py -3 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

In VS Code, choose the interpreter at `.venv\Scripts\python.exe` if it is not selected automatically.

### 3. Create the Supabase database

1. Create a Supabase project and open its **SQL Editor**.
2. Paste the complete contents of `schema.sql` into a new query and run it.
3. In the Supabase project settings, copy the project URL and the server-side `service_role` key. The service-role credential bypasses row-level security and must stay on the Flask server.
4. Do not put that key in HTML, JavaScript, a public repository, or a `NEXT_PUBLIC_`/browser variable. Never share or commit your `.env` file.

The included policies deny access to `anon` and `authenticated` browser roles. Flask uses the service-role key to perform database operations. Keep that key private and deploy the Flask app only over HTTPS.

## Public deployment (Render + Supabase)

Render runs the Flask site publicly; Supabase provides persistent online PostgreSQL storage so your brother's attempts can be opened from your laptop. Render's local filesystem is not used for question or result storage.

1. **Create Supabase and prepare the schema.** Create a Supabase project and run `schema.sql` in its SQL Editor. Copy the project URL and server-side service-role key for server environment variables only.
2. **Back up local data and migrate it before switching traffic.** Stop the local Flask app. Make a backup copy of `instance\jee_exam.sqlite3` in a safe location (do not commit it). In your local `.env`, set your Supabase URL and service-role key, then run:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   python migrate_sqlite_to_supabase.py --sqlite-path instance\jee_exam.sqlite3 --dry-run
   python migrate_sqlite_to_supabase.py --sqlite-path instance\jee_exam.sqlite3 --confirm
   ```

   Review the dry-run counts before the second command. The script migrates questions, tests, and attempts, keeps UUIDs, deterministically converts any old non-UUID IDs and their references, skips IDs already present, never overwrites existing rows, and verifies migrated IDs after insertion. Keep the SQLite file until you have confirmed tests and attempt history in Supabase.
3. **Create a GitHub repository** for this existing project and push the project files. `.env`, `instance\`, and SQLite database files are ignored; do not force-add them.
4. **Create the Render web service.** In Render, choose **New → Blueprint** and connect the repository containing `render.yaml`. Render reads the build and Gunicorn start commands from that file. The site is mobile responsive and the blueprint includes an HTTP health check.
5. **Set the service environment variables in Render → Environment.** Add these values using your own private credentials:

   | Variable | Value |
   | --- | --- |
   | `APP_ENV` | `production` |
   | `DATABASE_BACKEND` | `supabase` |
   | `FLASK_DEBUG` | `false` |
   | `SESSION_COOKIE_SECURE` | `true` |
   | `FLASK_SECRET_KEY` | Generate a new random value with `python -c "import secrets; print(secrets.token_hex(32))"` |
   | `ADMIN_USERNAME` | A username you choose |
   | `ADMIN_PASSWORD_HASH` | Generate locally using the Werkzeug command below |
   | `SUPABASE_URL` | Your Supabase project URL |
   | `SUPABASE_SERVICE_ROLE_KEY` | Your private Supabase service-role key |

   Generate the admin hash in your activated local virtual environment; enter the password at the hidden prompt and copy only the printed hash into Render:

   ```powershell
   python -c "from getpass import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass('Admin password: ')))"
   ```

   Do not put production secrets in `render.yaml`, Git, source files, browser code, or chat. The app refuses production startup if required configuration is absent, uses a strong session secret, requires HTTPS secure cookies, disables debug mode, and requires Supabase rather than ephemeral SQLite.
6. **Deploy and test.** Wait for the Render deployment to become live. Open the Render-provided HTTPS URL on your phone and computer. The student page is `/`; the admin sign-in is `/admin/login`. Create or confirm a published test, submit a test from your brother's phone, then sign into the admin dashboard on your laptop and check the attempt history.

The Render deployment is not complete until you configure the real Supabase/Render variables and the service reports healthy. Do not use SQLite mode for public hosting: its local database would not be shared across machines and may be lost on redeploy.

### Local SQLite mode (default without Supabase credentials)

When `SUPABASE_URL` or `SUPABASE_SERVICE_ROLE_KEY` is missing, the app automatically uses SQLite unless you explicitly set `DATABASE_BACKEND=sqlite` or `DATABASE_BACKEND=auto`. You do not need to edit `.env` to remove Supabase placeholders.

1. The app retains an existing `FLASK_SECRET_KEY`; if none is configured, local SQLite mode generates and persists one in its settings table. If an `ADMIN_PASSWORD_HASH` is already present, the app uses it.
2. To make local mode explicit, add or change this setting in `.env`:

   ```dotenv
   DATABASE_BACKEND=sqlite
   SQLITE_DATABASE_PATH=instance/jee_exam.sqlite3
   ```

3. Save `.env`. In SQLite mode, Supabase credentials and `ADMIN_PASSWORD_HASH` are not required. If no password hash is configured, the first `python app.py` startup generates a strong random admin password, displays it once in the terminal, and stores only its Werkzeug hash in the local SQLite settings table. Save that password; subsequent starts reuse the stored hash without displaying the password again. Flask creates the `instance` folder and tables automatically. The database persists at `instance\jee_exam.sqlite3`; it is ignored by Git and should not be deleted if you need to keep local questions, tests, and attempts.
4. In the VS Code PowerShell terminal, start the app:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   python app.py
   ```

5. Open <http://127.0.0.1:5000/> for student tests or <http://127.0.0.1:5000/admin/login> for the admin portal.

When both Supabase credentials are present, `auto` selects Supabase; otherwise it selects SQLite. Explicit `DATABASE_BACKEND=sqlite` always selects local SQLite. The SQLite database is a separate local store and does not copy or modify Supabase records.

### 4. Configure the application

If you do not already have a `.env`, create one from the example:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Generate a strong Flask signing key:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Generate an administrator password hash. Enter a strong password when prompted; the password itself is not printed:

```powershell
python -c "from getpass import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass()))"
```

Edit `.env` and set:

* `FLASK_SECRET_KEY` to the generated random value.
* `ADMIN_USERNAME` and `ADMIN_PASSWORD_HASH` to the admin login values.
* `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` to the private project credentials.
* Keep `FLASK_DEBUG=false`. `SESSION_COOKIE_SECURE=false` is only for local HTTP testing; set it to `true` behind HTTPS in production.

Do not use the placeholder values in `.env.example` for a real deployment.

### 5. Start Flask

With `.venv` activated:

```powershell
python app.py
```

Open <http://127.0.0.1:5000/> for student tests and <http://127.0.0.1:5000/admin/login> for administration. The login page prominently displays **Created by Om Singh**.

### 6. Run tests

```powershell
pytest -q
```

The automated tests cover question and CSV validation, server-side scoring (including negative marks), and admin route protection. To verify the full database-backed workflow, configure Supabase, add questions in the admin portal, create and publish a test, and submit a student attempt from another browser/device.

## CSV import

The root `sample_questions.csv` contains the 30 practice questions supplied for this project. They are not represented as verified official PYQs. The CSV leaves exam, year, session/shift, topic, and unprovided explanations blank. Correct answer indices from the supplied JSON are mapped to the bank's A/B/C/D format; the projectile item is corrected to **B (2 s)**. Since no marking scheme was supplied, each row uses the existing app defaults of +4 and −1. You can download this exact CSV from the protected admin import page.

For future imports, use the same UTF-8 headers:

| Column | Required / accepted values |
| --- | --- |
| `exam` | Optional: `JEE Main` or `JEE Advanced` |
| `year` | Optional: integer from 2002 through the current year |
| `session` | Optional; do not enter an unverified shift |
| `subject` | `Physics`, `Chemistry`, or `Mathematics` |
| `chapter` | Required classification |
| `topic` | Optional classification |
| `question_text` | Original or authorized text; LaTeX delimiters are supported |
| `image_url` | Optional `http://` or `https://` image/diagram URL |
| `option_a` … `option_d` | All four choices are required |
| `correct_answer` | `A`, `B`, `C`, or `D` |
| `solution` | Optional; provide only an authorized/verified explanation |
| `marks_correct` | Positive number up to 100 |
| `marks_incorrect` | Number from 0 through 100; deducted for a wrong answer |

CSV imports are capped at 2 MB and 500 rows. The first upload validates all rows and checks for existing content duplicates without saving. Duplicate questions (same subject, chapter, question text, and options) are skipped. Only a fully valid CSV can be uploaded again for explicit confirmation; after insertion, the app reads the questions back from Supabase and reports success only after verifying them.

## Security and scoring notes

* Admin mutations use session authentication and CSRF protection. Set a strong password hash and Flask secret key before use.
* The student question endpoint never selects or sends the correct answer. Correct choices and explanations are returned only after submission.
* The server reloads the selected test questions and computes marks from their stored marking schemes; browser values cannot set marks.
* The browser countdown is paired with a server-side submission deadline check. A short 20-second allowance accommodates network delay.
* Student names and attempts are stored in Supabase so attempt history can be reviewed across devices. The app currently does not provide student accounts; use non-sensitive display names.
* Question images are referenced by URL rather than uploaded to Supabase Storage.
* This starter app has no production account recovery, multi-admin roles, or deployment-specific rate limiting. Add those controls and use HTTPS before exposing it publicly.
