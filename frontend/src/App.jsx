import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import LandingPage from "./components/LandingPage.jsx";
import { getTopIdeas } from "./lib/api.js";
import "./styles/global.css";

export default function App() {
  const [topIdeas, setTopIdeas] = useState([]);
  const [loadingTopIdeas, setLoadingTopIdeas] = useState(false);

  async function refreshTopIdeas() {
    setLoadingTopIdeas(true);
    try {
      const data = await getTopIdeas({ exchange: "hyperliquid", timeframe: "4h" });
      setTopIdeas(data.ideas || []);
    } catch {
      setTopIdeas([]);
    } finally {
      setLoadingTopIdeas(false);
    }
  }

  useEffect(() => {
    refreshTopIdeas();
  }, []);

  return (
    <div className="site-shell">
      <LandingPage topIdeas={topIdeas} loadingTopIdeas={loadingTopIdeas} refreshTopIdeas={refreshTopIdeas} />
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
