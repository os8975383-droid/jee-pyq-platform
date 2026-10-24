-- Run this script in Supabase SQL Editor before starting the Flask app.
-- The service-role key is used only by Flask on the server and bypasses RLS.
create extension if not exists pgcrypto;

create table if not exists public.questions (
    id uuid primary key default gen_random_uuid(),
    exam text check (exam in ('JEE Main', 'JEE Advanced')),
    year integer check (year between 2002 and 2100),
    session text,
    subject text not null check (subject in ('Physics', 'Chemistry', 'Mathematics')),
    chapter text not null,
    topic text,
    question_text text not null,
    image_url text,
    options jsonb not null check (
        options ? 'A' and options ? 'B' and options ? 'C' and options ? 'D'
    ),
    correct_answer text not null check (correct_answer in ('A', 'B', 'C', 'D')),
    solution text,
    marks_correct numeric(7, 2) not null check (marks_correct > 0),
    marks_incorrect numeric(7, 2) not null check (marks_incorrect >= 0),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- Practice questions may not have an official exam, year, session, topic, or solution.
alter table public.questions alter column exam drop not null;
alter table public.questions alter column year drop not null;
alter table public.questions alter column session drop not null;
alter table public.questions alter column topic drop not null;
alter table public.questions alter column solution drop not null;

create index if not exists questions_search_idx on public.questions
    (exam, year, subject, chapter, topic);

create table if not exists public.tests (
    id uuid primary key default gen_random_uuid(),
    title text not null,
    duration_minutes integer not null check (duration_minutes between 1 and 600),
    question_ids jsonb not null default '[]'::jsonb,
    is_published boolean not null default false,
    created_at timestamptz not null default now()
);

create table if not exists public.attempts (
    id uuid primary key default gen_random_uuid(),
    test_id uuid not null references public.tests(id) on delete restrict,
    test_title text not null,
    student_name text not null,
    answers jsonb not null default '{}'::jsonb,
    score numeric(9, 2) not null,
    maximum_score numeric(9, 2) not null,
    percentage numeric(7, 2) not null,
    accuracy_percentage numeric(7, 2) not null check (accuracy_percentage between 0 and 100),
    correct_count integer not null check (correct_count >= 0),
    wrong_count integer not null check (wrong_count >= 0),
    unattempted_count integer not null check (unattempted_count >= 0),
    started_at timestamptz not null,
    submitted_at timestamptz not null default now()
);

alter table public.attempts add column if not exists test_title text not null default 'Test';
alter table public.attempts add column if not exists accuracy_percentage
    numeric(7, 2) not null default 0 check (accuracy_percentage between 0 and 100);

create index if not exists attempts_test_submitted_idx
    on public.attempts (test_id, submitted_at desc);
create index if not exists attempts_student_idx
    on public.attempts (student_name);

-- Deny browser/anonymous access. The Flask backend uses the service-role key.
alter table public.questions enable row level security;
alter table public.tests enable row level security;
alter table public.attempts enable row level security;

drop policy if exists "No public question access" on public.questions;
drop policy if exists "No public test access" on public.tests;
drop policy if exists "No public attempt access" on public.attempts;

create policy "No public question access" on public.questions
    for all to anon, authenticated using (false) with check (false);
create policy "No public test access" on public.tests
    for all to anon, authenticated using (false) with check (false);
create policy "No public attempt access" on public.attempts
    for all to anon, authenticated using (false) with check (false);

grant usage on schema public to service_role;
grant all on public.questions, public.tests, public.attempts to service_role;
