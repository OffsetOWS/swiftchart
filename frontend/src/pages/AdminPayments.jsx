import { useEffect, useState } from "react";
import { Check, ExternalLink, RefreshCw, ShieldAlert, X } from "lucide-react";
import { useAuth } from "../lib/AuthContext.jsx";
import { isPaymentAdmin, listPendingPayments, reviewPaymentSubmission } from "../lib/payments.js";

function shortHash(hash) {
  if (!hash || hash.length < 18) return hash;
  return `${hash.slice(0, 10)}...${hash.slice(-8)}`;
}

export default function AdminPayments() {
  const auth = useAuth();
  const [allowed, setAllowed] = useState(null);
  const [submissions, setSubmissions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");

  async function loadPayments() {
    setLoading(true);
    setError("");
    try {
      const accessToken = auth.session?.access_token;
      const admin = await isPaymentAdmin(accessToken);
      setAllowed(admin);
      if (!admin) {
        setSubmissions([]);
        return;
      }
      setSubmissions(await listPendingPayments(accessToken));
    } catch (loadError) {
      setAllowed(false);
      setError(loadError.message || "Could not load payment submissions.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!auth.loading && auth.isAuthenticated) loadPayments();
  }, [auth.loading, auth.isAuthenticated, auth.session?.access_token]);

  async function review(id, status) {
    setBusyId(id);
    setError("");
    try {
      await reviewPaymentSubmission(id, status, auth.session?.access_token);
      setSubmissions((rows) => rows.filter((row) => row.id !== id));
      if (status === "approved") await auth.refreshProfile();
    } catch (reviewError) {
      setError(reviewError.message || "Could not review this payment.");
    } finally {
      setBusyId("");
    }
  }

  if (auth.loading || loading) {
    return <main className="payments-admin-page"><p>Loading payment reviews...</p></main>;
  }

  if (!auth.isAuthenticated || !allowed) {
    return (
      <main className="payments-admin-page">
        <section className="payments-admin-empty">
          <ShieldAlert size={28} />
          <h1>Admin access required</h1>
          <p>{error || "This account is not authorized to review payments."}</p>
        </section>
      </main>
    );
  }

  return (
    <main className="payments-admin-page">
      <header>
        <div>
          <span>SwiftChart Admin</span>
          <h1>Payment submissions</h1>
        </div>
        <button type="button" onClick={loadPayments}><RefreshCw size={17} /> Refresh</button>
      </header>

      {error ? <p className="payments-admin-error">{error}</p> : null}

      <section className="payments-admin-list">
        {submissions.length === 0 ? (
          <div className="payments-admin-empty">
            <Check size={26} />
            <h2>No pending submissions</h2>
          </div>
        ) : submissions.map((submission) => (
          <article key={submission.id}>
            <div className="payments-admin-copy">
              <strong>{submission.email}</strong>
              <span>{new Date(submission.created_at).toLocaleString()}</span>
              <span>{submission.plan_requested === "pro_lifetime" ? "Pro Lifetime" : "Pro Monthly"}</span>
              <a
                href={`https://basescan.org/tx/${submission.tx_hash}`}
                target="_blank"
                rel="noreferrer"
                title={submission.tx_hash}
              >
                {shortHash(submission.tx_hash)} <ExternalLink size={13} />
              </a>
            </div>
            <strong className="payments-admin-amount">${Number(submission.amount).toFixed(2)} USDC</strong>
            <div className="payments-admin-actions">
              <button type="button" disabled={busyId === submission.id} onClick={() => review(submission.id, "rejected")}>
                <X size={16} /> Reject
              </button>
              <button className="approve" type="button" disabled={busyId === submission.id} onClick={() => review(submission.id, "approved")}>
                <Check size={16} /> Approve
              </button>
            </div>
          </article>
        ))}
      </section>
    </main>
  );
}
