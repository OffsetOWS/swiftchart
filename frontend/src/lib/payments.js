import {
  listMyPaymentsApi,
  listPendingPaymentsApi,
  paymentAdminAccessApi,
  reviewPaymentApi,
  submitPaymentApi,
} from "./api.js";

const BASE_PAYMENT = Object.freeze({
  network: "Base",
  token: "USDC",
  walletAddress: "0xF5b655697DE8E1f053ABD64d303A94DfF7074175",
});

export const PAYMENT_PLANS = Object.freeze({
  pro_monthly: Object.freeze({
    ...BASE_PAYMENT,
    amount: 9.99,
    plan: "pro_monthly",
    label: "Pro Monthly",
    price: "$9.99 USDC",
  }),
  pro_lifetime: Object.freeze({
    ...BASE_PAYMENT,
    amount: 99.99,
    plan: "pro_lifetime",
    label: "Pro Lifetime",
    price: "$99.99 USDC",
  }),
});

export const LIFETIME_PAYMENT = PAYMENT_PLANS.pro_lifetime;

function requireAccessToken(accessToken) {
  if (!accessToken) throw new Error("Sign in to continue.");
}

function normalizeSubmission(row) {
  return {
    ...row,
    tx_hash: row.transaction_hash || row.tx_hash,
    amount: Number(row.expected_amount ?? row.amount),
    plan_requested: row.plan || row.plan_requested,
    created_at: row.submitted_at || row.created_at,
  };
}

export async function submitPayment({
  txHash,
  plan = "pro_lifetime",
  accessToken,
  senderWallet = null,
}) {
  requireAccessToken(accessToken);
  const payment = PAYMENT_PLANS[plan];
  if (!payment) throw new Error("Unsupported payment plan.");
  const normalizedHash = String(txHash || "").trim().toLowerCase();
  if (!normalizedHash) throw new Error("Transaction hash is required.");
  if (!/^0x[0-9a-f]{64}$/.test(normalizedHash)) {
    throw new Error("Enter a valid Base transaction hash.");
  }

  const data = await submitPaymentApi({
    plan: payment.plan,
    transaction_hash: normalizedHash,
    sender_wallet: senderWallet,
  }, accessToken);
  return normalizeSubmission(data);
}

export async function submitLifetimePayment(options) {
  return submitPayment({ ...options, plan: "pro_lifetime" });
}

export async function listMyPaymentSubmissions(accessToken) {
  requireAccessToken(accessToken);
  const data = await listMyPaymentsApi(accessToken);
  return (data || []).map(normalizeSubmission);
}

export async function isPaymentAdmin(accessToken) {
  requireAccessToken(accessToken);
  const data = await paymentAdminAccessApi(accessToken);
  return Boolean(data?.is_admin);
}

export async function listPendingPayments(accessToken) {
  requireAccessToken(accessToken);
  const data = await listPendingPaymentsApi(accessToken);
  return (data || []).map(normalizeSubmission);
}

export async function reviewPaymentSubmission(
  submissionId,
  status,
  accessToken,
  rejectionReason = null,
) {
  requireAccessToken(accessToken);
  const data = await reviewPaymentApi(submissionId, {
    status,
    rejection_reason: rejectionReason,
  }, accessToken);
  return normalizeSubmission(data);
}
