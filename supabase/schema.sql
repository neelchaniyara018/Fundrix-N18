-- Run once in Supabase Dashboard -> SQL Editor.

create table if not exists public.profiles (
  id         uuid primary key references auth.users(id) on delete cascade,
  email      text not null,
  phone      text unique check (phone is null or phone ~ '^\+91[6-9][0-9]{9}$'),
  created_at timestamptz not null default now()
);
alter table public.profiles enable row level security;

-- A signed-in user may read only their own profile. No insert/update/delete policies: clients cannot write profiles.
drop policy if exists "read own profile" on public.profiles;
create policy "read own profile" on public.profiles for select to authenticated using (auth.uid() = id);

-- Profile rows are created only once the e-mail is CONFIRMED (so unverified sign-ups cannot squat on someone's phone number).
create or replace function public.handle_user_confirmed() returns trigger
language plpgsql security definer set search_path = public as $$
declare p text := new.raw_user_meta_data ->> 'phone';
begin
  if p is not null and p !~ '^\+91[6-9][0-9]{9}$' then p := null; end if;
  if p is not null and exists (select 1 from public.profiles where phone = p) then p := null; end if;
  begin
    insert into public.profiles (id, email, phone) values (new.id, new.email, p) on conflict (id) do nothing;
  exception when unique_violation then          -- lost a race for the same phone: keep the account, drop the phone
    insert into public.profiles (id, email, phone) values (new.id, new.email, null) on conflict (id) do nothing;
  end;
  return new;
end $$;

drop trigger if exists on_user_confirmed on auth.users;
create trigger on_user_confirmed after update of email_confirmed_at on auth.users
  for each row when (old.email_confirmed_at is null and new.email_confirmed_at is not null)
  execute function public.handle_user_confirmed();

drop trigger if exists on_user_created_confirmed on auth.users;
create trigger on_user_created_confirmed after insert on auth.users
  for each row when (new.email_confirmed_at is not null)
  execute function public.handle_user_confirmed();


-- ============================================================ saved screens + watchlist (per user, protected by Row Level Security)
create table if not exists public.screens (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null default auth.uid() references auth.users(id) on delete cascade,
  name       text not null check (char_length(name) between 1 and 80),
  note       text not null default '' check (char_length(note) <= 500),
  config     jsonb not null check (pg_column_size(config) < 20000),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists screens_user_updated on public.screens (user_id, updated_at desc);
alter table public.screens enable row level security;
drop policy if exists "screens: select own" on public.screens;
drop policy if exists "screens: insert own" on public.screens;
drop policy if exists "screens: update own" on public.screens;
drop policy if exists "screens: delete own" on public.screens;
create policy "screens: select own" on public.screens for select to authenticated using (user_id = auth.uid());
create policy "screens: insert own" on public.screens for insert to authenticated with check (user_id = auth.uid());
create policy "screens: update own" on public.screens for update to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "screens: delete own" on public.screens for delete to authenticated using (user_id = auth.uid());

-- Share links: anyone who knows the (unguessable) screen id can read THAT screen; nobody can list other people's screens.
create or replace function public.get_shared_screen(p_id uuid)
returns table (id uuid, name text, note text, config jsonb)
language sql stable security definer set search_path = public as
$$ select s.id, s.name, s.note, s.config from public.screens s where s.id = p_id $$;
revoke all on function public.get_shared_screen(uuid) from public;
grant execute on function public.get_shared_screen(uuid) to anon, authenticated;

-- Watchlist: fund_id is the AMFI scheme code (stable across daily rebuilds).
create table if not exists public.watchlist (
  user_id    uuid not null default auth.uid() references auth.users(id) on delete cascade,
  fund_id    integer not null check (fund_id > 0),
  created_at timestamptz not null default now(),
  primary key (user_id, fund_id)
);
alter table public.watchlist enable row level security;
drop policy if exists "watchlist: select own" on public.watchlist;
drop policy if exists "watchlist: insert own" on public.watchlist;
drop policy if exists "watchlist: delete own" on public.watchlist;
create policy "watchlist: select own" on public.watchlist for select to authenticated using (user_id = auth.uid());
create policy "watchlist: insert own" on public.watchlist for insert to authenticated with check (user_id = auth.uid());
create policy "watchlist: delete own" on public.watchlist for delete to authenticated using (user_id = auth.uid());
