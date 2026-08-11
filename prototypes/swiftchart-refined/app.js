const root = document.querySelector('#app');
const params = new URLSearchParams(location.search);

const cryptoSignals = [
  { id:'btc', symbol:'BTCUSDT', coin:'btc', mark:'₿', direction:'Long', tf:'4H', rr:'3.1R', score:97, state:'High conviction', entry:'116,240–116,680', current:'116,510', stop:'114,720', tp1:'119,340', tp2:'122,180' },
  { id:'hype', symbol:'HYPEUSDT', coin:'fx', mark:'H', direction:'Short', tf:'4H', rr:'3.4R', score:92, state:'High conviction' },
  { id:'sui', symbol:'SUIUSDT', coin:'sui', mark:'S', direction:'Long', tf:'6H', rr:'2.5R', score:88, state:'Pro' },
  { id:'eth', symbol:'ETHUSDT', coin:'eth', mark:'◆', direction:'Long', tf:'6H', rr:'2.6R', score:85, state:'Pro' },
];

const forexSignals = [
  { id:'eurusd', symbol:'EURUSD', coin:'fx', mark:'EU', direction:'Buy', tf:'15M', rr:'2.1R', score:84, state:'Session ranked', entry:'1.1642–1.1648', current:'1.1646', stop:'1.1618', tp1:'1.1698', tp2:'1.1732' },
  { id:'gbpusd', symbol:'GBPUSD', coin:'fx', mark:'GB', direction:'Sell', tf:'1H', rr:'2.0R', score:79, state:'Open' },
  { id:'usdjpy', symbol:'USDJPY', coin:'fx', mark:'UJ', direction:'Buy', tf:'30M', rr:'2.3R', score:72, state:'Wait' },
];

const activeTrades = [
  { ...cryptoSignals[0], market:'Crypto', status:'Approaching TP1', r:'+0.8R', progress:72, age:'1h 12m' },
  { ...cryptoSignals[3], market:'Crypto', status:'TP1 hit · TP2 live', r:'+1.4R', progress:84, age:'5h 28m' },
  { symbol:'AUDUSD', coin:'fx', mark:'AU', direction:'Short', tf:'1H', rr:'3.4R', score:86, status:'Waiting at entry', r:'0.0R', progress:24, age:'42m' },
  { symbol:'USDJPY', coin:'fx', mark:'UJ', direction:'Short', tf:'4H', rr:'2.2R', score:79, status:'Approaching SL', r:'-0.6R', progress:67, age:'11h', danger:true },
];
activeTrades[2].market='Forex'; activeTrades[3].market='Forex';

const historyRows = [
  { symbol:'EURUSD', coin:'fx', mark:'EU', score:84, meta:'FOREX · Today · TP2', r:'+3.2R' },
  { symbol:'BTCUSDT', coin:'btc', mark:'₿', score:91, meta:'CRYPTO · Yesterday · Partial Win', r:'+1.1R' },
  { symbol:'USDJPY', coin:'fx', mark:'UJ', score:76, meta:'FOREX · Yesterday · Breakeven', r:'0.0R' },
  { symbol:'ETHUSDT', coin:'eth', mark:'◆', score:82, meta:'CRYPTO · Aug 7 · Stopped', r:'-1.0R' },
];

const icons = {
  bell:'<svg viewBox="0 0 24 24"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/></svg>',
  home:'<svg viewBox="0 0 24 24"><path d="m3 11 9-8 9 8"/><path d="M5 10v10h14V10M9 20v-6h6v6"/></svg>',
  scan:'<svg viewBox="0 0 24 24"><path d="M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4"/><path d="M8 12h8"/></svg>',
  history:'<svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/></svg>',
  user:'<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>',
  trend:'<svg viewBox="0 0 24 24"><path d="m3 17 6-6 4 4 8-9"/><path d="M15 6h6v6"/></svg>',
  search:'<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg>',
};

const state = {
  screen: params.get('screen') || 'home',
  market: params.get('market') || 'Crypto',
  scan: params.get('state') || 'idle',
  progress: 0,
  toast: null,
  historyMarket:params.get('market') || 'All',
  historyStatus:'All',
};
let scanTimer;

function coin(item, size='') { return `<span class="coin ${item.coin || 'fx'} ${size}">${item.mark || item.symbol.slice(0,2)}</span>`; }
function marketSignals(){ return state.market === 'Forex' ? forexSignals : cryptoSignals; }
function marketActiveTrades(){ return activeTrades.filter(item => item.market === state.market); }
function header() { return `<header class="header"><div class="brand">SWIFTCHART</div><div class="header-actions"><div class="market-toggle" aria-label="Market"><button data-market="Crypto" class="${state.market==='Crypto'?'active':''}">Crypto</button><button data-market="Forex" class="${state.market==='Forex'?'active':''}">Forex</button></div><button class="icon-button" data-action="notify" aria-label="Notifications">${icons.bell}<i class="unread"></i></button></div></header>`; }

