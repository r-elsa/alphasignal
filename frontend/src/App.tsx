import { useEffect, useRef, useState } from "react";
import "./App.css";

interface Ratings {
  strongBuy: number;
  buy: number;
  hold: number;
  sell: number;
  strongSell: number;
}

interface Stock {
  name: string;
  sector: string | null;
  currency: string;
  price: number;
  change: number | null;
  change_pct: number | null;
  as_of: string | null;
  market_cap: number | null;
  week52_low: number | null;
  week52_high: number | null;
  trailing_pe: number | null;
  forward_pe: number | null;
  target_mean: number | null;
  target_low: number | null;
  target_high: number | null;
  upside_pct: number | null;
  recommendation: string | null;
  analyst_count: number | null;
  ratings: Ratings | null;
  next_earnings: string | null;
  business_summary: string;
  financials: Financials;
  earnings_history: EarningsQuarter[];
  links: Record<"income_statement" | "balance_sheet" | "cash_flow" | "sec_filings" | "company_website", string | null>;
}

interface Financials {
  revenue: number | null;
  revenue_growth_pct: number | null;
  earnings_growth_pct: number | null;
  profit_margin_pct: number | null;
  gross_margin_pct: number | null;
  total_cash: number | null;
  total_debt: number | null;
  free_cash_flow: number | null;
  dividend_yield_pct: number | null;
  payout_ratio_pct: number | null;
  beta: number | null;
}

interface EarningsQuarter {
  quarter_end: string;
  eps_actual: number;
  eps_estimate: number;
  surprise_pct: number;
}

interface Cited {
  source_ids: number[];
}

interface Development extends Cited {
  headline: string;
  why_it_matters: string;
}

interface Point extends Cited {
  text: string;
}

interface Brief {
  company_description: string;
  tldr: string;
  developments: Development[];
  bull_case: Point[];
  bear_case: Point[];
  watch: string[];
  sentiment: { positive: number; negative: number; neutral: number; summary: string };
}

interface Article {
  id: number;
  title: string;
  link: string;
  source: string;
  published: string | null;
  sentiment: "positive" | "negative" | "neutral";
}

interface Peer {
  ticker: string;
  name: string;
  market_cap: number | null;
  change_1y_pct: number | null;
  trailing_pe: number | null;
  forward_pe: number | null;
  revenue_growth_pct: number | null;
  profit_margin_pct: number | null;
}

interface Peers {
  source: "finnhub" | "ai";
  companies: Peer[];
  medians: Partial<Record<"forward_pe" | "trailing_pe" | "revenue_growth_pct" | "profit_margin_pct" | "change_1y_pct", number | null>>;
}

interface Result {
  ticker: string;
  stock: Stock;
  brief: Brief;
  peers: Peers;
  articles: Article[];
  generated_at: string;
}

// ── Formatting ────────────────────────────────────────────────────────────────
const money = (n: number | null) =>
  n == null ? "—" : "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const signed = (n: number, suffix = "") => (n > 0 ? "+" : "") + n.toFixed(2) + suffix;

const bigNumber = (n: number | null) => {
  if (n == null) return "—";
  if (n >= 1e12) return "$" + (n / 1e12).toFixed(2) + "T";
  if (n >= 1e9) return "$" + (n / 1e9).toFixed(1) + "B";
  if (n >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M";
  return "$" + n.toLocaleString();
};

const prettyRating = (r: string | null) =>
  r ? r.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()) : "—";

const shortDate = (iso: string | null) =>
  iso ? new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "";

const timeAgo = (iso: string) => {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  return hours < 24 ? `${hours} h ago` : shortDate(iso);
};

// Plain-English explanations shown behind the ⓘ icons
const EXPLAIN: Record<string, string> = {
  price: "The price of one share at the last trade, and how much it moved since yesterday's close.",
  target:
    "The average price analysts expect the stock to reach within about 12 months. Upside is how far that is above today's price. The range shows the most pessimistic and most optimistic analyst.",
  consensus:
    "The average recommendation of the analysts who cover this stock. Note: analysts say Buy far more often than Sell, so a Hold is already fairly cautious.",
  marketCap:
    "The total value of all the company's shares (share price × number of shares). It tells you how big the company is.",
  range52:
    "The lowest and highest price of the past year. Shows whether the stock is near its peak or near its low.",
  pe: "Price-to-earnings: how many dollars investors pay for $1 of the company's yearly profit. TTM uses the last 12 months of actual profit, forward uses analysts' estimates for next year. A higher number means investors expect more growth. The S&P 500 average is usually around 20–25.",
  earnings:
    "The date the company reports its quarterly results. The stock often moves sharply around this day.",
  revenueGrowth:
    "How much the company's sales grew compared with a year ago. Fast-growing companies often trade at higher prices relative to profit.",
  profitMargin:
    "How much of every $100 in sales is left as profit after all costs. Higher is better; negative means the company lost money.",
  cashDebt:
    "Cash the company holds versus money it has borrowed. More cash than debt gives a safety cushion. Some businesses, like banks and car makers with lending arms, normally carry a lot of debt.",
  freeCashFlow:
    "Cash left over after running the business and investing in it. Positive means the company funds itself; negative means it needs savings or borrowing.",
  dividend:
    "Cash paid to shareholders each year, as a % of the share price. The payout ratio is the share of profit paid out; above 100% is hard to sustain.",
  beta:
    "How much the stock tends to move compared with the overall market. 1 = same as the market, 2 = about twice as much up and down.",
  earningsHistory:
    "Each quarter, analysts estimate the company's profit per share (EPS). Beating the estimate usually pleases investors; missing it often pushes the stock down.",
  sentiment:
    "Whether each news article reads as good, bad or neutral for the stock. This is a rough mood indicator, not a prediction.",
};

