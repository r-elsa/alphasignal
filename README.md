# AlphaSignal

Enter a company or ticker and get everything that matters in one page: live price, analyst consensus, financial health, a comparison with competitors, recent news from many publishers, and an AI-written brief where every claim links to its source. Written to be readable by beginner investors.

## Features

- **Search by company name or ticker** with autocomplete
- **Price chart** (1M / 6M / 1Y / 5Y) with the average analyst target
- **Key numbers with context**, e.g. "near its 1-year high", "cheaper than its competitors", plus plain-English ⓘ explanations
- **Financial health**: sales growth, profit margin, cash vs. debt, free cash flow, dividend, volatility
- **Earnings vs. expectations** for the last four quarters
- **Competitor comparison** (Finnhub peers, AI fallback)
- **AI brief**: what happened, bull and bear case, what to watch, and news sentiment, each point cited
- **News** from Google News, Seeking Alpha, Nasdaq and Yahoo Finance, filtered for relevance and capped per publisher
- Live progress while the agents work, 15-minute caching, and shareable links (`?t=NVDA`)

## How it works

A FastAPI backend runs a small multi-agent pipeline:

1. **Market agent** fetches price, analyst data, financials and earnings history (yfinance)
2. **News agent** and **Peers agent** run in parallel: RSS feeds, and competitors via Finnhub
3. **Report agent** writes a structured brief with an OpenAI model and cites articles by ID

Progress is streamed to the React frontend with Server-Sent Events.

## Running locally

**Backend** (Python 3.9+)

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then add your API keys
uvicorn main:app --reload
```

**Frontend**

```bash
cd frontend
npm install
npm start
```

Open http://localhost:3000.

## Disclaimer

AlphaSignal summarises public news and market data with AI. It can contain errors and is not financial advice. Market data comes from yfinance, an unofficial Yahoo Finance library intended for personal and educational use.
