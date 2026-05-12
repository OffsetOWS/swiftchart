create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  username text not null unique,
  avatar_url text,
  signup_date timestamptz not null default now(),
  last_login timestamptz not null default now(),
  subscription_status text not null default 'free',
  telegram_chat_id text unique,
  watchlists jsonb not null default '[]'::jsonb,
  signal_history jsonb not null default '[]'::jsonb,
  ai_confidence_settings jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.profiles enable row level security;

create policy "Profiles are readable by owner"
  on public.profiles
  for select
  using (auth.uid() = id);

create policy "Profiles are insertable by owner"
  on public.profiles
  for insert
  with check (auth.uid() = id);

create policy "Profiles are updatable by owner"
  on public.profiles
  for update
  using (auth.uid() = id)
  with check (auth.uid() = id);

create index if not exists profiles_username_idx on public.profiles (username);
create index if not exists profiles_last_login_idx on public.profiles (last_login desc);

create or replace function public.set_profile_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists set_profile_updated_at on public.profiles;

create trigger set_profile_updated_at
before update on public.profiles
for each row
execute function public.set_profile_updated_at();