type Verdict = "good" | "ok" | "bad" | "neutral";

const pct = (n: number) => (n > 0 ? "+" : "") + n.toFixed(1) + "%";

const monthYear = (iso: string) =>
  new Date(iso + "T00:00:00").toLocaleDateString("en-US", { month: "short", year: "numeric" });

const rangeContext = (price: number, low: number | null, high: number | null) => {
  if (low == null || high == null || high <= low) return null;
  const pos = Math.min(1, Math.max(0, (price - low) / (high - low)));
  const text = pos >= 0.9 ? "Near its 1-year high" : pos <= 0.1 ? "Near its 1-year low" : `${Math.round(pos * 100)}% of the way from low to high`;
  return { pos, text };
};

const peContext = (trailing: number | null, forward: number | null, margin: number | null, peerMedian?: number | null) => {
  if (trailing == null && margin != null && margin < 0) return "Not profitable over the last year, so P/E can't be calculated";
  const pe = forward ?? trailing;
  if (pe == null) return null;
  const level =
    peerMedian != null && forward != null
      ? forward < peerMedian * 0.8
        ? `Cheaper than its competitors (median ${peerMedian})`
        : forward > peerMedian * 1.2
        ? `Pricier than its competitors (median ${peerMedian})`
        : `In line with its competitors (median ${peerMedian})`
      : pe < 15
      ? "Cheaper than the market average (~22)"
      : pe <= 30
      ? "Around the market average (~22)"
      : "Pricier than the market average (~22)";
  const growth =
    trailing != null && forward != null && forward < trailing * 0.8
      ? "; analysts expect profits to grow"
      : trailing != null && forward != null && forward > trailing * 1.1
      ? "; analysts expect profits to shrink"
      : "";
  return level + growth;
};

const capContext = (cap: number | null) =>
  cap == null ? null : cap >= 200e9 ? "One of the largest companies" : cap >= 10e9 ? "Large company" : cap >= 2e9 ? "Mid-sized company" : "Small company";

const targetContext = (s: Stock) => {
  if (s.target_low == null || s.target_high == null || !s.target_mean) return null;
  const spread = (s.target_high - s.target_low) / s.target_mean;
  return spread > 0.6 ? "Analysts disagree a lot" : spread < 0.25 ? "Analysts broadly agree" : null;
};

const healthItems = (f: Financials) => {
  const items: { label: string; value: string; note: string; verdict: Verdict; info: string }[] = [];
  if (f.revenue_growth_pct != null) {
    const g = f.revenue_growth_pct;
    items.push({
      label: "Sales growth",
      value: pct(g) + " vs. last year",
      note: g > 10 ? "Growing fast" : g >= 0 ? "Growing slowly" : "Sales are shrinking",
      verdict: g > 10 ? "good" : g >= 0 ? "ok" : "bad",
      info: EXPLAIN.revenueGrowth,
    });
  }
  if (f.profit_margin_pct != null) {
    const m = f.profit_margin_pct;
    items.push({
      label: "Profit margin",
      value: m.toFixed(1) + "%",
      note: m < 0 ? "Losing money" : `Keeps $${Math.round(m)} of every $100 in sales as profit`,
      verdict: m > 15 ? "good" : m >= 0 ? "ok" : "bad",
      info: EXPLAIN.profitMargin,
    });
  }
  if (f.total_cash != null && f.total_debt != null) {
    const ratio = f.total_debt / Math.max(f.total_cash, 1);
    items.push({
      label: "Cash vs. debt",
      value: `${bigNumber(f.total_cash)} / ${bigNumber(f.total_debt)}`,
      note: ratio <= 1 ? "More cash than debt" : ratio <= 3 ? "More debt than cash" : "Debt is much larger than cash",
      verdict: ratio <= 1 ? "good" : ratio <= 3 ? "ok" : "bad",
      info: EXPLAIN.cashDebt,
    });
  }
  if (f.free_cash_flow != null) {
    const positive = f.free_cash_flow > 0;
    items.push({
      label: "Free cash flow",
      value: (positive ? "" : "−") + bigNumber(Math.abs(f.free_cash_flow)) + " / yr",
      note: positive ? "Generates cash after investing" : "Spending more cash than it brings in",
      verdict: positive ? "good" : "bad",
      info: EXPLAIN.freeCashFlow,
    });
  }
  const y = f.dividend_yield_pct;
  items.push({
    label: "Dividend",
    value: y ? y.toFixed(2) + "% per year" : "None",
    note: !y
      ? "Doesn't pay shareholders a dividend"
      : f.profit_margin_pct != null && f.profit_margin_pct < 0
      ? "Paying a dividend while losing money — it could be cut"
      : f.payout_ratio_pct != null && f.payout_ratio_pct > 100
      ? "Pays out more than it earns — may not last"
      : f.payout_ratio_pct != null
      ? `Pays out ${Math.round(f.payout_ratio_pct)}% of profit`
      : "Pays a regular dividend",
    verdict:
      y && ((f.payout_ratio_pct != null && f.payout_ratio_pct > 100) || (f.profit_margin_pct != null && f.profit_margin_pct < 0))
        ? "ok"
        : "neutral",
    info: EXPLAIN.dividend,
  });
  if (f.beta != null) {
    items.push({
      label: "Volatility",
      value: `Beta ${f.beta.toFixed(2)}`,
      note:
        f.beta > 1.3
          ? `Moves about ${f.beta.toFixed(1)}× as much as the market — bigger swings`
          : f.beta < 0.7
          ? "Moves less than the market — calmer"
          : "Moves roughly with the market",
      verdict: "neutral",
      info: EXPLAIN.beta,
    });
  }
  return items;
};

