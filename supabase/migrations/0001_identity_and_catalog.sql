-- ============================================================================
-- 0001 — Identity and Catalog
-- Stage 2 (S2-01, S2-02, S2-06). Also carries the Stage 1 revenue tables so
-- that one migration brings the whole database up.
--
-- Design rules enforced by this schema:
--   1. We never mint ISRCs. ISRC prefix codes are allocated by the ISRC Agency;
--      self-generated codes are invalid. `isrc` is an optional attribute that a
--      rights holder supplies. The primary key is always our own internal ID.
--   2. Money is integer minor units plus a currency code. Never a float.
--   3. Every table has RLS enabled. Artists see only their own rows.
--   4. Nothing is deleted; corrections are new rows (audit is append-only).
--
-- Order: extensions, types, helper functions, tables, triggers, RLS, grants.
-- ============================================================================


-- ---------------------------------------------------------------------------
-- 1. Extensions
-- ---------------------------------------------------------------------------

create extension if not exists "pgcrypto";      -- gen_random_uuid()
create extension if not exists "citext";        -- case-insensitive email/handle
create extension if not exists "pg_trgm";       -- fuzzy title/artist matching


-- ---------------------------------------------------------------------------
-- 2. Enumerated types
-- ---------------------------------------------------------------------------

create type artist_role as enum ('artist', 'label', 'admin', 'service');

create type track_status as enum (
    'draft',        -- metadata incomplete, not deliverable
    'ready',        -- metadata complete, awaiting delivery
    'delivered',    -- ERN sent to at least one DSP
    'live',         -- DSP confirmed it is live
    'withdrawn',    -- pulled from DSPs
    'blocked'       -- held for rights or quality review
);

create type release_status as enum (
    'draft', 'ready', 'delivered', 'live', 'withdrawn', 'blocked'
);

create type statement_status as enum (
    'received', 'parsed', 'reconciled', 'disputed', 'closed'
);

create type match_status as enum (
    'pending',      -- not yet matched
    'matched',      -- exact match on ISRC / UPC / fingerprint
    'ambiguous',    -- fuzzy match, needs human review
    'unmatched',    -- no candidate
    'rejected'      -- reviewer rejected the candidate
);

create type match_method as enum (
    'isrc', 'upc', 'fingerprint', 'fuzzy', 'manual'
);

create type credential_status as enum (
    'issued', 'revoked', 'expired', 'suspended'
);


-- ---------------------------------------------------------------------------
-- 3. Helper functions
-- ---------------------------------------------------------------------------

create or replace function set_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;


-- Internal ID scheme (S2-06)
--
--   BOE-<KIND>-<SEQ>-<CHECK>
--     BOE    fixed prefix, marks a platform-internal identifier
--     KIND   TRK track, REL release, WRK work, ART artist
--     SEQ    10-digit zero-padded sequence, per kind
--     CHECK  2-character base32 check over prefix+kind+seq
--
-- The sequence is per kind so IDs stay short and readable. The check suffix
-- means a truncated or mistyped ID is rejected locally instead of silently
-- resolving to a different row.
--
-- These are NOT ISRCs. We never mint ISRCs.

-- Crockford base32: no I, L, O or U, so IDs cannot be misread.
create or replace function boe_check_suffix(p_body text)
returns text
language plpgsql
immutable
as $$
declare
    alphabet constant text := '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
    h        bigint := 2166136261;   -- FNV-1a offset basis
    i        int;
