import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { stocksApi, type StockQuote, type IndexQuote } from '../api/stocks';

const HOT_STOCKS = [
  { code: '600519', name: '贵州茅台' },
  { code: '300750', name: '宁德时代' },
  { code: '002594', name: '比亚迪' },
  { code: '601318', name: '中国平安' },
  { code: '000858', name: '五粮液' },
  { code: '600036', name: '招商银行' },
];

const WATCHLIST_KEY = 'dsa_watchlist';

function loadWatchlist(): { code: string; name?: string }[] {
  try {
    const raw = localStorage.getItem(WATCHLIST_KEY);
    if (raw) return JSON.parse(raw);
  } catch { /* ignore */ }
  return HOT_STOCKS;
}

function fmtNum(v: number | null | undefined, decimals = 2): string {
  if (v == null) return '--';
  return v.toFixed(decimals);
}

function fmtAmt(a: number | null | undefined): string {
  if (a == null) return '--';
  if (a >= 1e12) return (a / 1e12).toFixed(2) + '万亿';
  if (a >= 1e8) return (a / 1e8).toFixed(2) + '亿';
  if (a >= 1e4) return (a / 1e4).toFixed(2) + '万';
  return a.toLocaleString();
}

const HomePage: React.FC = () => {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [indexQuotes, setIndexQuotes] = useState<IndexQuote[]>([]);
  const [watchlistQuotes, setWatchlistQuotes] = useState<(StockQuote & { code: string })[]>([]);

  const handleNavigate = useCallback((code: string, name?: string) => {
    navigate(`/chat?stock=${encodeURIComponent(code)}&name=${encodeURIComponent(name || '')}`);
  }, [navigate]);

  const handleGoMarket = useCallback((code: string) => {
    navigate(`/market?code=${encodeURIComponent(code)}`);
  }, [navigate]);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const data = await stocksApi.getIndexQuotes();
        if (active) setIndexQuotes(data);
      } catch { /* ignore */ }
    };
    load();
    const t = setInterval(load, 60000);
    return () => { active = false; clearInterval(t); };
  }, []);

  useEffect(() => {
    let active = true;
    const wl = loadWatchlist();
    const load = async () => {
      const results: (StockQuote & { code: string })[] = [];
      await Promise.allSettled(wl.map(async (item) => {
        try {
          const q = await stocksApi.getQuote(item.code);
          results.push({ ...q, code: item.code });
        } catch { /* skip */ }
      }));
      if (active) setWatchlistQuotes(results);
    };
    load();
    const t = setInterval(load, 30000);
    return () => { active = false; clearInterval(t); };
  }, []);

  const sortedByChange = [...watchlistQuotes].sort((a, b) =>
    (b.changePercent ?? -999) - (a.changePercent ?? -999)
  );
  const gainers = sortedByChange.filter(q => (q.changePercent ?? 0) > 0);
  const losers = sortedByChange.filter(q => (q.changePercent ?? 0) < 0).reverse();

  const shIndex = indexQuotes.find(i => i.code === '000001');
  const szIndex = indexQuotes.find(i => i.code === '399001');
  const totalAmount = (shIndex?.amount ?? 0) + (szIndex?.amount ?? 0);

  return (
    <div className="h-full overflow-y-auto bg-background">
      <div className="max-w-5xl mx-auto px-4 py-6 flex flex-col gap-6">

        <div className="flex flex-col items-center gap-3">
          <div className="flex flex-col items-center gap-1">
            <h1 className="text-3xl font-bold tracking-wider text-[var(--color-cyan)] font-mono">牛气</h1>
            <p className="text-xs text-muted-text font-mono tracking-wide">牛气冲天量化决策终端</p>
          </div>
          <div className="w-full max-w-lg">
            <StockAutocomplete
              value={query}
              onChange={setQuery}
              onSubmit={(code, name) => handleNavigate(code, name)}
              placeholder="输入股票代码或名称，回车开始问股..."
              className="font-mono text-sm rounded-md"
            />
          </div>
          <div className="flex flex-wrap justify-center gap-1.5">
            {HOT_STOCKS.map((stock) => (
              <button
                key={stock.code}
                type="button"
                onClick={() => handleNavigate(stock.code, stock.name)}
                className="h-7 px-2.5 text-[11px] font-mono rounded-sm border border-[var(--border-dim)] text-secondary-text hover:text-[var(--color-cyan)] hover:border-[var(--color-cyan)]/30 hover:bg-[var(--color-cyan)]/5 transition-colors"
              >
                {stock.name}
              </button>
            ))}
          </div>
        </div>

        <div>
          <h2 className="text-[11px] font-medium text-muted-text font-mono mb-2 px-1">大盘指数</h2>
          <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
            {indexQuotes.map((idx) => {
              const pct = idx.changePercent;
              const isUp = pct != null && pct > 0;
              const isDown = pct != null && pct < 0;
              const cls = isUp ? 'text-red-400' : isDown ? 'text-emerald-400' : 'text-secondary-text';
              const bgCls = isUp ? 'border-red-400/20' : isDown ? 'border-emerald-400/20' : 'border-border';
              return (
                <div
                  key={idx.code}
                  className={`rounded-sm border ${bgCls} bg-foreground/3 px-2.5 py-2 cursor-pointer hover:bg-foreground/6 transition-colors`}
                  onClick={() => handleGoMarket(idx.code)}
                >
                  <p className="text-[10px] text-muted-text font-mono truncate">{idx.name}</p>
                  <p className={`text-sm font-bold font-mono ${cls} mt-0.5`}>
                    {idx.price > 0 ? idx.price.toFixed(2) : '--'}
                  </p>
                  <div className="flex items-center gap-1 mt-0.5">
                    {pct != null && (
                      <span className={`text-[10px] font-mono ${cls}`}>
                        {pct > 0 ? '+' : ''}{pct.toFixed(2)}%
                      </span>
                    )}
                    <div className="flex-1 h-1 bg-foreground/5 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full ${isUp ? 'bg-red-400/60' : isDown ? 'bg-emerald-400/60' : 'bg-foreground/20'}`}
                        style={{ width: pct != null ? `${Math.min(Math.abs(pct) * 10, 100)}%` : '0%' }}
                      />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {watchlistQuotes.length > 0 && (
          <div>
            <h2 className="text-[11px] font-medium text-muted-text font-mono mb-2 px-1">自选股排行</h2>
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-sm border border-border bg-foreground/3">
                <div className="px-2.5 py-1 border-b border-border">
                  <span className="text-[10px] font-mono text-red-400">涨幅榜</span>
                </div>
                <div className="divide-y divide-border/50">
                  {gainers.slice(0, 5).map((q) => (
                    <div
                      key={q.stockCode}
                      className="flex items-center justify-between px-2.5 py-1.5 cursor-pointer hover:bg-foreground/4 transition-colors"
                      onClick={() => handleGoMarket(q.code)}
                    >
                      <div className="min-w-0">
                        <p className="text-[11px] text-foreground truncate">{q.stockName || q.code}</p>
                        <p className="text-[9px] text-muted-text font-mono">{q.stockCode}</p>
                      </div>
                      <div className="text-right shrink-0 ml-2">
                        <p className="text-[11px] font-mono text-foreground">{fmtNum(q.currentPrice)}</p>
                        <p className="text-[9px] font-mono text-red-400">
                          +{(q.changePercent ?? 0).toFixed(2)}%
                        </p>
                      </div>
                    </div>
                  ))}
                  {gainers.length === 0 && (
                    <div className="px-2.5 py-3 text-center text-[10px] text-muted-text font-mono">暂无上涨</div>
                  )}
                </div>
              </div>
              <div className="rounded-sm border border-border bg-foreground/3">
                <div className="px-2.5 py-1 border-b border-border">
                  <span className="text-[10px] font-mono text-emerald-400">跌幅榜</span>
                </div>
                <div className="divide-y divide-border/50">
                  {losers.slice(0, 5).map((q) => (
                    <div
                      key={q.stockCode}
                      className="flex items-center justify-between px-2.5 py-1.5 cursor-pointer hover:bg-foreground/4 transition-colors"
                      onClick={() => handleGoMarket(q.code)}
                    >
                      <div className="min-w-0">
                        <p className="text-[11px] text-foreground truncate">{q.stockName || q.code}</p>
                        <p className="text-[9px] text-muted-text font-mono">{q.stockCode}</p>
                      </div>
                      <div className="text-right shrink-0 ml-2">
                        <p className="text-[11px] font-mono text-foreground">{fmtNum(q.currentPrice)}</p>
                        <p className="text-[9px] font-mono text-emerald-400">
                          {(q.changePercent ?? 0).toFixed(2)}%
                        </p>
                      </div>
                    </div>
                  ))}
                  {losers.length === 0 && (
                    <div className="px-2.5 py-3 text-center text-[10px] text-muted-text font-mono">暂无下跌</div>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}

        <div>
          <h2 className="text-[11px] font-medium text-muted-text font-mono mb-2 px-1">市场情绪</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            <div className="rounded-sm border border-border bg-foreground/3 px-2.5 py-2">
              <p className="text-[10px] text-muted-text font-mono">沪市涨跌</p>
              <div className="flex items-center gap-2 mt-1">
                <span className="text-sm font-mono text-red-400">{shIndex?.upCount ?? '--'}</span>
                <span className="text-[10px] text-muted-text">/</span>
                <span className="text-sm font-mono text-emerald-400">{shIndex?.downCount ?? '--'}</span>
              </div>
            </div>
            <div className="rounded-sm border border-border bg-foreground/3 px-2.5 py-2">
              <p className="text-[10px] text-muted-text font-mono">深市涨跌</p>
              <div className="flex items-center gap-2 mt-1">
                <span className="text-sm font-mono text-red-400">{szIndex?.upCount ?? '--'}</span>
                <span className="text-[10px] text-muted-text">/</span>
                <span className="text-sm font-mono text-emerald-400">{szIndex?.downCount ?? '--'}</span>
              </div>
            </div>
            <div className="rounded-sm border border-border bg-foreground/3 px-2.5 py-2">
              <p className="text-[10px] text-muted-text font-mono">沪市成交</p>
              <p className="text-sm font-mono text-foreground mt-1">{fmtAmt(shIndex?.amount ?? null)}</p>
            </div>
            <div className="rounded-sm border border-border bg-foreground/3 px-2.5 py-2">
              <p className="text-[10px] text-muted-text font-mono">两市合计</p>
              <p className="text-sm font-mono text-foreground mt-1">{fmtAmt(totalAmount || null)}</p>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
};

export default HomePage;
