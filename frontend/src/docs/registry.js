import WhatIsSwiftChart from "./introduction/what-is-swiftchart.mdx";
import FAQ from "./introduction/faq.mdx";
import QuickStart from "./introduction/quick-start.mdx";
import SignalEngine from "./how-it-works/signal-engine.mdx";
import MarketBiasDetection from "./how-it-works/market-bias-detection.mdx";
import RiskManagement from "./how-it-works/risk-management.mdx";
import TradeExecution from "./how-it-works/trade-execution.mdx";
import SessionFiltering from "./how-it-works/session-filtering.mdx";
import MultiAssetScanning from "./how-it-works/multi-asset-scanning.mdx";
import AlertSystem from "./how-it-works/alert-system.mdx";
import PerformanceTracking from "./how-it-works/performance-tracking.mdx";
import StrategyArchitecture from "./advanced/strategy-architecture.mdx";
import LiquidityVolatilityLogic from "./advanced/liquidity-volatility-logic.mdx";
import TrendConfirmationSystem from "./advanced/trend-confirmation-system.mdx";
import AiBiasEngine from "./advanced/ai-bias-engine.mdx";
import SmartEntryConditions from "./advanced/smart-entry-conditions.mdx";

export const docSections = [
  {
    label: "Introduction",
    pages: [
      {
        title: "What is SwiftChart?",
        slug: "what-is-swiftchart",
        description: "A practical overview of SwiftChart's market-structure workflow.",
        headings: ["Core idea", "What it watches", "What it is not"],
        Component: WhatIsSwiftChart,
      },
      {
        title: "FAQ",
        slug: "faq",
        description: "Answers for setup quality, alerts, long/short behavior, and no-trade states.",
        headings: ["Does SwiftChart trade for me?", "Why are there no setups sometimes?", "Why did a long disappear?", "Why did a short not trigger?", "Is Telegram required?"],
        Component: FAQ,
      },
      {
        title: "Quick Start",
        slug: "quick-start",
        description: "A short workflow for opening the app and reading the dashboard.",
        headings: ["Step 1: Launch the app", "Step 2: Review bias", "Step 3: Check top ideas", "Step 4: Save or monitor"],
        Component: QuickStart,
      },
    ],
  },
  {
    label: "How It Works",
    pages: [
      {
        title: "Signal Engine",
        slug: "signal-engine",
        description: "How SwiftChart scores, accepts, and rejects trade ideas.",
        headings: ["Scoring model", "Acceptance rules", "Rejection rules"],
        Component: SignalEngine,
      },
      {
        title: "Market Bias Detection",
        slug: "market-bias-detection",
        description: "How the engine decides bullish, bearish, transition, or no-trade state.",
        headings: ["Bullish bias", "Bearish bias", "Transitions"],
        Component: MarketBiasDetection,
      },
      {
        title: "Risk Management",
        slug: "risk-management",
        description: "How entries, stops, targets, and invalidation protect setup quality.",
        headings: ["Risk/reward", "Position context", "Invalidation"],
        Component: RiskManagement,
      },
      {
        title: "Trade Execution",
        slug: "trade-execution",
        description: "How execution is separated from signal generation.",
        headings: ["Current behavior", "Safety model", "Future expansion"],
        Component: TradeExecution,
      },
      {
        title: "Session Filtering",
        slug: "session-filtering",
        description: "How session context can reduce low-quality signals.",
        headings: ["Purpose", "Example filters", "Status"],
        Component: SessionFiltering,
      },
      {
        title: "Multi-Asset Scanning",
        slug: "multi-asset-scanning",
        description: "How SwiftChart scans Hyperliquid markets and ranks opportunities.",
        headings: ["Discovery", "Prefiltering", "Ranking"],
        Component: MultiAssetScanning,
      },
      {
        title: "Alert System",
        slug: "alert-system",
        description: "How Telegram alerts are selected and delivered.",
        headings: ["Alert quality", "Telegram flow", "Future controls"],
        Component: AlertSystem,
      },
      {
        title: "Performance Tracking",
        slug: "performance-tracking",
        description: "How signal history turns scanner output into measurable feedback.",
        headings: ["Signal memory", "Outcome checks", "Why it matters"],
        Component: PerformanceTracking,
      },
    ],
  },
  {
    label: "Advanced",
    pages: [
      {
        title: "Strategy Architecture",
        slug: "strategy-architecture",
        description: "A layered view of the SwiftChart strategy stack.",
        headings: ["Layers", "Design principle"],
        Component: StrategyArchitecture,
      },
      {
        title: "Liquidity + Volatility Logic",
        slug: "liquidity-volatility-logic",
        description: "How sweeps, displacement, and volatility shape setup quality.",
        headings: ["Liquidity sweeps", "Volatility checks", "Combined reading"],
        Component: LiquidityVolatilityLogic,
      },
      {
        title: "Trend Confirmation System",
        slug: "trend-confirmation-system",
        description: "How trend confirmation avoids reacting to every bounce.",
        headings: ["Confirmation inputs", "Bull trend confirmation", "Bear trend confirmation"],
        Component: TrendConfirmationSystem,
      },
      {
        title: "AI Bias Engine",
        slug: "ai-bias-engine",
        description: "How SwiftChart summarizes market state into a tradable regime.",
        headings: ["Bias states", "Faster regime changes", "Guardrails"],
        Component: AiBiasEngine,
      },
      {
        title: "Smart Entry Conditions",
        slug: "smart-entry-conditions",
        description: "How long and short entries are accepted or rejected.",
        headings: ["Long entries", "Short entries", "Quality control"],
        Component: SmartEntryConditions,
      },
    ],
  },
];

export const docs = docSections.flatMap((section) => section.pages.map((page) => ({ ...page, section: section.label })));

export function getDocBySlug(slug) {
  return docs.find((doc) => doc.slug === slug) || docs[0];
}
