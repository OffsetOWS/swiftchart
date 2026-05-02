import { useEffect, useRef } from "react";
import { createChart, ColorType } from "lightweight-charts";

function toSeries(candles) {
  return candles.map((candle) => ({
    time: Math.floor(new Date(candle.timestamp).getTime() / 1000),
    open: candle.open,
    high: candle.high,
    low: candle.low,
    close: candle.close,
  }));
}

export default function Chart({ candles, analysis }) {
  const ref = useRef(null);

  useEffect(() => {
    if (!ref.current || candles.length === 0) return undefined;
    const chart = createChart(ref.current, {
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#9da6b5" },
      grid: { vertLines: { color: "rgba(255,255,255,0.06)" }, horzLines: { color: "rgba(255,255,255,0.06)" } },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.1)" },
      timeScale: { borderColor: "rgba(255,255,255,0.1)" },
      width: ref.current.clientWidth,
      height: ref.current.clientHeight,
    });
    const series = chart.addCandlestickSeries({
      upColor: "#42d89d",
      downColor: "#ff6875",
      borderUpColor: "#42d89d",
      borderDownColor: "#ff6875",
      wickUpColor: "#42d89d",
      wickDownColor: "#ff6875",
    });
    series.setData(toSeries(candles));

    [...(analysis?.support_zones || []), ...(analysis?.resistance_zones || [])].forEach((zone) => {
      series.createPriceLine({
        price: (zone.lower + zone.upper) / 2,
        color: zone.type === "support" ? "#42d89d" : "#ff6875",
        lineWidth: 2,
        lineStyle: 2,
        axisLabelVisible: true,
        title: `${zone.type} ${zone.touches}x`,
      });
    });

    chart.timeScale().fitContent();
    const resize = () => chart.applyOptions({ width: ref.current.clientWidth, height: ref.current.clientHeight });
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.remove();
    };
  }, [candles, analysis]);

  return <div className="chart-host" ref={ref} />;
}
