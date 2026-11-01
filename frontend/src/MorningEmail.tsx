import { useEffect, useState } from "react";
import { API, Info } from "./shared";

interface DigestSettings {
  email: string;
  send_time: string; // "HH:MM", local time in `timezone`
  timezone: string;
  enabled: boolean;
  last_sent_at: string | null;
  last_status: string | null;
  email_configured: boolean; // the backend has a Resend API key
}

interface Preview {
  subject: string;
  html: string;
  headline_count: number;
}

const HELP =
  "Every morning at the time you choose, you get an email with each holding's price move and its new headlines (with links), plus a short AI summary of what matters for this portfolio. It's sent by the backend, so it needs to be running at that time; if it starts later, the email still goes out within 6 hours.";

const errorMessage = async (res: Response) => {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return "Check the email address and the time.";
  } catch {}
  return "Something went wrong. Try again in a moment.";
};

const localTimezone = () => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Helsinki";
  } catch {
    return "Europe/Helsinki";
  }
};

export default function MorningEmail({ portfolioId, portfolioName }: { portfolioId: number; portfolioName: string }) {
  const [settings, setSettings] = useState<DigestSettings | null>(null);
  const [email, setEmail] = useState("");
  const [sendTime, setSendTime] = useState("07:00");
  const [enabled, setEnabled] = useState(true);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState<"" | "save" | "preview" | "send">("");
  const [preview, setPreview] = useState<Preview | null>(null);

  const load = async () => {
    try {
      const res = await fetch(`${API}/portfolios/${portfolioId}/digest`);
      if (!res.ok) throw new Error(await errorMessage(res));
      const s: DigestSettings = await res.json();
      setSettings(s);
      setEmail(s.email);
      setSendTime(s.send_time);
      setEnabled(s.email ? s.enabled : true);
    } catch (e) {
      setMessage({ kind: "error", text: e instanceof Error ? e.message : "Couldn't load the email settings." });
    }
  };

  useEffect(() => {
    setPreview(null);
    setMessage(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [portfolioId]);

  const save = async (e?: React.FormEvent) => {
    e?.preventDefault();
    setBusy("save");
    setMessage(null);
    try {
      const res = await fetch(`${API}/portfolios/${portfolioId}/digest`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), send_time: sendTime, timezone: localTimezone(), enabled }),
      });
      if (!res.ok) throw new Error(await errorMessage(res));
      setSettings(await res.json());
      setMessage({
        kind: "ok",
        text: enabled ? `Saved. "${portfolioName}" will be emailed to ${email.trim()} every morning at ${sendTime}.` : "Saved. The morning email is turned off.",
      });
      return true;
    } catch (err) {
      setMessage({ kind: "error", text: err instanceof Error ? err.message : "Couldn't save." });
      return false;
    } finally {
      setBusy("");
    }
  };

  const showPreview = async () => {
    setBusy("preview");
    setMessage(null);
    try {
      const res = await fetch(`${API}/portfolios/${portfolioId}/digest/preview`);
      if (!res.ok) throw new Error(await errorMessage(res));
      setPreview(await res.json());
    } catch (err) {
      setMessage({ kind: "error", text: err instanceof Error ? err.message : "Couldn't build the preview." });
    } finally {
      setBusy("");
    }
  };

  const sendNow = async () => {
    // Save first so "Send now" uses the address that's in the form
    if (!settings || settings.email !== email.trim() || settings.send_time !== sendTime || settings.enabled !== enabled) {
      if (!(await save())) return;
    }
    setBusy("send");
    setMessage(null);
    try {
      const res = await fetch(`${API}/portfolios/${portfolioId}/digest/send`, { method: "POST" });
      if (!res.ok) throw new Error(await errorMessage(res));
      const body = await res.json();
      setMessage({ kind: "ok", text: body.status });
      await load();
    } catch (err) {
      setMessage({ kind: "error", text: err instanceof Error ? err.message : "Couldn't send." });
    } finally {
      setBusy("");
    }
  };

  if (!settings) return null;

  return (
    <div className="card morning-email">
      <div className="brief-label">
        Morning email <Info text={HELP} />
      </div>

      {!settings.email_configured && (
        <div className="notice">
          <strong>Sending isn't set up yet.</strong> Add your Resend API key to <code>backend/.env</code> as{" "}
          <code>RESEND_API_KEY=…</code> and restart the backend. You can already save the settings and preview the email.
        </div>
      )}

      <form className="email-fields" onSubmit={save}>
        <label>
          Send to
          <input type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="you@example.com" maxLength={320} required />
        </label>
        <label>
          Every day at
          <input type="time" value={sendTime} onChange={e => setSendTime(e.target.value)} required />
        </label>
        <label className="toggle">
          <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
          On
        </label>
        <button type="submit" disabled={busy !== ""}>
          {busy === "save" ? "Saving…" : "Save"}
        </button>
      </form>

      <div className="email-actions">
        <button className="link-button" onClick={showPreview} disabled={busy !== ""}>
          {busy === "preview" ? "Building preview… (about 20 s)" : "Preview today's email"}
        </button>
        <button className="link-button" onClick={sendNow} disabled={busy !== "" || !settings.email_configured || !email.trim()}>
          {busy === "send" ? "Sending…" : "Send now"}
        </button>
      </div>

      {message && <p className={message.kind === "ok" ? "added-note" : "error"}>{message.text}</p>}
      {settings.last_status && !message && (
        <p className="peer-source">
          Last email: {settings.last_status}
          {settings.last_sent_at && ` (${new Date(settings.last_sent_at).toLocaleString()})`}
        </p>
      )}

      {preview && (
        <div className="email-preview">
          <div className="email-preview-bar">
            <span>
              <strong>Subject:</strong> {preview.subject}
            </span>
            <button className="link-button" onClick={() => setPreview(null)}>
              Close
            </button>
          </div>
          {/* sandbox: the email's HTML can't run scripts or reach this page */}
          <iframe title="Email preview" srcDoc={preview.html} sandbox="allow-popups allow-popups-to-escape-sandbox" />
        </div>
      )}
    </div>
  );
}
