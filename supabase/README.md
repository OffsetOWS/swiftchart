# SwiftChart Supabase Auth Setup

1. Create a Supabase project.
2. In Supabase SQL Editor, run `supabase/profiles.sql`.
3. In Supabase SQL Editor, run `supabase/paper_trades.sql`.
4. In Supabase Auth providers, enable Google OAuth.
5. Add these redirect URLs in Supabase Auth URL configuration:
   - `https://swiftchart.vercel.app/app`
   - `http://localhost:5173/app`
6. Add these frontend environment variables in Vercel:
   - `VITE_SUPABASE_URL`
   - `VITE_SUPABASE_ANON_KEY`

Only the Supabase anon key belongs in the frontend. Do not add Google client secrets or Supabase service role keys to Vite variables.
