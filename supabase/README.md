# SwiftChart Supabase Auth Setup

1. Create a Supabase project.
2. In Supabase SQL Editor, run `supabase/profiles.sql`.
3. In Supabase SQL Editor, run `supabase/paper_trades.sql`.
4. Run `supabase/payment_submissions.sql`.
5. Add payment-review admins:

```sql
insert into public.payment_admins (email)
values ('owner@example.com')
on conflict (email) do nothing;
```

The payment migration creates the manual Base USDC submission workflow for Pro Monthly and Pro Lifetime, prevents duplicate transaction hashes, and exposes an admin-only approval function that activates the requested `profiles.subscription_status`.
6. In Supabase Auth providers, enable Google OAuth.
7. Add these redirect URLs in Supabase Auth URL configuration:
   - `https://swiftchart.vercel.app/app`
   - `http://localhost:5173/app`
8. Add these frontend environment variables in Vercel:
   - `VITE_SUPABASE_URL`
   - `VITE_SUPABASE_ANON_KEY`
9. Add these server-only variables to the Telegram bot environment:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`

The Telegram bot uses the service-role key to create and monitor simulated
paper trades keyed by `telegram_user_id`. It never submits exchange orders and
does not need exchange API keys.

Only the Supabase anon key belongs in the frontend. Do not add Google client secrets or Supabase service role keys to Vite variables.
