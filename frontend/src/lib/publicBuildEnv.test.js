import assert from "node:assert/strict";
import test from "node:test";
import { validatePublicBuildEnv } from "./publicBuildEnv.js";
const jwt = (role) => ['header', Buffer.from(JSON.stringify({role})).toString('base64url'), 'signature'].join('.');
const production = { VERCEL_ENV:"production", VITE_SUPABASE_URL:"https://project.supabase.co" };
test("public builds accept publishable and legacy anon keys", () => {
  for (const key of ['sb_publishable_local-fixture',jwt('anon')]) {
    assert.equal(validatePublicBuildEnv({...production,VITE_SUPABASE_ANON_KEY:key}).authenticationConfigured,true);
  }
});
test("elevated keys fail before frontend assets are built", () => {
  for (const key of ['sb_secret_local-fixture',jwt('service_role'),jwt('authenticated')]) {
    assert.throws(()=>validatePublicBuildEnv({...production,VITE_SUPABASE_ANON_KEY:key}));
  }
});
test("production requires configured auth and a same-origin proxy", () => {
  assert.throws(()=>validatePublicBuildEnv(production));
  assert.throws(()=>validatePublicBuildEnv({...production,VITE_SUPABASE_ANON_KEY:jwt('anon'),VITE_API_BASE:'https://backend.example'}));
  assert.throws(()=>validatePublicBuildEnv({...production,VITE_SUPABASE_ANON_KEY:jwt('anon'),VITE_SUPABASE_URL:'https://user:credential@project.supabase.co'}));
  assert.equal(validatePublicBuildEnv({}).authenticationConfigured,false);
});