function signalRow(item) {
  const tone = /buy|long/i.test(item.direction) ? 'long' : 'short';
  return `<button class="signal-row" data-signal="${item.id || 'btc'}">${coin(item)}<span class="signal-copy"><strong>${item.symbol}</strong><span><b class="${tone}">${item.direction}</b> ${item.tf} · ${item.rr}</span></span><span class="score ${item.score<90?'pro':''}">${item.score}</span><span class="row-state">${item.state}</span></button>`;
}

function activeRow(item) {
  const tone = /buy|long/i.test(item.direction) ? 'long' : 'short';
  return `<button class="signal-row" data-signal="btc">${coin(item)}<span class="signal-copy"><strong>${item.symbol}</strong><span><b class="${tone}">${item.direction}</b> ${item.tf} · ${item.age}</span></span><span class="score ${item.score<90?'pro':''}">${item.score}</span><span class="progress-state ${item.danger?'loss':''}"><strong>${item.r}</strong><small>${item.status}</small><span class="micro-progress" style="--p:${item.progress}%"><i></i></span></span></button>`;
}

function home() {
  const isForex=state.market==='Forex';
  const hero=isForex
    ? `<section class="card hero forex-hero"><div class="hero-copy"><span class="eyebrow">Forex market</span><p>Live Forex sessions ranked against current market time.</p></div><div class="mini-chart session-card"><span class="eyebrow">Live session</span><strong>New York<br>Open</strong><small>Next: Tokyo in 2d 4h</small><b>19:47 UTC</b></div></section>`
    : `<section class="card hero"><div class="hero-copy"><span class="eyebrow">Market summary</span><strong class="hero-score">97</strong><p><strong>BTCUSDT</strong> is the strongest ranked setup right now.</p></div><div class="mini-chart"><span class="chart-line"></span><span class="chart-bars"><i></i><i></i><i></i><i></i></span></div></section><section class="card regime"><div><span class="eyebrow">Market regime</span><h2>Bullish, selective</h2><p>Momentum favors longs. Weak mid-range setups are filtered out.</p></div><span class="regime-icon">${icons.trend}</span></section>`;
  return `<main class="content screen-enter">${hero}<div class="section-head"><span>${isForex?'Top Forex setups':'Top opportunities'}</span><button data-action="go-scan">${isForex?'Session ranked':'Scan market'}</button></div><section class="signal-stack">${marketSignals().map(signalRow).join('')}</section><div class="section-head"><span>Active signals</span><button data-action="active">View active</button></div><section class="signal-stack">${marketActiveTrades().map(activeRow).join('')}</section></main>`;
}

function scanStateCard() {
  if (state.scan === 'idle') return '';
  if (state.scan === 'scanning') return `<section class="card state-card"><div class="state-card-head"><div><span class="eyebrow">Scanning market</span><h2>Checking structure…</h2></div><span class="score">${state.progress}%</span></div><p>Reading trend, liquidity, entry quality, and reward-to-risk without forcing a setup.</p><div class="scan-progress" style="--p:${state.progress}%"><i></i></div></section>`;
  const states = {
    trade:state.market==='Forex'?['Trade found','EURUSD · BUY · 15M','Session, spread, news risk, structure, and reward-to-risk checks are aligned.']:['Trade found','BTCUSDT · LONG · 4H','Structure held after a liquidity sweep. The entry zone is active and risk remains defined.'],
    wait:['Wait for retest','AUDUSD · SHORT · 1H','Bias is valid, but price has not returned to the qualified entry zone.'],
    no:['No trade','Market scanned · no qualified setup','Current reward-to-risk and structure quality do not meet SwiftChart thresholds.'],
    error:['Scan unavailable','Latest candles could not be verified','No trading decision was generated. Retry or review the last cached market snapshot.'],
  };
  const x=states[state.scan]||states.trade;
  return `<section class="card state-card"><div class="state-card-head"><div><span class="eyebrow">${x[0]}</span><h2>${x[1]}</h2></div>${state.scan==='trade'?'<span class="score">92</span>':''}</div><p>${x[2]}</p><div class="state-actions">${state.scan==='trade'?'<button data-signal="btc">Open Signal</button>':'<button data-action="reset-scan">Adjust scan</button>'}<button data-action="scan-state">Preview states</button></div></section>`;
}

