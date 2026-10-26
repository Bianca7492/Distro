-- Emulates the parts of Supabase that a migration depends on, so the schema
-- can be applied and tested against a plain Postgres. Supabase provides these
-- already; this file is never run against a real project.
--
--   auth.users   the identity table `artists.auth_user_id` references
--   auth.uid()   returns the caller's user id; reads a session setting so a
--                test can impersonate a user without a real JWT
--   auth.role()  the Postgres role Supabase sets for the request
--
-- Impersonation in tests:
--   set local request.jwt.claims = '{"sub":"<uuid>"}';

create schema if not exists auth;

create table if not exists auth.users (
    id                 uuid primary key default gen_random_uuid(),
    email              text unique,
    encrypted_password text,
    created_at         timestamptz not null default now()
);

create or replace function auth.uid()
returns uuid
language sql
stable
as $$
    select nullif(
        coalesce(
            current_setting('request.jwt.claim.sub', true),
            (nullif(current_setting('request.jwt.claims', true), ''))::jsonb ->> 'sub'
        ),
        ''
    )::uuid;
$$;

create or replace function auth.role()
returns text
language sql
stable
as $$
    select coalesce(
        nullif(current_setting('request.jwt.claim.role', true), ''),
        'authenticated'
    );
$$;

-- Supabase grants these roles to the API. Create them if absent so the
-- migration's grant block runs for real.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'anon') then
        create role anon nologin;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'authenticated') then
        create role authenticated nologin;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'service_role') then
        create role service_role nologin bypassrls;
    end if;
end;
$$;

grant usage on schema auth to anon, authenticated, service_role;
