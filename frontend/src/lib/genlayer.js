const GENLAYER_CONTRACT_ENDPOINT = import.meta.env.VITE_GENLAYER_CONTRACT_ENDPOINT || "";
const GENLAYER_CONTRACT_ADDRESS = import.meta.env.VITE_GENLAYER_CONTRACT_ADDRESS || "";
const GENLAYER_CONTRACT_METHOD = "validate_signal";
const GENLAYER_NETWORK = import.meta.env.VITE_GENLAYER_NETWORK || "studionet";

function setupScoreFor(signal) {
  const value = signal.setupScore ?? signal.setup_score ?? signal.confidence_score ?? 0;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? Math.round(numeric) : 0;
}

function rrFor(signal) {
  const value = signal.rr ?? signal.risk_reward_ratio ?? 0;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? Math.floor(numeric) : 0;
}

function normalizeDecision(value) {
  const decision = String(value || "").toUpperCase();
  if (decision === "APPROVE" || decision === "REJECT") return decision;
  throw new Error("GenLayer contract returned an unsupported decision.");
}

function parseContractResponse(payload) {
  if (typeof payload === "string") return normalizeDecision(payload);
  if (Array.isArray(payload)) return normalizeDecision(payload[0]);
  return normalizeDecision(payload?.decision ?? payload?.result ?? payload?.output);
}

async function callStudioContract(input) {
  if (!GENLAYER_CONTRACT_ADDRESS) {
    throw new Error("GenLayer contract address is not configured.");
  }
  const [{ createClient }, chains] = await Promise.all([
    import("genlayer-js"),
    import("genlayer-js/chains"),
  ]);
  const chain = chains[GENLAYER_NETWORK] || chains.studionet;
  if (!chain) {
    throw new Error(`Unsupported GenLayer network: ${GENLAYER_NETWORK}`);
  }
  const client = createClient({ chain });
  const result = await client.readContract({
    address: GENLAYER_CONTRACT_ADDRESS,
    functionName: GENLAYER_CONTRACT_METHOD,
    args: [input.setup_score, input.rr],
  });
  return parseContractResponse(result);
}

async function callContract(input) {
  const response = await fetch(GENLAYER_CONTRACT_ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      contract_address: GENLAYER_CONTRACT_ADDRESS || undefined,
      method: GENLAYER_CONTRACT_METHOD,
      args: [input.setup_score, input.rr],
    }),
  });
  if (!response.ok) {
    throw new Error("GenLayer scan failed. Try again.");
  }
  return parseContractResponse(await response.json());
}

async function mockContract(input) {
  return input.setup_score >= 70 && input.rr >= 2 ? "APPROVE" : "REJECT";
}

export async function scanWithGenLayer(signal) {
  const input = {
    setup_score: setupScoreFor(signal),
    rr: rrFor(signal),
  };
  let decision;
  let source = "mock";
  try {
    if (GENLAYER_CONTRACT_ADDRESS) {
      decision = await callStudioContract(input);
      source = "genlayer-studio";
    }
  } catch (error) {
    console.warn("GenLayer Studio contract scan failed.", error);
  }
  try {
    if (!decision && GENLAYER_CONTRACT_ENDPOINT) {
      decision = await callContract(input);
      source = "genlayer-endpoint";
    }
  } catch (error) {
    console.warn("GenLayer endpoint scan failed.", error);
  }
  if (!decision) {
    console.warn("Falling back to GenLayer mock adapter.");
    decision = await mockContract(input);
  }
  return { decision, input, source };
}