function scan() {
  const isForex=state.market==='Forex';
  const rows=isForex?forexSignals:cryptoSignals.concat([{id:'inj',symbol:'INJUSDT',coin:'eth',mark:'I',direction:'Long',tf:'4H',rr:'2.4R',score:74,state:'Open'}]);
  return `<main class="content screen-enter"><section class="card scan-hero"><div><span class="eyebrow">${isForex?'Forex scan':'Scan'}</span><h1>${isForex?'Session filter':'Find clean setups'}</h1><p>${isForex?'Validate pair, spread, session, score, and news risk before ranking setups.':'Search one coin or rank the market in seconds.'}</p></div><button class="primary" data-action="run-scan" ${state.scan==='scanning'?'disabled':''}>${state.scan==='scanning'?'Scanning…':isForex?'Scan Forex':'Scan Market'}</button></section><form class="search" data-action="search">${icons.search}<input aria-label="${isForex?'Search pair':'Search coin'}" placeholder="${isForex?'Search pair (EURUSD…)':'Search coin (BTC, ETH, SOL…)'}"/><button>Scan</button></form><div class="filters"><button class="active">${isForex?'EUR/USD':'65–75'}</button><button>${isForex?'15M':'4H'}</button><button>${isForex?'New York':'All Exchanges'}</button></div>${scanStateCard()}<div class="section-head"><span>${isForex?'Forex results':'Premium results'}</span><button data-action="scan-state">States</button></div><section class="signal-stack">${rows.map(signalRow).join('')}</section></main>`;
}

function detail() {
  const s=marketSignals()[0], isForex=state.market==='Forex';
  return `<main class="content screen-enter"><section class="detail-head">${coin(s)}<div><span class="eyebrow">${isForex?'Forex trade analysis':'Trade analysis'}</span><h1>${s.symbol}</h1><p>${isForex?'Session-qualified setup':'High-conviction setup'} · updated 2m ago</p></div><span class="score-ring" style="--score-angle:${s.score}%"><strong>${s.score}</strong></span></section><section class="stat-strip"><div class="stat"><span>Direction</span><strong class="long">${s.direction.toUpperCase()}</strong></div><div class="stat"><span>Timeframe</span><strong>${s.tf}</strong></div><div class="stat"><span>R/R</span><strong>${s.rr}</strong></div></section><section class="card trade-plan"><div class="plan-head"><span class="eyebrow">Trade plan</span><strong>Entry active</strong></div><div class="plan-live"><span>Current price</span><strong>${s.current}</strong></div><div class="ladder"><i class="sl"></i><i class="entry"></i><i class="current"></i><i class="tp1"></i><i class="tp2"></i></div><div class="ladder-values"><div><span>SL</span><strong class="bad">${s.stop}</strong></div><div><span>Entry</span><strong>${s.entry.split('–')[0]}</strong></div><div><span>Current</span><strong class="live">${s.current}</strong></div><div><span>TP1</span><strong class="good">${s.tp1}</strong></div><div><span>TP2</span><strong class="good">${s.tp2}</strong></div></div></section><section class="card reasoning"><span class="eyebrow">Reasoning</span><p>${isForex?'EURUSD is ranked for the New York session after spread, news-risk, structure, and reward-to-risk checks aligned. The stop remains beyond structural invalidation.':'BTCUSDT reclaimed the 4H range after sweeping sell-side liquidity. The retest held above displacement, while the stop remains beyond structural invalidation.'}</p></section><div class="detail-actions"><button class="secondary" data-action="back">Close</button><button class="primary" data-action="take">Take Trade</button></div></main>`;
}

function active() {
  return `<main class="content screen-enter"><section class="card scan-hero"><div><span class="eyebrow">Active signals</span><h1>Track what matters</h1><p>Entry, TP, stop, and stale-state changes in the familiar signal format.</p></div><button class="primary" data-action="back">Home</button></section><section class="signal-stack">${marketActiveTrades().map(activeRow).join('')}</section><section class="empty"><h2>Nothing else needs action</h2><p>SwiftChart will surface the next meaningful state change here.</p></section></main>`;
}

