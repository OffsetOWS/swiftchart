-- SwiftChart manual Base USDC payment workflow.
-- This migration is idempotent and upgrades the earlier tx_hash/amount schema in place.

create table if not exists public.payment_admins (
  email text primary key,
  created_at timestamptz not null default now()
);

alter table public.payment_admins enable row level security;
revoke all on public.payment_admins from anon, authenticated;

create table if not exists public.payment_submissions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  email text not null,
  plan text not null,
  network text not null,
  token text not null,
  expected_amount numeric(18, 6) not null,
  transaction_hash text not null,
  sender_wallet text,
  status text not null default 'pending',
  rejection_reason text,
  submitted_at timestamptz not null default now(),
  reviewed_at timestamptz,
  reviewed_by uuid references auth.users(id),
  payment_confirmed_at timestamptz,
  -- Legacy aliases retained while older clients are phased out.
  tx_hash text,
  amount numeric(18, 6),
  plan_requested text,
  created_at timestamptz not null default now()
);

alter table public.payment_submissions
  add column if not exists email text,
  add column if not exists plan text,
  add column if not exists network text,
  add column if not exists token text,
  add column if not exists expected_amount numeric(18, 6),
  add column if not exists transaction_hash text,
  add column if not exists sender_wallet text,
  add column if not exists status text default 'pending',
  add column if not exists rejection_reason text,
  add column if not exists submitted_at timestamptz default now(),
  add column if not exists reviewed_at timestamptz,
  add column if not exists reviewed_by uuid references auth.users(id),
  add column if not exists payment_confirmed_at timestamptz,
  add column if not exists tx_hash text,
  add column if not exists amount numeric(18, 6),
  add column if not exists plan_requested text,
  add column if not exists created_at timestamptz default now();

update public.payment_submissions
set
  plan = coalesce(plan, plan_requested),
  expected_amount = coalesce(expected_amount, amount),
  transaction_hash = lower(coalesce(transaction_hash, tx_hash)),
  submitted_at = coalesce(submitted_at, created_at, now()),
  tx_hash = lower(coalesce(tx_hash, transaction_hash)),
  amount = coalesce(amount, expected_amount),
  plan_requested = coalesce(plan_requested, plan),
  created_at = coalesce(created_at, submitted_at, now()),
  network = coalesce(network, 'Base'),
  token = coalesce(token, 'USDC'),
  status = coalesce(status, 'pending');

alter table public.payment_submissions
  alter column email set not null,
  alter column plan set not null,
  alter column network set not null,
  alter column token set not null,
  alter column expected_amount set not null,
  alter column transaction_hash set not null,
  alter column status set not null,
  alter column status set default 'pending',
  alter column submitted_at set not null,
  alter column submitted_at set default now();

alter table public.payment_submissions
  drop constraint if exists payment_submissions_status_check,
  drop constraint if exists payment_submissions_network_check,
  drop constraint if exists payment_submissions_token_check,
  drop constraint if exists payment_submissions_plan_check,
  drop constraint if exists payment_submissions_plan_amount_check,
  add constraint payment_submissions_status_check
    check (status in ('pending', 'approved', 'rejected')),
  add constraint payment_submissions_network_check
    check (network = 'Base'),
  add constraint payment_submissions_token_check
    check (token = 'USDC'),
  add constraint payment_submissions_plan_check
    check (plan in ('pro_monthly', 'pro_lifetime')),
  add constraint payment_submissions_plan_amount_check
    check (
      (plan = 'pro_monthly' and expected_amount = 9.99)
      or (plan = 'pro_lifetime' and expected_amount = 99.99)
    );

create unique index if not exists payment_submissions_transaction_hash_unique
  on public.payment_submissions (lower(transaction_hash));

create index if not exists payment_submissions_status_submitted_idx
  on public.payment_submissions (status, submitted_at desc);

create index if not exists payment_submissions_user_submitted_idx
  on public.payment_submissions (user_id, submitted_at desc);

alter table public.payment_submissions enable row level security;
revoke all on public.payment_submissions from anon, authenticated;
grant select on public.payment_submissions to authenticated;

