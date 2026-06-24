create table if not exists public.paper_trades (
  id uuid primary key default gen_random_uuid(),

  -- Web dashboard ownership.
  user_id uuid references auth.users(id) on delete cascade,

  -- Telegram paper-trading ownership and signal fields.
  telegram_user_id bigint,
  signal_id text not null,
  pair text,
  side text check (side in ('long', 'short')),
  entry numeric,
  stop_loss numeric,
  tp1 numeric,
  tp2 numeric,
  status text not null default 'open',
  opened_at timestamptz,
  closed_at timestamptz,
  pnl_r numeric,

  -- Existing web dashboard fields remain supported.
  symbol text,
  exchange text not null default 'hyperliquid',
  timeframe text,
  direction text check (direction in ('long', 'short')),
  entry_price numeric,
  take_profit numeric,
  take_profit_2 numeric,
  risk_reward numeric,
  confidence numeric,
  market_bias text,
  pnl numeric,
  result text not null default 'open',
  source text not null default 'signal',
  paper_trade boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Upgrade installations created from the earlier dashboard-only schema.
alter table public.paper_trades alter column user_id drop not null;
alter table public.paper_trades alter column symbol drop not null;
alter table public.paper_trades alter column direction drop not null;
alter table public.paper_trades alter column entry_price drop not null;
alter table public.paper_trades alter column stop_loss drop not null;
alter table public.paper_trades alter column take_profit drop not null;

alter table public.paper_trades add column if not exists telegram_user_id bigint;
alter table public.paper_trades add column if not exists pair text;
alter table public.paper_trades add column if not exists side text;
alter table public.paper_trades add column if not exists entry numeric;
alter table public.paper_trades add column if not exists tp1 numeric;
alter table public.paper_trades add column if not exists tp2 numeric;
alter table public.paper_trades add column if not exists opened_at timestamptz;
alter table public.paper_trades add column if not exists closed_at timestamptz;
alter table public.paper_trades add column if not exists pnl_r numeric;

alter table public.paper_trades drop constraint if exists paper_trades_status_check;
alter table public.paper_trades
  add constraint paper_trades_status_check
  check (status in ('taken', 'open', 'tp_hit', 'tp1_hit', 'tp2_hit', 'sl_hit', 'closed'));

alter table public.paper_trades drop constraint if exists paper_trades_side_check;
alter table public.paper_trades
  add constraint paper_trades_side_check
  check (side is null or side in ('long', 'short'));

alter table public.paper_trades drop constraint if exists paper_trades_owner_check;
alter table public.paper_trades
  add constraint paper_trades_owner_check
  check (user_id is not null or telegram_user_id is not null);

alter table public.paper_trades enable row level security;

drop policy if exists "Paper trades are readable by owner" on public.paper_trades;
create policy "Paper trades are readable by owner"
  on public.paper_trades
  for select
  using (auth.uid() = user_id);

drop policy if exists "Paper trades are insertable by owner" on public.paper_trades;
create policy "Paper trades are insertable by owner"
  on public.paper_trades
  for insert
  with check (auth.uid() = user_id);

drop policy if exists "Paper trades are updatable by owner" on public.paper_trades;
create policy "Paper trades are updatable by owner"
  on public.paper_trades
  for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create unique index if not exists paper_trades_user_signal_unique
  on public.paper_trades (user_id, signal_id)
  where user_id is not null;

create unique index if not exists paper_trades_telegram_signal_unique
  on public.paper_trades (telegram_user_id, signal_id)
  where telegram_user_id is not null;

create index if not exists paper_trades_user_created_idx
  on public.paper_trades (user_id, created_at desc);
create index if not exists paper_trades_user_status_idx
  on public.paper_trades (user_id, status);
create index if not exists paper_trades_telegram_opened_idx
  on public.paper_trades (telegram_user_id, opened_at desc);
create index if not exists paper_trades_telegram_status_idx
  on public.paper_trades (status, opened_at)
  where telegram_user_id is not null;
create index if not exists paper_trades_signal_idx
  on public.paper_trades (signal_id);

create or replace function public.set_paper_trade_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists set_paper_trade_updated_at on public.paper_trades;

create trigger set_paper_trade_updated_at
before update on public.paper_trades
for each row
execute function public.set_paper_trade_updated_at();

-- Telegram rows are accessed only by the trusted bot using the service-role
-- key. That key bypasses RLS and must never be exposed to the frontend.
