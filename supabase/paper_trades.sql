create table if not exists public.paper_trades (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  signal_id text not null,
  symbol text not null,
  exchange text not null default 'hyperliquid',
  timeframe text,
  direction text not null check (direction in ('long', 'short')),
  entry_price numeric not null,
  stop_loss numeric not null,
  take_profit numeric not null,
  take_profit_2 numeric,
  risk_reward numeric,
  confidence numeric,
  market_bias text,
  status text not null default 'open' check (status in ('open', 'tp_hit', 'sl_hit', 'closed')),
  pnl numeric,
  result text not null default 'open' check (result in ('open', 'win', 'loss', 'closed')),
  source text not null default 'signal',
  paper_trade boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (user_id, signal_id)
);

alter table public.paper_trades enable row level security;

create policy "Paper trades are readable by owner"
  on public.paper_trades
  for select
  using (auth.uid() = user_id);

create policy "Paper trades are insertable by owner"
  on public.paper_trades
  for insert
  with check (auth.uid() = user_id);

create policy "Paper trades are updatable by owner"
  on public.paper_trades
  for update
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

create index if not exists paper_trades_user_created_idx on public.paper_trades (user_id, created_at desc);
create index if not exists paper_trades_user_status_idx on public.paper_trades (user_id, status);
create index if not exists paper_trades_signal_idx on public.paper_trades (signal_id);

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