const VERDICT_ICON: Record<Verdict, string> = { good: "✓", ok: "•", bad: "!", neutral: "" };

const Info = ({ text }: { text: string }) => (
  <span className="info" tabIndex={0} aria-label={text}>
    ⓘ<span className="info-tip" role="tooltip">{text}</span>
  </span>
);

const toneClass = (n: number | null) => (n == null ? "" : n > 0 ? "green" : n < 0 ? "red" : "");

const API = "http://localhost:8000";

// ── Price chart ───────────────────────────────────────────────────────────────
interface PricePoint {
  date: string;
  close: number;
}

const RANGES = [
  { id: "1mo", label: "1M", long: "1 month" },
  { id: "6mo", label: "6M", long: "6 months" },
  { id: "1y", label: "1Y", long: "1 year" },
  { id: "5y", label: "5Y", long: "5 years" },
];

const fullDate = (iso: string) =>
  new Date(iso + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });

function PriceChart({ ticker, target }: { ticker: string; target: number | null }) {
  const [range, setRange] = useState("1y");
  const [points, setPoints] = useState<PricePoint[]>([]);
  const [failed, setFailed] = useState(false);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    setFailed(false);
    fetch(`${API}/history/${encodeURIComponent(ticker)}?range=${range}`)
      .then(r => (r.ok ? r.json() : Promise.reject()))
      .then(data => !cancelled && setPoints(data))
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [ticker, range]);

  const W = 700, H = 220, PAD_L = 8, PAD_R = 8, PAD_T = 12, PAD_B = 12;
  const closes = points.map(p => p.close);
  const showTarget = target != null && closes.length > 0;
  const lo = Math.min(...closes, ...(showTarget ? [target!] : []));
  const hi = Math.max(...closes, ...(showTarget ? [target!] : []));
  const pad = (hi - lo) * 0.08 || 1;
  const yMin = lo - pad, yMax = hi + pad;
  const x = (i: number) => PAD_L + (i / Math.max(points.length - 1, 1)) * (W - PAD_L - PAD_R);
  const y = (v: number) => PAD_T + (1 - (v - yMin) / (yMax - yMin)) * (H - PAD_T - PAD_B);

  const first = closes[0], last = closes[closes.length - 1];
  const up = last >= first;
  const change = first ? ((last - first) / first) * 100 : 0;
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.close).toFixed(1)}`).join(" ");
  const area = points.length ? `${line} L${x(points.length - 1)},${H - PAD_B} L${x(0)},${H - PAD_B} Z` : "";

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const rel = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.round(((rel - PAD_L) / (W - PAD_L - PAD_R)) * (points.length - 1));
    setHover(Math.min(points.length - 1, Math.max(0, i)));
  };

  const shown = hover != null ? points[hover] : points[points.length - 1];
  const rangeLong = RANGES.find(r => r.id === range)!.long;

  return (
    <div className="card chart-card">
      <div className="chart-header">
        <div>
          <div className="brief-label">Price history</div>
          {shown && (
            <div className="chart-readout">
              <strong>{money(shown.close)}</strong>
              <span>{hover != null ? fullDate(shown.date) : <span className={up ? "green-text" : "red-text"}>{pct(change)} over {rangeLong}</span>}</span>
            </div>
          )}
        </div>
        <div className="range-tabs" role="tablist">
          {RANGES.map(r => (
            <button key={r.id} role="tab" aria-selected={range === r.id} className={range === r.id ? "active" : ""} onClick={() => setRange(r.id)}>
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {failed && <p className="muted">Price history isn't available right now.</p>}
      {!failed && points.length > 1 && (
        <>
          <svg
            className="chart"
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="none"
            onPointerMove={onMove}
            onPointerLeave={() => setHover(null)}
            role="img"
            aria-label={`${ticker} price over ${rangeLong}: from ${money(first)} to ${money(last)}`}
          >
            <path d={area} className={up ? "chart-area up" : "chart-area down"} />
            <path d={line} className={up ? "chart-line up" : "chart-line down"} vectorEffect="non-scaling-stroke" />
            {showTarget && (
              <line x1={PAD_L} x2={W - PAD_R} y1={y(target!)} y2={y(target!)} className="chart-target" vectorEffect="non-scaling-stroke" />
            )}
            {hover != null && (
              <>
                <line x1={x(hover)} x2={x(hover)} y1={PAD_T} y2={H - PAD_B} className="chart-cursor" vectorEffect="non-scaling-stroke" />
                <circle cx={x(hover)} cy={y(points[hover].close)} r={4} className={up ? "chart-dot up" : "chart-dot down"} />
              </>
            )}
          </svg>
          <div className="chart-footer">
            <span>{fullDate(points[0].date)}</span>
            {showTarget && <span className="chart-target-label">- - Avg. analyst target {money(target)}</span>}
            <span>{fullDate(points[points.length - 1].date)}</span>
          </div>
        </>
      )}
    </div>
  );
}

// ── Competitor comparison ─────────────────────────────────────────────────────
const peDisplay = (pe: number | null) => (pe == null ? "—" : pe <= 0 ? "loss" : pe.toFixed(1));
const pctDisplay = (n: number | null) => (n == null ? "—" : pct(n));

function peerSummary(self: Peer, m: Peers["medians"]): string[] {
  const lines: string[] = [];
  if (self.forward_pe != null && self.forward_pe > 0 && m.forward_pe != null) {
    lines.push(
      self.forward_pe < m.forward_pe * 0.8
        ? "Priced cheaper than most competitors relative to expected profit."
        : self.forward_pe > m.forward_pe * 1.2
        ? "Priced higher than most competitors relative to expected profit — investors expect more from it."
        : "Priced roughly in line with competitors."
    );
  }
  if (self.revenue_growth_pct != null && m.revenue_growth_pct != null) {
    const d = self.revenue_growth_pct - m.revenue_growth_pct;
    lines.push(d > 5 ? "Growing sales faster than its competitors." : d < -5 ? "Growing sales more slowly than its competitors." : "Growing sales at a similar pace to competitors.");
  }
  if (self.profit_margin_pct != null && m.profit_margin_pct != null) {
    const d = self.profit_margin_pct - m.profit_margin_pct;
    lines.push(d > 5 ? "More profitable than most competitors." : d < -5 ? "Less profitable than most competitors." : "About as profitable as its competitors.");
  }
  return lines;
}

function PeersCard({ result }: { result: Result }) {
  const { peers, stock: s, ticker } = result;
  if (!peers || peers.companies.length === 0) return null;
  const self: Peer = {
    ticker,
    name: s.name,
    market_cap: s.market_cap,
    change_1y_pct: null,
    trailing_pe: s.trailing_pe,
    forward_pe: s.forward_pe,
    revenue_growth_pct: s.financials.revenue_growth_pct,
    profit_margin_pct: s.financials.profit_margin_pct,
  };
  const rows = [self, ...peers.companies];
  const summary = peerSummary(self, peers.medians);

  return (
    <div className="card peers-card">
      <div className="brief-label">Compared with competitors</div>
      {summary.length > 0 && (
        <ul className="peer-summary">
          {summary.map(line => <li key={line}>{line}</li>)}
        </ul>
      )}
      <div className="table-scroll">
        <table className="peers-table">
          <thead>
            <tr>
              <th>Company</th>
              <th>Size <Info text={EXPLAIN.marketCap} /></th>
              <th>P/E (fwd) <Info text={EXPLAIN.pe} /></th>
              <th>Sales growth <Info text={EXPLAIN.revenueGrowth} /></th>
              <th>Profit margin <Info text={EXPLAIN.profitMargin} /></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p, i) => (
              <tr key={p.ticker} className={i === 0 ? "self" : ""}>
                <td>
                  <strong>{p.ticker}</strong>
                  <span className="peer-name">{p.name}</span>
                </td>
                <td>{bigNumber(p.market_cap)}</td>
                <td>{peDisplay(p.forward_pe)}</td>
                <td className={toneClass(p.revenue_growth_pct)}>{pctDisplay(p.revenue_growth_pct)}</td>
                <td className={toneClass(p.profit_margin_pct && p.profit_margin_pct < 0 ? -1 : 0)}>
                  {p.profit_margin_pct == null ? "—" : p.profit_margin_pct.toFixed(1) + "%"}
                </td>
              </tr>
            ))}
            <tr className="median">
              <td>Competitor median</td>
              <td />
              <td>{peers.medians.forward_pe ?? "—"}</td>
              <td>{pctDisplay(peers.medians.revenue_growth_pct ?? null)}</td>
              <td>{peers.medians.profit_margin_pct == null ? "—" : peers.medians.profit_margin_pct + "%"}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p className="peer-source">
        {peers.source === "finnhub"
          ? "Competitors from Finnhub's industry peer list (US-listed companies only)."
          : "Competitors suggested by AI, so the list may be imperfect."}{" "}
        "loss" means the company is expected to lose money, so P/E doesn't apply.
      </p>
    </div>
  );
}

// ── Progress steps ────────────────────────────────────────────────────────────
const STEPS = [
  { id: "market", label: "Fetching price and analyst data" },
  { id: "news", label: "Searching the news" },
  { id: "peers", label: "Finding competitors" },
  { id: "brief", label: "Writing the brief" },
];

type StepState = Record<string, { status: "running" | "done"; detail: string }>;

interface Suggestion {
  symbol: string;
  name: string;
  exchange: string;
  type: string;
}

export default function App() {
  const [ticker, setTicker] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [history, setHistory] = useState<string[]>([]);
  const [steps, setSteps] = useState<StepState>({});
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [highlight, setHighlight] = useState(-1);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const streamRef = useRef<EventSource | null>(null);
  const latestQuery = useRef("");

  // Autocomplete: search companies by name or ticker as the user types
  useEffect(() => {
    const q = ticker.trim();
    latestQuery.current = q;
    if (q.length < 1 || !showSuggestions) {
      setSuggestions([]);
      return;
    }
    const timer = setTimeout(() => {
      fetch(`${API}/search?q=${encodeURIComponent(q)}`)
        .then(r => (r.ok ? r.json() : []))
        .then((data: Suggestion[]) => {
          if (latestQuery.current === q) {
            setSuggestions(data);
            setHighlight(-1);
          }
        })
        .catch(() => setSuggestions([]));
    }, 250);
    return () => clearTimeout(timer);
  }, [ticker, showSuggestions]);

  const analyze = (t?: string, refresh = false) => {
    const target = (t || ticker).trim().toUpperCase();
    if (!target) return;
    setTicker(target);
    setShowSuggestions(false);
    setSuggestions([]);
    window.history.replaceState(null, "", `?t=${encodeURIComponent(target)}`);
    setLoading(true);
    setError("");
    setResult(null);
    setSteps({});

    // Progress arrives as Server-Sent Events, then the final result
    streamRef.current?.close();
    const es = new EventSource(`${API}/analyze/${encodeURIComponent(target)}/stream${refresh ? "?refresh=true" : ""}`);
    streamRef.current = es;
    const finish = () => {
      es.close();
      setLoading(false);
    };
    es.addEventListener("step", e => {
      const { step, status, detail } = JSON.parse((e as MessageEvent).data);
      setSteps(prev => ({ ...prev, [step]: { status, detail } }));
    });
    es.addEventListener("result", e => {
      setResult(JSON.parse((e as MessageEvent).data));
      setHistory(prev => [target, ...prev.filter(h => h !== target)].slice(0, 6));
      finish();
    });
    es.addEventListener("failed", e => {
      const { message, not_found } = JSON.parse((e as MessageEvent).data);
      setError(not_found ? `No market data found for ${target}. Check the ticker or search by company name.` : message);
      finish();
    });
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED || streamRef.current === es) {
        setError("Can't reach the server. Is the backend running?");
        finish();
      }
    };
  };

  const onSearchKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" && suggestions.length) {
      e.preventDefault();
      setHighlight(h => (h + 1) % suggestions.length);
    } else if (e.key === "ArrowUp" && suggestions.length) {
      e.preventDefault();
      setHighlight(h => (h <= 0 ? suggestions.length - 1 : h - 1));
    } else if (e.key === "Escape") {
      setShowSuggestions(false);
    } else if (e.key === "Enter") {
      const typed = ticker.trim().toUpperCase();
      if (highlight >= 0 && suggestions[highlight]) return analyze(suggestions[highlight].symbol);
      // "tesla" + Enter should find TSLA, but an exact ticker match always wins
      const exact = suggestions.find(s => s.symbol === typed);
      analyze(exact ? exact.symbol : suggestions[0] ? suggestions[0].symbol : typed);
    }
  };

  // Open a shared link like ?t=NVDA straight away
  const loadedFromUrl = useRef(false);
  useEffect(() => {
    if (loadedFromUrl.current) return;
    loadedFromUrl.current = true;
    const t = new URLSearchParams(window.location.search).get("t");
    if (t) analyze(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const consensusClass = (c: string | null) => {
    if (!c) return "stat-value";
    if (c.includes("buy")) return "stat-value green";
    if (c.includes("sell")) return "stat-value red";
    return "stat-value amber";
  };

  const Cites = ({ ids }: { ids: number[] }) => {
    if (!result) return null;
    if (ids.length === 0) return <span className="cite-background">background</span>;
    return (
      <>
        {ids.map(id => {
          const a = result.articles.find(x => x.id === id);
          return a ? (
            <a key={id} className="cite" href={a.link} target="_blank" rel="noreferrer" title={a.title}>
              {id}
            </a>
          ) : null;
        })}
      </>
    );
  };

  const RatingsBar = ({ r }: { r: Ratings }) => {
    const buy = r.strongBuy + r.buy;
    const sell = r.sell + r.strongSell;
    const total = buy + r.hold + sell || 1;
    return (
      <div className="ratings">
        <div className="ratings-bar">
          <span className="seg buy" style={{ width: `${(buy / total) * 100}%` }} />
          <span className="seg hold" style={{ width: `${(r.hold / total) * 100}%` }} />
          <span className="seg sell" style={{ width: `${(sell / total) * 100}%` }} />
        </div>
        <div className="ratings-legend">
          {buy} Buy · {r.hold} Hold · {sell} Sell
        </div>
      </div>
    );
  };

  const s = result?.stock;
  const b = result?.brief;

  return (
    <div>
      <header className="header">
        <span className="header-icon">⚡</span>
        <span className="header-logo">AlphaSignal</span>
        <span className="header-subtitle">AI Investment Intelligence</span>
      </header>

      <main className="main">
        <div className="hero">
          <h1>Enter a ticker. Get everything that matters.</h1>
          <p>Latest news, analyst consensus and an AI brief with every claim linked to its source.</p>
        </div>

        <div className="search-bar">
          <div className="search-input-wrap">
            <input
              value={ticker}
              onChange={e => {
                setTicker(e.target.value);
                setShowSuggestions(true);
              }}
              onKeyDown={onSearchKey}
              onBlur={() => setTimeout(() => setShowSuggestions(false), 150)}
              placeholder="Company or ticker — e.g. Nvidia, AAPL, Tesla"
              role="combobox"
              aria-expanded={showSuggestions && suggestions.length > 0}
              aria-controls="search-suggestions"
              aria-autocomplete="list"
            />
            {showSuggestions && suggestions.length > 0 && (
              <ul className="suggestions" id="search-suggestions" role="listbox">
                {suggestions.map((sug, i) => (
                  <li
                    key={sug.symbol}
                    role="option"
                    aria-selected={i === highlight}
                    className={i === highlight ? "active" : ""}
                    onMouseDown={e => {
                      e.preventDefault();
                      analyze(sug.symbol);
                    }}
                    onMouseEnter={() => setHighlight(i)}
                  >
                    <span className="sug-symbol">{sug.symbol}</span>
                    <span className="sug-name">{sug.name}</span>
                    <span className="sug-exchange">
                      {sug.exchange}
                      {sug.type === "ETF" && " · ETF"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <button onClick={() => analyze()} disabled={loading}>
            {loading ? "Analysing..." : "Analyse"}
          </button>
        </div>

        {history.length > 0 && (
          <div className="history">
            <span className="history-label">Recent:</span>
            {history.map((h, i) => (
              <button
                key={i}
                className="history-tag"
                onClick={() => analyze(h)}
                disabled={loading}
              >
                {h}
              </button>
            ))}
          </div>
        )}

        {error && <div className="error">{error}</div>}

        {loading && (
          <div className="loading">
            <div className="loading-icon">⚡</div>
            <p>Agents are working...</p>
            <ul className="steps">
              {STEPS.map(st => {
                const state = steps[st.id];
                return (
                  <li key={st.id} className={state?.status ?? "pending"}>
                    <span className="step-icon">
                      {state?.status === "done" ? "✓" : state?.status === "running" ? <span className="spinner" /> : "○"}
                    </span>
                    <span className="step-label">{st.label}</span>
                    {state?.detail && <span className="step-detail">{state.detail}</span>}
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        {result && s && b && !loading && (
          <div className="results">
            <details className="card how-to">
              <summary>New to investing? How to read this page</summary>
              <ul>
                <li>
                  <strong>The top card</strong> shows the facts: today's price, what analysts expect, and a few key
                  numbers. Hover or tap any <span className="info-inline">ⓘ</span> for a plain-English explanation.
                </li>
                <li>
                  <strong>"In short"</strong> is a one-line AI summary of the current situation.
                </li>
                <li>
                  <strong>What happened</strong> lists recent news events. The small blue numbers link to the article
                  each point comes from, so you can check it yourself.
                </li>
                <li>
                  <strong>Bull case / Bear case</strong>: the main arguments for the stock going up (bull) or down
                  (bear). Weigh both, not just the side you like.
                </li>
                <li>
                  <strong>Analyst ratings are opinions, not guarantees.</strong> Price targets are often wrong, and
                  analysts rarely say Sell.
                </li>
                <li>
                  This page summarises information. It doesn't tell you whether to buy, and it can contain mistakes.
                </li>
              </ul>
            </details>

            <div className="card stock-card">
              <div className="stock-header">
                <div className="stock-name">
                  <h2>{s.name}</h2>
                  <span>
                    {result.ticker}
                    {s.sector && " · " + s.sector}
                  </span>
                  {b.company_description && <p className="company-description">{b.company_description}</p>}
                </div>
                <div className="stock-stats">
                  <div className="stat">
                    <div className="stat-label">Price <Info text={EXPLAIN.price} /></div>
                    <div className="stat-value">{money(s.price)}</div>
                    {s.change != null && s.change_pct != null && (
                      <div className={"stat-sub " + toneClass(s.change)}>
                        {signed(s.change)} ({signed(s.change_pct, "%")})
                      </div>
                    )}
                  </div>
                  <div className="stat">
                    <div className="stat-label">Avg. target <Info text={EXPLAIN.target} /></div>
                    <div className="stat-value">{money(s.target_mean)}</div>
                    {s.upside_pct != null && (
                      <div className={"stat-sub " + toneClass(s.upside_pct)}>
                        {(s.upside_pct > 0 ? "+" : "") + s.upside_pct.toFixed(1)}% {s.upside_pct >= 0 ? "upside" : "downside"}
                      </div>
                    )}
                    {s.target_low != null && s.target_high != null && (
                      <div className="stat-sub">
                        {money(s.target_low)} – {money(s.target_high)}
                      </div>
                    )}
                    {targetContext(s) && <div className="stat-sub context">{targetContext(s)}</div>}
                  </div>
                  <div className="stat">
                    <div className="stat-label">Consensus <Info text={EXPLAIN.consensus} /></div>
                    <div className={consensusClass(s.recommendation)}>{prettyRating(s.recommendation)}</div>
                    {s.analyst_count != null && <div className="stat-sub">{s.analyst_count} analysts</div>}
                    {s.ratings && <RatingsBar r={s.ratings} />}
                  </div>
                </div>
              </div>

              <div className="metrics">
                <div>
                  <span>Market cap <Info text={EXPLAIN.marketCap} /></span>
                  {bigNumber(s.market_cap)}
                  {capContext(s.market_cap) && <div className="context">{capContext(s.market_cap)}</div>}
                </div>
                <div>
                  <span>52-week range <Info text={EXPLAIN.range52} /></span>
                  {money(s.week52_low)} – {money(s.week52_high)}
                  {(() => {
                    const r = rangeContext(s.price, s.week52_low, s.week52_high);
                    return r ? (
                      <>
                        <div className="range-bar" aria-hidden="true">
                          <span className="range-marker" style={{ left: `${r.pos * 100}%` }} />
                        </div>
                        <div className="context">{r.text}</div>
                      </>
                    ) : null;
                  })()}
                </div>
                <div>
                  <span>P/E (ttm / fwd) <Info text={EXPLAIN.pe} /></span>
                  {s.trailing_pe ?? "—"} / {s.forward_pe ?? "—"}
                  {peContext(s.trailing_pe, s.forward_pe, s.financials.profit_margin_pct, result.peers?.medians.forward_pe) && (
                    <div className="context">
                      {peContext(s.trailing_pe, s.forward_pe, s.financials.profit_margin_pct, result.peers?.medians.forward_pe)}
                    </div>
                  )}
                </div>
                <div>
                  <span>Next earnings <Info text={EXPLAIN.earnings} /></span>
                  {s.next_earnings ? shortDate(s.next_earnings) : "—"}
                </div>
              </div>
              <div className="as-of">
                {s.as_of && <>Price as of {new Date(s.as_of).toLocaleString()} · </>}
                Brief generated {timeAgo(result.generated_at)}
                {(() => {
                  const newest = result.articles.reduce<string | null>((m, a) => (a.published && (!m || a.published > m) ? a.published : m), null);
                  return newest ? <> · newest article {shortDate(newest)}</> : null;
                })()}
                {" · "}
                <button className="link-button" onClick={() => analyze(result.ticker, true)}>
                  Refresh
                </button>
              </div>
            </div>

            <PriceChart ticker={result.ticker} target={s.target_mean} />

            <div className="card brief-card">
              <div className="brief-label">⚡ AI Investment Brief</div>
              {b.tldr && (
                <div className="tldr">
                  <span className="tldr-label">In short</span>
                  {b.tldr}
                </div>
              )}

              <section className="brief-section">
                <h3>What happened</h3>
                {b.developments.length === 0 && <p className="muted">No significant recent events found.</p>}
                <ul className="developments">
                  {b.developments.map((d, i) => (
                    <li key={i}>
                      <div className="dev-headline">
                        {d.headline} <Cites ids={d.source_ids} />
                      </div>
                      <div className="dev-why">{d.why_it_matters}</div>
                    </li>
                  ))}
                </ul>
              </section>

              <div className="bull-bear">
                <section className="brief-section bull">
                  <h3>Bull case</h3>
                  <ul>
                    {b.bull_case.map((p, i) => (
                      <li key={i}>{p.text} <Cites ids={p.source_ids} /></li>
                    ))}
                  </ul>
                </section>
                <section className="brief-section bear">
                  <h3>Bear case</h3>
                  <ul>
                    {b.bear_case.map((p, i) => (
                      <li key={i}>{p.text} <Cites ids={p.source_ids} /></li>
                    ))}
                  </ul>
                </section>
              </div>

              {b.watch.length > 0 && (
                <section className="brief-section">
                  <h3>What to watch</h3>
                  <ul>
                    {b.watch.map((w, i) => <li key={i}>{w}</li>)}
                  </ul>
                </section>
              )}

              <section className="brief-section">
                <h3>News sentiment <Info text={EXPLAIN.sentiment} /></h3>
                <div className="sentiment-counts">
                  <span className="pill positive">{b.sentiment.positive} positive</span>
                  <span className="pill negative">{b.sentiment.negative} negative</span>
                  <span className="pill neutral">{b.sentiment.neutral} neutral</span>
                </div>
                <p>{b.sentiment.summary}</p>
              </section>
            </div>

            <div className="card health-card">
              <div className="brief-label">Financial health</div>
              <div className="health-grid">
                {healthItems(s.financials).map(item => (
                  <div key={item.label} className={"health-item " + item.verdict}>
                    <div className="health-label">
                      {item.label} <Info text={item.info} />
                    </div>
                    <div className="health-value">
                      {VERDICT_ICON[item.verdict] && <span className="verdict-icon">{VERDICT_ICON[item.verdict]}</span>}
                      {item.value}
                    </div>
                    <div className="health-note">{item.note}</div>
                  </div>
                ))}
              </div>

              {s.earnings_history.length > 0 && (
                <section className="earnings">
                  <h3>
                    Earnings vs. expectations <Info text={EXPLAIN.earningsHistory} />
                  </h3>
                  <p className="earnings-summary">
                    Beat analysts' estimates in{" "}
                    <strong>
                      {s.earnings_history.filter(q => q.eps_actual >= q.eps_estimate).length} of the last{" "}
                      {s.earnings_history.length}
                    </strong>{" "}
                    quarters.
                  </p>
                  <div className="earnings-row">
                    {s.earnings_history.map(q => {
                      const beat = q.eps_actual >= q.eps_estimate;
                      return (
                        <div key={q.quarter_end} className={"earnings-q " + (beat ? "beat" : "miss")}>
                          <div className="earnings-q-label">Quarter to {monthYear(q.quarter_end)}</div>
                          <div className="earnings-q-result">{beat ? "Beat" : "Missed"} {pct(q.surprise_pct)}</div>
                          <div className="earnings-q-detail">
                            ${q.eps_actual.toFixed(2)} vs. ${q.eps_estimate.toFixed(2)} expected
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </section>
              )}

              <div className="fin-links">
                <span>Full financial statements:</span>
                {s.links.income_statement && <a href={s.links.income_statement} target="_blank" rel="noreferrer">Income statement</a>}
                {s.links.balance_sheet && <a href={s.links.balance_sheet} target="_blank" rel="noreferrer">Balance sheet</a>}
                {s.links.cash_flow && <a href={s.links.cash_flow} target="_blank" rel="noreferrer">Cash flow</a>}
                {s.links.sec_filings && <a href={s.links.sec_filings} target="_blank" rel="noreferrer">Official SEC filings</a>}
                {s.links.company_website && <a href={s.links.company_website} target="_blank" rel="noreferrer">Company website</a>}
              </div>
            </div>

            <PeersCard result={result} />

            <div className="card sources-card">
              <div className="brief-label">
                Sources · {result.articles.length} articles from{" "}
                {new Set(result.articles.map(a => (/yahoo/i.test(a.source) ? "yahoo" : a.source))).size} publishers
              </div>
              {result.articles.length === 0 && <p className="muted">No recent articles found.</p>}
              <ol className="source-list">
                {result.articles.map(a => (
                  <li key={a.id}>
                    <span className="source-id">{a.id}</span>
                    <span className={"dot " + a.sentiment} title={a.sentiment} />
                    <a href={a.link} target="_blank" rel="noreferrer">{a.title}</a>
                    <span className="source-meta">
                      {a.source}
                      {a.published && " · " + shortDate(a.published)}
                    </span>
                  </li>
                ))}
              </ol>
            </div>

            <p className="disclaimer">
              AI-generated summary of public news and analyst data. May contain errors. Not financial advice.
              "Background" marks points from the model's general knowledge rather than the linked articles.
            </p>
          </div>
        )}
      </main>
    </div>
  );
}