drop policy if exists "Payment submissions are insertable by owner" on public.payment_submissions;
drop policy if exists "Payment submissions are readable by owner or admin" on public.payment_submissions;
drop policy if exists "Users can read their payment submissions" on public.payment_submissions;

create policy "Users can read their payment submissions"
  on public.payment_submissions
  for select
  to authenticated
  using (auth.uid() = user_id);

alter table public.profiles
  add column if not exists subscription_expires_at timestamptz,
  add column if not exists subscription_started_at timestamptz;

-- Users may edit profile presentation fields, but never subscription fields.
revoke insert on public.profiles from authenticated;
grant insert (id, email, username, avatar_url, signup_date, last_login, telegram_chat_id, watchlists, signal_history, ai_confidence_settings)
  on public.profiles to authenticated;
revoke update on public.profiles from authenticated;
grant update (email, username, avatar_url, last_login, telegram_chat_id, watchlists, signal_history, ai_confidence_settings)
  on public.profiles to authenticated;

create or replace function public.refresh_my_subscription_status()
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.profiles
  set
    subscription_status = 'free',
    subscription_started_at = null,
    subscription_expires_at = null
  where id = auth.uid()
    and subscription_status = 'pro_monthly'
    and subscription_expires_at is not null
    and subscription_expires_at <= now();
end;
$$;

revoke all on function public.refresh_my_subscription_status() from public;
grant execute on function public.refresh_my_subscription_status() to authenticated;

create or replace function public.review_payment_submission_backend(
  p_submission_id uuid,
  p_status text,
  p_rejection_reason text,
  p_reviewer_id uuid
)
returns setof public.payment_submissions
language plpgsql
security definer
set search_path = public, auth
as $$
declare
  reviewed public.payment_submissions;
  reviewer_email text;
begin
  select lower(email) into reviewer_email
  from auth.users
  where id = p_reviewer_id;

  if reviewer_email is null or not exists (
    select 1
    from public.payment_admins
    where lower(email) = reviewer_email
  ) then
    raise exception 'Admin access required';
  end if;

  if p_status not in ('approved', 'rejected') then
    raise exception 'Invalid payment review status';
  end if;

  select * into reviewed
  from public.payment_submissions
  where id = p_submission_id
    and status = 'pending'
  for update;

  if reviewed.id is null then
    raise exception 'Pending payment submission not found';
  end if;

  if reviewed.user_id = p_reviewer_id then
    raise exception 'Administrators cannot review their own payment';
  end if;

  update public.payment_submissions
  set
    status = p_status,
    rejection_reason = case when p_status = 'rejected' then nullif(trim(p_rejection_reason), '') else null end,
    reviewed_at = now(),
    reviewed_by = p_reviewer_id,
    payment_confirmed_at = case when p_status = 'approved' then now() else null end
  where id = p_submission_id
  returning * into reviewed;

  if p_status = 'approved' then
    update public.profiles
    set
      subscription_status = reviewed.plan,
      subscription_started_at = now(),
      subscription_expires_at = case
        when reviewed.plan = 'pro_monthly' then now() + interval '30 days'
        else null
      end
    where id = reviewed.user_id;

    if not found then
      raise exception 'Payment owner profile not found';
    end if;
  end if;

  return next reviewed;
end;
$$;

revoke all on function public.review_payment_submission_backend(uuid, text, text, uuid) from public;
grant execute on function public.review_payment_submission_backend(uuid, text, text, uuid) to service_role;

-- Retire the browser-callable approval function from the previous version,
-- while remaining safe on fresh installations where it never existed.
do $$
begin
  if to_regprocedure('public.review_payment_submission(uuid,text)') is not null then
    execute 'revoke all on function public.review_payment_submission(uuid, text) from authenticated';
  end if;
end;
$$;

notify pgrst, 'reload schema';

-- Add the owner after applying the migration:
-- insert into public.payment_admins (email)
-- values ('owner@example.com')
-- on conflict (email) do nothing;