begin
    for i in 1..length(p_body) loop
        h := (h # ascii(substr(p_body, i, 1))) & 4294967295;
        h := (h * 16777619) & 4294967295;
    end loop;
    -- Cast to int: `h >> 5` is bigint, and there is no
    -- substr(text, bigint, int) overload.
    return substr(alphabet, (((h >> 5) % 32) + 1)::int, 1)
        || substr(alphabet, ((h % 32) + 1)::int, 1);
end;
$$;

-- SECURITY DEFINER: the caller has no grant on id_sequences, and must not need
-- one. This runs as the function owner, so the counter is incremented on the
-- caller's behalf. Without this, an artist insert fails on the trigger.
create or replace function next_internal_id(p_kind text)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
    v_seq  bigint;
    v_body text;
begin
    if p_kind not in ('TRK', 'REL', 'WRK', 'ART') then
        raise exception 'unknown internal id kind: %', p_kind
            using errcode = 'check_violation';
    end if;

    update id_sequences
       set next_value = next_value + 1,
           updated_at = now()
     where kind = p_kind
    returning next_value - 1 into v_seq;

    if v_seq is null then
        raise exception 'no id sequence row for kind %', p_kind;
    end if;

    v_body := 'BOE-' || p_kind || '-' || lpad(v_seq::text, 10, '0');
    return v_body || '-' || boe_check_suffix(v_body);
end;
$$;

-- Verify a well-formed internal ID, including its check suffix.
create or replace function is_valid_internal_id(p_id text)
returns boolean
language sql
immutable
as $$
    select p_id ~ '^BOE-(TRK|REL|WRK|ART)-[0-9]{10}-[0-9A-Z]{2}$'
       and right(p_id, 2) = boe_check_suffix(left(p_id, length(p_id) - 3));
$$;

-- Assign an internal ID automatically so no application path can forget to.
-- The kind is passed as a trigger argument, e.g. assign_internal_id('TRK').
create or replace function assign_internal_id()
returns trigger
language plpgsql
as $$
begin
    if new.internal_id is null or new.internal_id = '' then
        new.internal_id := next_internal_id(tg_argv[0]);
    end if;
    return new;
end;
$$;


-- ---------------------------------------------------------------------------
-- 4. Tables
-- ---------------------------------------------------------------------------

-- Counter table for the internal ID scheme. Plain bookkeeping, not user data.
create table id_sequences (
    kind        text primary key,
    next_value  bigint not null default 1,
    updated_at  timestamptz not null default now()
);

insert into id_sequences (kind, next_value) values
    ('TRK', 1), ('REL', 1), ('WRK', 1), ('ART', 1);


-- artists -------------------------------------------------------------------
-- The platform account. `auth_user_id` links to Supabase Auth; RLS keys off it.

create table artists (
    id                uuid primary key default gen_random_uuid(),
    auth_user_id      uuid unique references auth.users (id) on delete set null,
    internal_id       text unique,
    handle            citext unique not null,
    display_name      text not null,
    legal_name        text,
    email             citext,
    country           char(2),
    role              artist_role not null default 'artist',
    -- Identity reference for the credential. Opaque to us: a DID, a tax id
    -- reference, or an internal KYC record id. Never a password or a secret.
    identity_ref      text,
    is_verified       boolean not null default false,
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now()
);

create index ix_artists_auth_user on artists (auth_user_id);
create index ix_artists_handle on artists (handle);


-- works ---------------------------------------------------------------------
-- A composition (the song), distinct from a recording. ISWC is optional and
-- never minted by us.

create table works (
    id                uuid primary key default gen_random_uuid(),
    owner_artist_id   uuid not null references artists (id) on delete restrict,
    internal_id       text unique,
    title             text not null,
    subtitle          text,
    iswc              text,          -- optional, supplied by rights holder
    language          char(3),
    -- Writers and their shares, as declared. Shape validated in application
    -- code: [{name, role, share_bps}]. bps = basis points (10000 = 100%).
    contributors      jsonb not null default '[]'::jsonb,
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now(),

    constraint works_iswc_format
        check (iswc is null or iswc ~ '^T[0-9]{9}[0-9]$'),
    constraint works_contributors_is_array
        check (jsonb_typeof(contributors) = 'array')
);

create index ix_works_owner on works (owner_artist_id);
create index ix_works_title_trgm on works using gin (title gin_trgm_ops);


-- releases ------------------------------------------------------------------

create table releases (
    id                uuid primary key default gen_random_uuid(),
    owner_artist_id   uuid not null references artists (id) on delete restrict,
    internal_id       text unique,
    title             text not null,
    release_type      text not null default 'single',
    -- UPC/EAN is supplied by the artist or their registrant. We do not mint it.
    upc               text,
    release_date      date,
    original_release_date date,
    label_name        text,
    catalog_number    text,
    artwork_uri       text,
    status            release_status not null default 'draft',
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now(),

    constraint releases_upc_format
        check (upc is null or upc ~ '^[0-9]{12,14}$')
);

create index ix_releases_owner on releases (owner_artist_id);
create index ix_releases_status on releases (status);


-- tracks --------------------------------------------------------------------
-- The recording. `isrc` is optional and never minted by this platform: ISRC
-- prefix codes are allocated by the ISRC Agency and self-generated codes are
-- invalid. The primary key is `internal_id`.
--
-- `fingerprint` is the chromaprint fingerprint, used for dedup and for
-- matching statement lines that carry no ISRC.

create table tracks (
    id                uuid primary key default gen_random_uuid(),
    owner_artist_id   uuid not null references artists (id) on delete restrict,
    work_id           uuid references works (id) on delete set null,
    release_id        uuid references releases (id) on delete set null,
    internal_id       text unique,
    title             text not null,
    display_artist    text not null,
    version_subtitle  text,
    isrc              text,          -- optional, supplied by rights holder
    duration_ms       integer,
    track_number      integer,
    disc_number       integer default 1,
    genre             text,
    language          char(3),
    explicit          boolean not null default false,
    audio_uri         text,
    audio_sha256      text,
    fingerprint       text,
    fingerprint_algo  text default 'chromaprint',
    -- Splits: [{party, role, share_bps}]. bps must sum to 10000 for delivery.
    splits            jsonb not null default '[]'::jsonb,
    status            track_status not null default 'draft',
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now(),

    -- An ISRC is CC-XXX-YY-NNNNN: 2-char country, 3-char registrant, 2-digit
    -- year, 5-digit designation.
    constraint tracks_isrc_format
        check (isrc is null or isrc ~ '^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$'),
    constraint tracks_duration_positive
        check (duration_ms is null or duration_ms > 0),
    constraint tracks_splits_is_array
        check (jsonb_typeof(splits) = 'array')
);

create index ix_tracks_owner on tracks (owner_artist_id);
create index ix_tracks_release on tracks (release_id);
create index ix_tracks_work on tracks (work_id);
create index ix_tracks_isrc on tracks (isrc) where isrc is not null;
create index ix_tracks_fingerprint on tracks (fingerprint)
    where fingerprint is not null;
create index ix_tracks_title_trgm on tracks using gin (title gin_trgm_ops);
create index ix_tracks_artist_trgm
    on tracks using gin (display_artist gin_trgm_ops);


-- uploads -------------------------------------------------------------------
-- Every uploaded master. Content-addressed by sha256 so a re-upload of the
-- same bytes is detected without reading the audio again.

create table uploads (
    id                uuid primary key default gen_random_uuid(),
    owner_artist_id   uuid not null references artists (id) on delete restrict,
    track_id          uuid references tracks (id) on delete set null,
    filename          text not null,
    content_type      text,
    byte_size         bigint not null,
    sha256            text not null,
    storage_uri       text not null,
    -- 'pending' | 'scanning' | 'accepted' | 'rejected'
    scan_status       text not null default 'pending',
    scan_detail       text,
    created_at        timestamptz not null default now(),

    constraint uploads_size_positive check (byte_size > 0),
    constraint uploads_sha256_format check (sha256 ~ '^[0-9a-f]{64}$')
);

create index ix_uploads_owner on uploads (owner_artist_id);
create index ix_uploads_sha256 on uploads (sha256);
create index ix_uploads_track on uploads (track_id);


-- duplicate_candidates ------------------------------------------------------
-- A track is a duplicate of another when the fingerprint matches exactly, or
-- when title and artist are close enough to need review.

create table duplicate_candidates (
    id                uuid primary key default gen_random_uuid(),
    owner_artist_id   uuid not null references artists (id) on delete restrict,
    track_id          uuid not null references tracks (id) on delete cascade,
    candidate_track_id uuid not null references tracks (id) on delete cascade,
    method            match_method not null,
    score             numeric(5,4) not null,
    -- 'pending' | 'confirmed' | 'dismissed'
    resolution        text not null default 'pending',
    resolved_by       uuid references artists (id) on delete set null,
    resolved_at       timestamptz,
    created_at        timestamptz not null default now(),

    constraint dup_not_self check (track_id <> candidate_track_id),
    constraint dup_score_range check (score >= 0 and score <= 1),
    constraint dup_pair_unique unique (track_id, candidate_track_id)
);

create index ix_dup_track on duplicate_candidates (track_id);
create index ix_dup_pending on duplicate_candidates (owner_artist_id)
    where resolution = 'pending';


-- artist_credentials --------------------------------------------------------
-- Verifiable Credentials (S2-07, S2-08). Issued per artist, signed by a
-- platform-controlled issuer key. The payload records the artist's identity
-- reference and, critically, the copyright retention assertion: the artist
-- retains copyright and metadata autonomy, and the distribution terms contain
-- no IP assignment.
--
-- The assertion is stored both as a flag and as the exact wording shown to and
-- accepted by the artist, because the wording is what counsel reviews.

create table artist_credentials (
    id                uuid primary key default gen_random_uuid(),
    artist_id         uuid not null references artists (id) on delete restrict,
    credential_type   text not null default 'ArtistCredential',
    issuer            text not null,
    subject_did       text not null,
    identity_ref      text,

    -- The copyright-retention assertion.
    retains_copyright boolean not null default true,
    assertion_text    text not null,
    assertion_version text not null,

    -- The signed credential. `payload` is the canonical JSON that was signed;
    -- `signature` is the detached signature over it.
    payload           jsonb not null,
    signature         text,
    proof_type        text,
    verification_method text,

    status            credential_status not null default 'issued',
    issued_at         timestamptz not null default now(),
    expires_at        timestamptz,
    revoked_at        timestamptz,
    revocation_reason text,
    created_at        timestamptz not null default now(),

    constraint credential_assertion_present
        check (retains_copyright = false or assertion_text <> '')
);

create index ix_credentials_artist on artist_credentials (artist_id);
create index ix_credentials_status on artist_credentials (status);


-- ---------------------------------------------------------------------------
-- 4b. Deliveries
-- One row per ERN delivered to a DSP, with the acknowledgment that came back.
--
-- The exact bytes are kept by hash. When a DSP later disputes what it received,
-- this row is the evidence, and a delivery with no acknowledgment is recorded
-- as such rather than as a success.
-- ---------------------------------------------------------------------------

create table deliveries (
    id                uuid primary key default gen_random_uuid(),
    owner_artist_id   uuid references artists (id) on delete set null,
    release_id        uuid references releases (id) on delete set null,
    internal_id       text unique,
    -- The DDEX MessageId. Unique: re-delivering the same message id would
    -- overwrite the record of what the DSP was actually sent.
    message_id        text not null unique,
    message_type      text not null default 'OriginalMessage',
    profile           text,
    dsp_name          text not null,
    xml_sha256        text not null,
    byte_size         integer,
    track_count       integer not null default 0,
    status            delivery_status not null default 'pending',
    attempts          integer not null default 0,
    -- The acknowledgment body, verbatim.
    receipt           jsonb,
    -- Error codes the DSP returned, kept separate so a rejected delivery can
    -- be found without parsing the receipt.
    errors            jsonb not null default '[]'::jsonb,
    created_at        timestamptz not null default now(),
    acknowledged_at   timestamptz,

    constraint deliveries_sha256_format
        check (xml_sha256 ~ '^[0-9a-f]{64}$'),
    constraint deliveries_errors_is_array
        check (jsonb_typeof(errors) = 'array'),
    -- A rejected delivery must say why. Otherwise the reason is lost and the
    -- delivery cannot be corrected.
    constraint deliveries_rejection_has_errors
        check (status <> 'rejected' or jsonb_array_length(errors) > 0),
    -- An accepted delivery must not carry errors.
    constraint deliveries_acceptance_has_no_errors
        check (status <> 'accepted' or jsonb_array_length(errors) = 0),
    -- Acknowledged means a receipt arrived.
    constraint deliveries_ack_has_receipt
        check (acknowledged_at is null or receipt is not null)
);

create index ix_deliveries_owner on deliveries (owner_artist_id);
create index ix_deliveries_release on deliveries (release_id);
create index ix_deliveries_status on deliveries (status)
    where status in ('pending', 'unacknowledged', 'failed');
create index ix_deliveries_created on deliveries (created_at desc);


-- ---------------------------------------------------------------------------
-- 5. Revenue truth (Stage 1)
-- Mirrors revenue/schema.py. Keep the two in step.
-- ---------------------------------------------------------------------------

create table statements (
    id                text primary key,
    owner_artist_id   uuid references artists (id) on delete set null,
    source            text not null,
    period_start      date not null,
    period_end        date not null,
    currency          char(3) not null,
    received_at       timestamptz not null default now(),
    file_uri          text not null,
    file_hash         text not null,
    line_count        integer not null default 0,
    status            statement_status not null default 'received',
    total_minor       bigint not null default 0
);

create index ix_statements_owner on statements (owner_artist_id);
create index ix_statements_period on statements (period_start, period_end);

create table statement_lines (
    id                uuid primary key default gen_random_uuid(),
    statement_id      text not null references statements (id) on delete cascade,
    line_no           integer not null,
    raw_isrc          text,
    raw_upc           text,
    raw_title         text,
    raw_artist        text,
    raw_dsp           text,
    territory         char(2),
    quantity          bigint not null default 0,
    gross_minor       bigint not null default 0,
    deductions_minor  bigint not null default 0,
    net_minor         bigint not null default 0,
    currency          char(3) not null,
    matched_track_id  uuid references tracks (id) on delete set null,
    match_method      match_method,
    match_confidence  numeric(5,4),
    status            match_status not null default 'pending',
    created_at        timestamptz not null default now(),

    constraint lines_unique_no unique (statement_id, line_no)
);

create index ix_lines_statement on statement_lines (statement_id);
create index ix_lines_status on statement_lines (status);
create index ix_lines_isrc on statement_lines (raw_isrc);

-- One revenue fact per reconciled line. The unique constraint is what makes
-- ingestion idempotent: re-ingesting a statement cannot double-count revenue.
create table revenue_facts (
    id                uuid primary key default gen_random_uuid(),
    statement_id      text not null references statements (id) on delete cascade,
    line_id           uuid not null unique references statement_lines (id)
        on delete cascade,
    track_id          uuid references tracks (id) on delete set null,
    dsp               text,
    territory         char(2),
    period_start      date not null,
    period_end        date not null,
    units             bigint not null default 0,
    gross_minor       bigint not null default 0,
    deductions_minor  bigint not null default 0,
    net_minor         bigint not null default 0,
    currency          char(3) not null,
    created_at        timestamptz not null default now()
);

create index ix_facts_track on revenue_facts (track_id);
create index ix_facts_statement on revenue_facts (statement_id);

-- Unmatched and ambiguous revenue is parked here and never distributed until
-- a human resolves it.
create table suspense (
    id                uuid primary key default gen_random_uuid(),
    statement_id      text references statements (id) on delete cascade,
    line_id           uuid not null references statement_lines (id)
        on delete cascade,
    amount_minor      bigint not null,
    currency          char(3) not null,
    reason            text not null,
    created_at        timestamptz not null default now(),
    resolved_at       timestamptz,
    resolution        text,
    resolved_by       uuid references artists (id) on delete set null
);

create index ix_suspense_unresolved on suspense (created_at)
    where resolved_at is null;

-- Append-only. No update or delete policy is granted to anyone.
create table audit_log (
    id                bigserial primary key,
    at                timestamptz not null default now(),
    actor             uuid references artists (id) on delete set null,
    actor_label       text,
    action            text not null,
    entity            text not null,
    entity_id         text not null,
    detail            jsonb
);

create index ix_audit_entity on audit_log (entity, entity_id);
create index ix_audit_at on audit_log (at desc);


-- ---------------------------------------------------------------------------
-- 6. Engagement and stability (S2-18)
--
-- The stability index is a variance measure over engagement. It does not
-- create revenue; it sets the discount rate applied to revenue that already
-- exists. Lower volatility -> lower discount rate -> higher valuation per unit
-- of proven revenue.
-- ---------------------------------------------------------------------------

create table engagement_daily (
    id                uuid primary key default gen_random_uuid(),
    track_id          uuid not null references tracks (id) on delete cascade,
    day               date not null,
    plays             bigint not null default 0,
    unique_listeners  bigint not null default 0,
    completed_plays   bigint not null default 0,
    created_at        timestamptz not null default now(),

    constraint engagement_unique unique (track_id, day),
    constraint engagement_non_negative
        check (plays >= 0 and unique_listeners >= 0 and completed_plays >= 0)
);

create index ix_engagement_track_day on engagement_daily (track_id, day desc);

create table stability_index (
    id                uuid primary key default gen_random_uuid(),
    track_id          uuid not null references tracks (id) on delete cascade,
    period_start      date not null,
    period_end        date not null,
    -- Coefficient of variation of daily plays over the window, in bps.
    -- 0 = perfectly stable.
    cv_bps            integer not null,
    -- The stability score actually used: 1 / (1 + cv). In [0, 1].
    score             numeric(5,4) not null,
    -- Discount rate derived from the score, in bps, for the I1 valuation.
    discount_bps      integer not null,
    observations      integer not null,
    computed_at       timestamptz not null default now(),

    constraint stability_score_range check (score >= 0 and score <= 1),
    constraint stability_window unique (track_id, period_start, period_end)
);

create index ix_stability_track on stability_index (track_id, period_end desc);


-- ---------------------------------------------------------------------------
-- 7. Triggers
-- ---------------------------------------------------------------------------

create trigger trg_artists_updated
    before update on artists
    for each row execute function set_updated_at();

create trigger trg_works_updated
    before update on works
    for each row execute function set_updated_at();

create trigger trg_releases_updated
    before update on releases
    for each row execute function set_updated_at();

create trigger trg_tracks_updated
    before update on tracks
    for each row execute function set_updated_at();

create trigger trg_artists_internal_id
    before insert on artists
    for each row execute function assign_internal_id('ART');

create trigger trg_works_internal_id
    before insert on works
    for each row execute function assign_internal_id('WRK');

create trigger trg_releases_internal_id
    before insert on releases
    for each row execute function assign_internal_id('REL');

create trigger trg_tracks_internal_id
    before insert on tracks
    for each row execute function assign_internal_id('TRK');


-- ---------------------------------------------------------------------------
-- 8. Row Level Security
--
-- Every table has RLS enabled. The predicate throughout is: the row belongs to
-- an artist whose auth_user_id is the caller. `auth.uid()` is null for the
-- service role and for unauthenticated requests, so a null check is required
-- or background jobs would silently see nothing.
-- ---------------------------------------------------------------------------

create or replace function current_artist_id()
returns uuid
language sql
stable
security definer
set search_path = public
as $$
    select id from artists where auth_user_id = auth.uid() limit 1;
$$;

create or replace function is_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
    select exists (
        select 1 from artists
         where auth_user_id = auth.uid() and role = 'admin'
    );
$$;

alter table artists              enable row level security;
alter table works                enable row level security;
alter table releases             enable row level security;
alter table tracks               enable row level security;
alter table uploads              enable row level security;
alter table duplicate_candidates enable row level security;
alter table artist_credentials   enable row level security;
alter table statements           enable row level security;
alter table statement_lines      enable row level security;
alter table revenue_facts        enable row level security;
alter table suspense             enable row level security;
alter table audit_log            enable row level security;
alter table engagement_daily     enable row level security;
alter table stability_index      enable row level security;
alter table id_sequences         enable row level security;

-- artists -------------------------------------------------------------------
-- An artist sees and edits their own row. Admins see all.

create policy artists_select_own on artists
    for select using (auth_user_id = auth.uid() or is_admin());

create policy artists_update_own on artists
    for update using (auth_user_id = auth.uid())
    with check (auth_user_id = auth.uid());

-- Registration. A new row must be claimed by the caller, and cannot
-- self-assign the admin role.
create policy artists_insert_self on artists
    for insert with check (auth_user_id = auth.uid() and role <> 'admin');

-- works, releases, tracks ---------------------------------------------------
-- Read and write only where the row belongs to the caller's artist record.

create policy works_rw_own on works
    for all using (owner_artist_id = current_artist_id() or is_admin())
    with check (owner_artist_id = current_artist_id());

create policy releases_rw_own on releases
    for all using (owner_artist_id = current_artist_id() or is_admin())
    with check (owner_artist_id = current_artist_id());

create policy tracks_rw_own on tracks
    for all using (owner_artist_id = current_artist_id() or is_admin())
    with check (owner_artist_id = current_artist_id());

-- uploads -------------------------------------------------------------------
-- Read and write only for the caller. `track_id` is not checked here: it is a
-- foreign key to tracks, which is itself RLS-protected, so a caller cannot
-- attach an upload to a track they cannot see.

create policy uploads_rw_own on uploads
    for all using (owner_artist_id = current_artist_id() or is_admin())
    with check (owner_artist_id = current_artist_id());

-- duplicate_candidates ------------------------------------------------------
-- Read own. Write own, but a reviewer may only resolve, not fabricate.

create policy dup_select_own on duplicate_candidates
    for select using (owner_artist_id = current_artist_id() or is_admin());

create policy dup_insert_own on duplicate_candidates
    for insert with check (owner_artist_id = current_artist_id());

create policy dup_update_own on duplicate_candidates
    for update using (owner_artist_id = current_artist_id() or is_admin())
    with check (owner_artist_id = current_artist_id());

-- artist_credentials --------------------------------------------------------
-- An artist reads their own credentials. Only the service role issues them, so
-- there is no insert policy for ordinary users: a client cannot mint its own
-- credential. Admins may read all.

create policy credentials_select_own on artist_credentials
    for select using (artist_id = current_artist_id() or is_admin());

-- statements and revenue ----------------------------------------------------
-- Read own. Only the service role writes these (statements arrive from
-- distributors, not from the browser), so there is no insert policy.

create policy statements_select_own on statements
    for select using (owner_artist_id = current_artist_id() or is_admin());

create policy statement_lines_select_own on statement_lines
    for select using (
        exists (
            select 1 from statements s
             where s.id = statement_lines.statement_id
               and (s.owner_artist_id = current_artist_id() or is_admin())
        )
    );

create policy revenue_facts_select_own on revenue_facts
    for select using (
        exists (
            select 1 from statements s
             where s.id = revenue_facts.statement_id
               and (s.owner_artist_id = current_artist_id() or is_admin())
        )
    );

-- suspense is internal. Artists may see it so they can help resolve it.
create policy suspense_select_own on suspense
    for select using (
        exists (
            select 1 from statements s
             where s.id = suspense.statement_id
               and (s.owner_artist_id = current_artist_id() or is_admin())
        )
    );

-- audit_log -----------------------------------------------------------------
-- Append-only. Readable by admins; anyone may append their own action.

create policy audit_select_admin on audit_log
    for select using (is_admin());

create policy audit_insert_any on audit_log
    for insert with check (actor = current_artist_id() or actor is null);

-- engagement and stability --------------------------------------------------

create policy engagement_select_own on engagement_daily
    for select using (
        exists (
            select 1 from tracks t
             where t.id = engagement_daily.track_id
               and (t.owner_artist_id = current_artist_id() or is_admin())
        )
    );

create policy stability_select_own on stability_index
    for select using (
        exists (
            select 1 from tracks t
             where t.id = stability_index.track_id
               and (t.owner_artist_id = current_artist_id() or is_admin())
        )
    );

-- id_sequences --------------------------------------------------------------
-- Internal bookkeeping. RLS is enabled with no policy, which denies all
-- non-owner access. The functions that consume it are SECURITY DEFINER, so
-- they run as the owner and are unaffected.


-- ---------------------------------------------------------------------------
-- 9. Grants
--
-- Supabase grants table privileges to `authenticated` by default; RLS then
-- narrows rows. These statements make the intent explicit and keep the
-- migration self-contained when run outside Supabase.
-- ---------------------------------------------------------------------------

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'authenticated') then
        grant usage on schema public to authenticated;
        grant select, insert, update on
            artists, works, releases, tracks, uploads,
            duplicate_candidates, artist_credentials,
            statements, statement_lines, revenue_facts, suspense,
            audit_log, engagement_daily, stability_index
            to authenticated;
        -- audit_log is append-only.
        revoke update, delete on audit_log from authenticated;
        revoke delete on artists, works, releases, tracks, uploads,
            duplicate_candidates, artist_credentials, statements,
            statement_lines, revenue_facts, suspense,
            engagement_daily, stability_index
            from authenticated;
    end if;
end;
$$;
