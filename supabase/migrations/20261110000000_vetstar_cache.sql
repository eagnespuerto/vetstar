-- vetstar shared cache: cleaned light curves and TESScut FFI cutouts.
--
-- The backend (backend/app/cache_store.py) reads and writes this table over
-- a direct Postgres connection (SUPABASE_DB_URL). It is not meant to be
-- reached through the Supabase REST API, so row level security is on with
-- no policies and the API roles get no grants.

create table if not exists public.vetstar_cache (
    kind        text        not null check (kind in ('lc', 'ffi')),
    cache_key   text        not null,
    tic_id      bigint,
    sector      integer,
    payload     bytea       not null,
    size_bytes  integer     generated always as (octet_length(payload)) stored,
    created_at  timestamptz not null default now(),
    expires_at  timestamptz not null,
    primary key (kind, cache_key)
);

create index if not exists vetstar_cache_tic_idx
    on public.vetstar_cache (kind, tic_id, created_at desc);
create index if not exists vetstar_cache_expires_idx
    on public.vetstar_cache (expires_at);

alter table public.vetstar_cache enable row level security;
revoke all on table public.vetstar_cache from anon, authenticated;

-- Delete expired rows, then the oldest rows until the payloads fit in
-- max_bytes. Returns the number of rows deleted. The backend calls this
-- every few writes; the free tier allows 500 MB for the whole database.
create or replace function public.vetstar_cache_prune(
    max_bytes bigint default 300 * 1024 * 1024
) returns integer
language plpgsql
set search_path = public
as $$
declare
    n_expired integer := 0;
    n_over    integer := 0;
begin
    delete from public.vetstar_cache where expires_at < now();
    get diagnostics n_expired = row_count;

    with ranked as (
        select kind, cache_key,
               sum(size_bytes) over (order by created_at desc, kind, cache_key) as running
        from public.vetstar_cache
    )
    delete from public.vetstar_cache c
    using ranked r
    where c.kind = r.kind
      and c.cache_key = r.cache_key
      and r.running > max_bytes;
    get diagnostics n_over = row_count;

    return n_expired + n_over;
end;
$$;

revoke execute on function public.vetstar_cache_prune(bigint) from public, anon, authenticated;
