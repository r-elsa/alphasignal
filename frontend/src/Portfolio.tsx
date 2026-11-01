import { useEffect, useState } from "react";
import MorningEmail from "./MorningEmail";
import { API, Info, money, pct, toneClass } from "./shared";

interface Holding {
  id: number;
  ticker: string;
  name: string;
  shares: number;
  avg_price: number;
  currency: string; // the stock's trading currency; avg_price and price are in it
  price: number | null;
  day_change_pct: number | null;
  value: number | null; // portfolio currency from here on
  cost: number | null;
  gain: number | null;
  gain_pct: number | null;
  day_change: number | null;
  weight_pct: number | null;
}

interface PortfolioSummary {
  id: number;
  name: string;
  holdings_count: number;
}

interface PortfolioData {
  id: number;
  name: string;
  currency: string;
  holdings: Holding[];
  totals: {
    value: number;
    cost: number;
    gain: number;
    gain_pct: number | null;
    day_change: number;
    day_change_pct: number | null;
    missing_prices: number;
  };
}

const EXPLAIN = {
  value: "What your holdings are worth at the latest prices, converted to euros at today's exchange rates.",
  gain: "Current value minus what you paid. Both are converted at today's exchange rates, so currency moves since you bought are not included.",
  today: "How much your portfolio moved since the previous trading day's close.",
  avgPrice: "The average price you paid per share, in the stock's own currency (e.g. USD for US stocks, EUR for Helsinki, pence for London).",
  weight: "How much of your portfolio this holding makes up. A single stock above about 20–25% means your portfolio depends heavily on it.",
};

const errorMessage = async (res: Response) => {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return "Check the numbers: shares must be above 0 and the price can't be negative.";
  } catch {}
  return "Something went wrong. Try again in a moment.";
};

const shares = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 4 });

const urlPortfolioId = () => {
  const p = Number(new URLSearchParams(window.location.search).get("p"));
  return Number.isInteger(p) && p > 0 ? p : null;
};

