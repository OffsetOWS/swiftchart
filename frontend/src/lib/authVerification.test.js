import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const authPage = readFileSync(new URL("../pages/Auth.jsx", import.meta.url), "utf8");
const authContext = readFileSync(new URL("./AuthContext.jsx", import.meta.url), "utf8");

test("email signup exposes and submits the Supabase verification code", () => {
  assert.match(authPage, /Verify your email/);
  assert.match(authPage, /autoComplete="one-time-code"/);
  assert.match(authPage, /verificationCode\.length !== 6/);
  assert.match(authPage, /auth\.verifySignupCode\(email\.trim\(\), verificationCode\)/);
  assert.match(authPage, /Verify and continue/);
  assert.match(authPage, /Resend verification code/);

  assert.match(authContext, /supabase\.auth\.verifyOtp\(/);
  assert.match(authContext, /type: "signup"/);
  assert.match(authContext, /verifySignupCode,/);
});
