import { useEffect, useRef } from "react";
import { ColorType, CrosshairMode, LineStyle, createChart } from "lightweight-charts";

function chartCandles(candles) {
  return candles
    .map((candle) => ({
      time: Math.floor(new Date(candle.timestamp).getTime() / 1000),
      open: Number(candle.open),
      high: Number(candle.high),
      low: Number(candle.low),
      close: Number(candle.close),
    }))
    .filter((candle) => Number.isFinite(candle.time) && Number.isFinite(candle.close))
    .sort((a, b) => a.time - b.time);
}

export default function ForexPairChart({ snapshot, layer = "zones", light = false }) {
  const hostRef = useRef(null);
  const overlayRef = useRef(null);

  useEffect(() => {
    if (!hostRef.current || !snapshot?.candles?.length) return undefined;
    const chart = createChart(hostRef.current, {
      width: hostRef.current.clientWidth,
      height: hostRef.current.clientHeight,
      layout: {
        background: { type: ColorType.Solid, color: light ? "#f8fafa" : "#090b0d" },
        textColor: light ? "#657176" : "#778087",
        fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
        fontSize: 10,
      },
      grid: {
        vertLines: { color: light ? "rgba(20,30,35,.055)" : "rgba(255,255,255,.035)" },
        horzLines: { color: light ? "rgba(20,30,35,.055)" : "rgba(255,255,255,.035)" },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.1, bottom: 0.12 } },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false, rightOffset: 6 },
      handleScroll: { vertTouchDrag: false },
      handleScale: { axisPressedMouseMove: false },
    });
    const candles = chart.addCandlestickSeries({
      upColor: "#3ddc97",
      downColor: "#ff646d",
      borderVisible: false,
      wickUpColor: "#3ddc97",
      wickDownColor: "#ff646d",
      priceLineVisible: true,
      lastValueVisible: true,
    });
    candles.setData(chartCandles(snapshot.candles));

    const levels = snapshot.levels || {};
    const addLine = (price, color, title, style = LineStyle.Dashed, width = 1) => {
      if (!Number.isFinite(Number(price))) return;
      candles.createPriceLine({
        price: Number(price), color, lineWidth: width, lineStyle: style,
        axisLabelVisible: true, title,
      });
    };
    if (layer !== "clean") {
      addLine(levels.confirmation, "#9ba4aa", "CONF", LineStyle.Dashed);
      addLine(levels.retest, "#6f7a80", "RETEST", LineStyle.Dotted);
      addLine(levels.invalidation, "#ff646d", "INVALID", LineStyle.Solid);
      addLine(levels.target_1, "#5dd9cf", "TP1", LineStyle.Dashed);
      addLine(levels.target_2, "#5dd9cf", "TP2", LineStyle.Dashed);
    }

    const zoneElements = [];
    if (layer === "zones") {
      [
        ...(snapshot.buy_zones || []).map((zone) => ({ ...zone, side: "buy" })),
        ...(snapshot.sell_zones || []).map((zone) => ({ ...zone, side: "sell" })),
      ].forEach((zone) => {
        const element = document.createElement("div");
        element.className = `forex-chart-zone ${zone.side}`;
        element.innerHTML = `<span>${zone.side === "buy" ? "BUY" : "SELL"} ZONE · ${String(zone.state).replaceAll("_", " ")}</span>`;
        overlayRef.current?.appendChild(element);
        zoneElements.push({ element, zone });
      });
    }

    const positionZones = () => {
      zoneElements.forEach(({ element, zone }) => {
        const top = candles.priceToCoordinate(Number(zone.high));
        const bottom = candles.priceToCoordinate(Number(zone.low));
        if (top == null || bottom == null) {
          element.style.display = "none";
          return;
        }
        element.style.display = "block";
        element.style.top = `${Math.min(top, bottom)}px`;
        element.style.height = `${Math.max(6, Math.abs(bottom - top))}px`;
      });
    };
    chart.timeScale().fitContent();
    const frame = window.requestAnimationFrame(positionZones);
    chart.timeScale().subscribeVisibleLogicalRangeChange(positionZones);
    const resize = () => {
      if (!hostRef.current) return;
      chart.applyOptions({ width: hostRef.current.clientWidth, height: hostRef.current.clientHeight });
      positionZones();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(hostRef.current);

    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(positionZones);
      zoneElements.forEach(({ element }) => element.remove());
      chart.remove();
    };
  }, [layer, light, snapshot]);

  return (
    <div className="forex-live-chart">
      <div className="forex-chart-host" ref={hostRef} />
      <div className="forex-chart-overlay" ref={overlayRef} aria-hidden="true" />
    </div>
  );
}