function historyRow(item){ const tone=item.r.startsWith('-')?'loss':item.r==='0.0R'?'':'profit'; return `<button class="history-row" data-signal="btc">${coin(item)}<span><strong>${item.symbol}</strong><p>${item.meta}</p></span><em class="${tone}">${item.r}</em></button>`; }
function historyScreen(){ const rows=historyRows.filter(r=>state.historyMarket==='All'||(state.historyMarket==='Crypto'?r.meta.startsWith('CRYPTO'):r.meta.startsWith('FOREX'))); const total=state.historyMarket==='Forex'?'+3.2R':state.historyMarket==='Crypto'?'+0.1R':'+3.3R'; return `<main class="content screen-enter"><section class="card history-hero"><span class="eyebrow">${state.historyMarket==='All'?'Total R':`${state.historyMarket} total R`}</span><strong>${total}</strong><p>${rows.length} tracked ideas · market filter active</p></section><div class="filter-row">${['All','Crypto','Forex'].map(x=>`<button data-history-market="${x}" class="${state.historyMarket===x?'active':''}">${x}</button>`).join('')}</div><div class="filter-row">${['All','Open','TP1','TP2','SL','Expired'].map(x=>`<button data-history-status="${x}" class="${state.historyStatus===x?'active':''}">${x}</button>`).join('')}</div><section class="card timeline"><span class="timeline-title">Trade timeline</span>${rows.length?rows.map(historyRow).join(''):'<div class="empty"><h2>No matching trades</h2><p>Try another market or outcome filter.</p></div>'}</section></main>`; }

function account(){ return `<main class="content screen-enter"><section class="card history-hero"><span class="eyebrow">Profile</span><strong style="font-size:30px;color:var(--ink)">doflam</strong><p>SwiftChart Pro · 5 scans today</p></section><section class="card timeline"><span class="timeline-title">Account</span>${['Notifications','Appearance','Trading settings','Support','Log out'].map(x=>`<button class="history-row"><span></span><span><strong>${x}</strong><p>${x==='Appearance'?'Dark mode':x==='Notifications'?'Trade alerts on':'SwiftChart settings'}</p></span><em>›</em></button>`).join('')}</section></main>`; }

function nav(){ if(['detail','active'].includes(state.screen)) return ''; const items=[['home','home','Home'],['scan','scan','Scan'],['history','history','History'],['account','user','Account']]; const activeIndex=Math.max(0,items.findIndex(x=>x[0]===state.screen)); return `<nav class="bottom-nav" style="--active:${activeIndex}" aria-label="Primary">${items.map(([screen,icon,label])=>`<button data-screen="${screen}" class="${state.screen===screen?'active':''}">${icons[icon]}<span>${label}</span></button>`).join('')}</nav>`; }
function toast(){ return state.toast?`<div class="toast" role="status"><span class="score">✓</span><div><strong>${state.toast[0]}</strong><p>${state.toast[1]}</p></div><button data-action="dismiss">×</button></div>`:''; }
function setUrl(){ const u=new URL(location.href);u.searchParams.set('screen',state.screen);u.searchParams.set('market',state.market);if(state.screen==='scan')u.searchParams.set('state',state.scan);else u.searchParams.delete('state');history.replaceState(null,'',u); }
function render(){ const screens={home,scan,detail,active,history:historyScreen,account};root.innerHTML=`<div class="canvas"><div class="app">${header()}${(screens[state.screen]||home)()}${nav()}</div>${toast()}</div>`;setUrl(); }
function navigate(screen){state.screen=screen;window.scrollTo(0,0);render();}
function showToast(title,body){state.toast=[title,body];render();setTimeout(()=>{state.toast=null;render()},2600);}
function runScan(){clearInterval(scanTimer);state.scan='scanning';state.progress=12;render();scanTimer=setInterval(()=>{state.progress=Math.min(94,state.progress+(state.progress<60?18:9));render();if(state.progress>=94){clearInterval(scanTimer);setTimeout(()=>{state.scan='trade';render()},260)}},300);}

document.addEventListener('click',e=>{const t=e.target.closest('button');if(!t)return;if(t.dataset.screen)return navigate(t.dataset.screen);if(t.dataset.market){state.market=t.dataset.market;state.historyMarket=t.dataset.market;return render()}if(t.dataset.signal)return navigate('detail');if(t.dataset.historyMarket){state.historyMarket=t.dataset.historyMarket;return render()}if(t.dataset.historyStatus){state.historyStatus=t.dataset.historyStatus;return render()}const actions={notify:()=>showToast('Notifications up to date','No new state changes require attention.'),'go-scan':()=>navigate('scan'),active:()=>navigate('active'),'run-scan':runScan,'reset-scan':()=>{state.scan='idle';render()},'scan-state':()=>{state.scan=state.scan==='trade'?'wait':state.scan==='wait'?'no':state.scan==='no'?'error':'trade';render()},back:()=>navigate('home'),take:()=>showToast('Prototype only','No trade or account mutation occurred.'),dismiss:()=>{state.toast=null;render()}};actions[t.dataset.action]?.();});
document.addEventListener('submit',e=>{e.preventDefault();runScan()});
window.scrollTo(0,0);render();
