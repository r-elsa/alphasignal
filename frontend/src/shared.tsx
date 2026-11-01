// Helpers shared by the analysis page and the portfolio page
export const API = "http://localhost:8000";

// Prices use the stock's trading currency (London quotes in pence, "GBp"); financials can use another currency
export const currencySymbol = (currency: string) => {
  try {
    const part = new Intl.NumberFormat("en-US", { style: "currency", currency }).formatToParts(0).find(p => p.type === "currency");
    const sym = part?.value ?? currency;
    return /^[A-Z]{2,3}$/.test(sym) ? sym + " " : sym; // "SEK 12" reads better than "SEK12"
  } catch {
    return currency + " ";
  }
};

export const money = (n: number | null, currency = "USD") => {
  if (n == null) return "—";
  if (currency === "GBp") return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + "p";
  const digits = ["JPY", "KRW"].includes(currency) ? 0 : 2;
  return currencySymbol(currency) + n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
};

export const signed = (n: number, suffix = "") => (n > 0 ? "+" : "") + n.toFixed(2) + suffix;

export const bigNumber = (n: number | null, currency = "USD") => {
  if (n == null) return "—";
  const sym = currencySymbol(currency === "GBp" ? "GBP" : currency);
  if (n >= 1e12) return sym + (n / 1e12).toFixed(2) + "T";
  if (n >= 1e9) return sym + (n / 1e9).toFixed(1) + "B";
  if (n >= 1e6) return sym + (n / 1e6).toFixed(1) + "M";
  return sym + Math.round(n).toLocaleString("en-US");
};

export const pct = (n: number) => (n > 0 ? "+" : "") + n.toFixed(1) + "%";

export const toneClass = (n: number | null) => (n == null ? "" : n > 0 ? "green" : n < 0 ? "red" : "");

export const Info = ({ text }: { text: string }) => (
  <span className="info" tabIndex={0} aria-label={text}>
    ⓘ<span className="info-tip" role="tooltip">{text}</span>
  </span>
);