export default function Portfolio({ onAnalyse }: { onAnalyse: (ticker: string) => void }) {
  const [portfolios, setPortfolios] = useState<PortfolioSummary[] | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [data, setData] = useState<PortfolioData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Creating and renaming portfolios
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [nameError, setNameError] = useState("");

  const [ticker, setTicker] = useState("");
  const [newShares, setNewShares] = useState("");
  const [newPrice, setNewPrice] = useState("");
  const [formError, setFormError] = useState("");
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState("");

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editShares, setEditShares] = useState("");
  const [editPrice, setEditPrice] = useState("");

  const reportError = (e: unknown) =>
    setError(e instanceof Error && e.message !== "Failed to fetch" ? e.message : "Can't reach the server. Is the backend running?");

  // List of portfolios (names and how many holdings each has)
  const loadList = async () => {
    try {
      const res = await fetch(`${API}/portfolios`);
      if (!res.ok) throw new Error(await errorMessage(res));
      const list: PortfolioSummary[] = await res.json();
      setPortfolios(list);
      setError("");
      return list;
    } catch (e) {
      reportError(e);
      setLoading(false);
      return null;
    }
  };

  // The selected portfolio with live prices
  const load = async (id: number | null = selectedId) => {
    if (id == null) {
      setData(null);
      setLoading(false);
      return;
    }
    try {
      const res = await fetch(`${API}/portfolios/${id}`);
      if (!res.ok) throw new Error(await errorMessage(res));
      setData(await res.json());
      setError("");
    } catch (e) {
      reportError(e);
    } finally {
      setLoading(false);
    }
  };

  const select = (id: number | null) => {
    setSelectedId(id);
    setEditingId(null);
    setRenaming(false);
    setFormError("");
    setAdded("");
    window.history.replaceState(null, "", id == null ? "?view=portfolio" : `?view=portfolio&p=${id}`);
  };

  useEffect(() => {
    loadList().then(list => {
      if (!list) return;
      const fromUrl = urlPortfolioId();
      select(list.find(p => p.id === fromUrl)?.id ?? list[0]?.id ?? null);
      if (list.length === 0) setLoading(false);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (selectedId != null) {
      setLoading(true);
      load(selectedId);
    } else {
      setData(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  const createPortfolio = async (e: React.FormEvent) => {
    e.preventDefault();
    setNameError("");
    if (!newName.trim()) {
      setNameError("Give the portfolio a name.");
      return;
    }
    try {
      const res = await fetch(`${API}/portfolios`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName }),
      });
      if (!res.ok) throw new Error(await errorMessage(res));
      const created: PortfolioSummary = await res.json();
      setNewName("");
      setCreating(false);
      await loadList();
      select(created.id);
    } catch (err) {
      setNameError(err instanceof Error ? err.message : "Something went wrong.");
    }
  };

  const renamePortfolio = async (e: React.FormEvent) => {
    e.preventDefault();
    if (selectedId == null) return;
    setNameError("");
    try {
      const res = await fetch(`${API}/portfolios/${selectedId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: renameValue }),
      });
      if (!res.ok) throw new Error(await errorMessage(res));
      setRenaming(false);
      await Promise.all([loadList(), load()]);
    } catch (err) {
      setNameError(err instanceof Error ? err.message : "Something went wrong.");
    }
  };

  const deletePortfolio = async () => {
    if (!data) return;
    const n = data.holdings.length;
    const what = n ? ` and its ${n} holding${n === 1 ? "" : "s"}` : "";
    if (!window.confirm(`Delete the portfolio "${data.name}"${what}? This can't be undone.`)) return;
    const res = await fetch(`${API}/portfolios/${data.id}`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) {
      setError(await errorMessage(res));
      return;
    }
    const list = await loadList();
    select(list?.[0]?.id ?? null);
  };

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError("");
    setAdded("");
    const s = parseFloat(newShares.replace(",", "."));
    const p = parseFloat(newPrice.replace(",", "."));
    if (!ticker.trim() || !(s > 0) || !(p >= 0)) {
      setFormError("Fill in the company, a number of shares above 0, and the price you paid.");
      return;
    }
    setAdding(true);
    try {
      const res = await fetch(`${API}/portfolios/${selectedId}/holdings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker: ticker.trim(), shares: s, avg_price: p }),
      });
      if (!res.ok) throw new Error(await errorMessage(res));
      const holding = await res.json();
      setAdded(`Added ${holding.name} (${holding.ticker}), priced in ${holding.currency}.`);
      setTicker("");
      setNewShares("");
      setNewPrice("");
      await Promise.all([load(), loadList()]);
    } catch (e) {
      setFormError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setAdding(false);
    }
  };

  const startEdit = (h: Holding) => {
    setEditingId(h.id);
    setEditShares(String(h.shares));
    setEditPrice(String(h.avg_price));
  };

  const saveEdit = async (h: Holding) => {
    const s = parseFloat(editShares.replace(",", "."));
    const p = parseFloat(editPrice.replace(",", "."));
    if (!(s > 0) || !(p >= 0)) {
      setError("Shares must be above 0 and the price can't be negative.");
      return;
    }
    const res = await fetch(`${API}/portfolios/${selectedId}/holdings/${h.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ shares: s, avg_price: p }),
    });
    if (!res.ok) {
      setError(await errorMessage(res));
      return;
    }
    setEditingId(null);
    await load();
  };

  const remove = async (h: Holding) => {
    if (!window.confirm(`Remove ${h.name} (${h.ticker}) from your portfolio?`)) return;
    const res = await fetch(`${API}/portfolios/${selectedId}/holdings/${h.id}`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) {
      setError(await errorMessage(res));
      return;
    }
    await Promise.all([load(), loadList()]);
  };

  const cur = data?.currency ?? "EUR";
  const t = data?.totals;

  return (
    <div className="portfolio">
      <div className="hero">
        <h1>My portfolios</h1>
        <p>Your holdings at today's prices. Keep several portfolios side by side, and click a stock for its full brief.</p>
      </div>

      {error && <div className="error">{error}</div>}

      {portfolios && portfolios.length > 0 && (
        <div className="portfolio-bar">
          <div className="portfolio-tabs" role="tablist" aria-label="Your portfolios">
            {portfolios.map(p => (
              <button
                key={p.id}
                role="tab"
                aria-selected={p.id === selectedId}
                className={p.id === selectedId ? "active" : ""}
                onClick={() => select(p.id)}
              >
                {p.name}
                <span className="tab-count">{p.holdings_count}</span>
              </button>
            ))}
            {!creating && (
              <button className="new-portfolio" onClick={() => { setCreating(true); setNameError(""); }}>
                + New portfolio
              </button>
            )}
          </div>
          {creating && (
            <form className="name-form" onSubmit={createPortfolio}>
              <input autoFocus value={newName} onChange={e => setNewName(e.target.value)} placeholder="Portfolio name, e.g. Long term" maxLength={100} aria-label="New portfolio name" />
              <button type="submit">Create</button>
              <button type="button" className="link-button muted-inline" onClick={() => { setCreating(false); setNameError(""); }}>Cancel</button>
            </form>
          )}
          {nameError && <div className="error">{nameError}</div>}
        </div>
      )}

      {portfolios && portfolios.length === 0 && (
        <form className="card empty-portfolio" onSubmit={createPortfolio}>
          <p>
            <strong>Create your first portfolio.</strong> You can have several, for example "Long term" and "Trading",
            or one per broker account.
          </p>
          <div className="name-form">
            <input autoFocus value={newName} onChange={e => setNewName(e.target.value)} placeholder="Portfolio name, e.g. My portfolio" maxLength={100} aria-label="Portfolio name" />
            <button type="submit">Create</button>
          </div>
          {nameError && <div className="error">{nameError}</div>}
        </form>
      )}

      {data && (
        <div className="portfolio-heading">
          {renaming ? (
            <form className="name-form" onSubmit={renamePortfolio}>
              <input autoFocus onFocus={e => e.target.select()} value={renameValue} onChange={e => setRenameValue(e.target.value)} maxLength={100} aria-label="Portfolio name" />
              <button type="submit">Save</button>
              <button type="button" className="link-button muted-inline" onClick={() => { setRenaming(false); setNameError(""); }}>Cancel</button>
            </form>
          ) : (
            <>
              <h2>{data.name}</h2>
              <button className="link-button" onClick={() => { setRenaming(true); setRenameValue(data.name); setNameError(""); }}>Rename</button>
              <button className="link-button danger" onClick={deletePortfolio}>Delete portfolio</button>
            </>
          )}
        </div>
      )}

      {loading && <p className="muted">Loading your portfolio…</p>}

      {data && t && data.holdings.length > 0 && (
        <div className="portfolio-summary">
          <div className="card summary-card">
            <div className="stat-label">
              Value <Info text={EXPLAIN.value} />
            </div>
            <div className="summary-value">{money(t.value, cur)}</div>
            <div className="stat-sub">{data.holdings.length} holdings</div>
          </div>
          <div className="card summary-card">
            <div className="stat-label">
              Total gain <Info text={EXPLAIN.gain} />
            </div>
            <div className={"summary-value " + toneClass(t.gain)}>
              {(t.gain > 0 ? "+" : t.gain < 0 ? "−" : "") + money(Math.abs(t.gain), cur)}
            </div>
            <div className={"stat-sub " + toneClass(t.gain)}>{t.gain_pct != null ? pct(t.gain_pct) : "—"} on {money(t.cost, cur)} invested</div>
          </div>
          <div className="card summary-card">
            <div className="stat-label">
              Today <Info text={EXPLAIN.today} />
            </div>
            <div className={"summary-value " + toneClass(t.day_change)}>
              {(t.day_change > 0 ? "+" : t.day_change < 0 ? "−" : "") + money(Math.abs(t.day_change), cur)}
            </div>
            <div className={"stat-sub " + toneClass(t.day_change)}>{t.day_change_pct != null ? pct(t.day_change_pct) : "—"}</div>
          </div>
        </div>
      )}

      {data && data.holdings.length > 0 && (
        <div className="card holdings-card">
          <div className="table-scroll">
            <table className="holdings-table">
              <thead>
                <tr>
                  <th>Company</th>
                  <th>Shares</th>
                  <th>
                    Avg. price <Info text={EXPLAIN.avgPrice} />
                  </th>
                  <th>Price</th>
                  <th>Value ({cur})</th>
                  <th>Gain</th>
                  <th>
                    Weight <Info text={EXPLAIN.weight} />
                  </th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {data.holdings.map(h => {
                  const editing = editingId === h.id;
                  return (
                    <tr key={h.id}>
                      <td>
                        <button className="link-button holding-ticker" onClick={() => onAnalyse(h.ticker)} title="Open the full brief">
                          {h.ticker}
                        </button>
                        <span className="peer-name">{h.name}</span>
                      </td>
                      <td>
                        {editing ? (
                          <input className="cell-input" value={editShares} onChange={e => setEditShares(e.target.value)} inputMode="decimal" aria-label="Shares" />
                        ) : (
                          shares(h.shares)
                        )}
                      </td>
                      <td>
                        {editing ? (
                          <input className="cell-input" value={editPrice} onChange={e => setEditPrice(e.target.value)} inputMode="decimal" aria-label="Average price" />
                        ) : (
                          money(h.avg_price, h.currency)
                        )}
                      </td>
                      <td>
                        {money(h.price, h.currency)}
                        {h.day_change_pct != null && <span className={"cell-sub " + toneClass(h.day_change_pct)}>{pct(h.day_change_pct)} today</span>}
                      </td>
                      <td>{money(h.value, cur)}</td>
                      <td className={toneClass(h.gain)}>
                        {h.gain == null ? "—" : (h.gain > 0 ? "+" : h.gain < 0 ? "−" : "") + money(Math.abs(h.gain), cur)}
                        {h.gain_pct != null && <span className={"cell-sub " + toneClass(h.gain_pct)}>{pct(h.gain_pct)}</span>}
                      </td>
                      <td>
                        {h.weight_pct == null ? "—" : h.weight_pct.toFixed(1) + "%"}
                        {h.weight_pct != null && (
                          <span className="weight-bar" aria-hidden="true">
                            <span style={{ width: `${Math.min(100, h.weight_pct)}%` }} />
                          </span>
                        )}
                      </td>
                      <td className="row-actions">
                        {editing ? (
                          <>
                            <button className="link-button" onClick={() => saveEdit(h)}>Save</button>
                            <button className="link-button muted-inline" onClick={() => setEditingId(null)}>Cancel</button>
                          </>
                        ) : (
                          <>
                            <button className="link-button" onClick={() => startEdit(h)}>Edit</button>
                            <button className="link-button danger" onClick={() => remove(h)}>Remove</button>
                          </>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="peer-source">
            Totals in {cur} at today's exchange rates. Gain % per stock is in its own currency.
            {t && t.missing_prices > 0 && ` ${t.missing_prices} holding(s) have no price right now and are left out of the totals.`}
          </p>
        </div>
      )}

      {data && data.holdings.length === 0 && (
        <div className="card empty-portfolio">
          <p>
            <strong>This portfolio is empty.</strong> Add the stocks you own below to see their value, gains and how your
            money is spread.
          </p>
        </div>
      )}

      {data && (
      <form className="card add-holding" onSubmit={add}>
        <div className="brief-label">Add a holding to {data.name}</div>
        <div className="add-fields">
          <label>
            Company or ticker
            <input value={ticker} onChange={e => setTicker(e.target.value)} placeholder="e.g. Nvidia, AAPL, FORTUM.HE" />
          </label>
          <label>
            Shares
            <input value={newShares} onChange={e => setNewShares(e.target.value)} inputMode="decimal" placeholder="e.g. 10" />
          </label>
          <label>
            <span>
              Avg. purchase price <Info text={EXPLAIN.avgPrice} />
            </span>
            <input value={newPrice} onChange={e => setNewPrice(e.target.value)} inputMode="decimal" placeholder="per share" />
          </label>
          <button type="submit" disabled={adding}>
            {adding ? "Adding…" : "Add"}
          </button>
        </div>
        <p className="peer-source">Enter the price in the stock's own currency: USD for US stocks, EUR for Helsinki, pence for London.</p>
        {formError && <div className="error">{formError}</div>}
        {added && <p className="added-note">{added}</p>}
      </form>
      )}

      {data && data.holdings.length > 0 && <MorningEmail portfolioId={data.id} portfolioName={data.name} />}

      <p className="disclaimer">Saved in a database on this computer. Prices may be delayed. Not financial advice.</p>
    </div>
  );
}
