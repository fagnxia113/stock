import type React from 'react';
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { init, dispose, type Chart } from 'klinecharts';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { stocksApi, formulaApi, type StockQuote, type OrderBookResponse, type TradeTicksResponse, type IndexQuote, type FinanceInfo, type XdxrResponse, type RpsData, type DivergenceData, type ResonanceData, type BacktestSummaryData } from '../api/stocks';

type WatchlistItem = {
  code: string;
  name?: string;
};

const STORAGE_KEY = 'dsa_watchlist';

const DEFAULT_WATCHLIST: WatchlistItem[] = [
  { code: '600519', name: '贵州茅台' },
  { code: '300750', name: '宁德时代' },
  { code: '002594', name: '比亚迪' },
  { code: '601318', name: '中国平安' },
  { code: '000858', name: '五粮液' },
];

function loadWatchlist(): WatchlistItem[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length > 0) return parsed;
    }
  } catch { /* ignore */ }
  return DEFAULT_WATCHLIST;
}

function saveWatchlist(items: WatchlistItem[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
}

const PERIOD_MAP: Record<string, { type: 'day' | 'week' | 'month' | 'minute'; span: number }> = {
  fenshi: { type: 'minute', span: 1 },
  '5min': { type: 'minute', span: 5 },
  '15min': { type: 'minute', span: 15 },
  '30min': { type: 'minute', span: 30 },
  '60min': { type: 'minute', span: 60 },
  daily: { type: 'day', span: 1 },
  weekly: { type: 'week', span: 1 },
  monthly: { type: 'month', span: 1 },
};

const PERIODS = [
  { value: 'fenshi', label: '分时' },
  { value: '5min', label: '5分' },
  { value: '15min', label: '15分' },
  { value: '30min', label: '30分' },
  { value: '60min', label: '60分' },
  { value: 'daily', label: '日K' },
  { value: 'weekly', label: '周K' },
  { value: 'monthly', label: '月K' },
];

const INDICATOR_LIST = [
  { key: 'MACD', display: 'MACD', color: '#9b59b6' },
  { key: 'KDJ', display: 'KDJ', color: '#e67e22' },
  { key: 'RSI', display: 'RSI', color: '#2ecc71' },
  { key: 'BOLL', display: 'BOLL', color: '#3498db' },
  { key: 'ZHU_LI_SHA_ZHUANG', display: '主力杀庄', color: '#e74c3c' },
  { key: 'CAPITAL_FLOW', display: '资金流向', color: '#1abc9c' },
];

function fmtVol(v: number | null | undefined): string {
  if (v == null) return '--';
  if (v >= 1e8) return (v / 1e8).toFixed(2) + '亿';
  if (v >= 1e4) return (v / 1e4).toFixed(2) + '万';
  return v.toLocaleString();
}

function fmtAmt(a: number | null | undefined): string {
  if (a == null) return '--';
  if (a >= 1e8) return (a / 1e8).toFixed(2) + '亿';
  if (a >= 1e4) return (a / 1e4).toFixed(2) + '万';
  return a.toLocaleString();
}

function fmtMv(v: number | null | undefined): string {
  if (v == null) return '--';
  if (v >= 1e12) return (v / 1e12).toFixed(2) + '万亿';
  if (v >= 1e8) return (v / 1e8).toFixed(2) + '亿';
  if (v >= 1e4) return (v / 1e4).toFixed(2) + '万';
  return v.toLocaleString();
}

function fmtP(p: number | null | undefined): string {
  if (p == null) return '--';
  return p.toFixed(2);
}

function fmtPct(p: number | null | undefined): string {
  if (p == null) return '--';
  return p.toFixed(2) + '%';
}

function fmtHand(v: number): string {
  if (v >= 10000) return (v / 10000).toFixed(1) + '万';
  return v.toString();
}

function pColor(price: number, ref: number): string {
  if (price > ref) return 'text-red-400';
  if (price < ref) return 'text-emerald-400';
  return 'text-secondary-text';
}

const Spinner = ({ size = 3 }: { size?: number }) => (
  <svg className={`h-${size} w-${size} animate-spin`} viewBox="0 0 24 24">
    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
  </svg>
);

function toPureCode(code: string): string {
  return code.replace(/\.(SZ|SH|BJ|SS)$/i, '');
}

const MarketPage: React.FC = () => {
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>(loadWatchlist);
  const [selectedCode, setSelectedCode] = useState('600519');
  const [quote, setQuote] = useState<StockQuote | null>(null);
  const [quoteLoading, _setQuoteLoading] = useState(false);
  const [chartLoading, setChartLoading] = useState(true);
  const [chartError, setChartError] = useState<string | null>(null);
  const [activePeriod, setActivePeriod] = useState('fenshi');
  const [quotesMap, setQuotesMap] = useState<Record<string, StockQuote>>({});
  const [activeIndicator, setActiveIndicator] = useState<string | null>(null);
  const [indicatorOutputs, setIndicatorOutputs] = useState<Record<string, (number | null)[]> | null>(null);
  const [indicatorLoading, setIndicatorLoading] = useState(false);
  const [showAddStock, setShowAddStock] = useState(false);
  const [orderbook, setOrderbook] = useState<OrderBookResponse | null>(null);
  const [ticksData, setTicksData] = useState<TradeTicksResponse | null>(null);
  const [searchValue, setSearchValue] = useState('');
  const [sidebarSearchValue, setSidebarSearchValue] = useState('');
  const [indexQuotes, setIndexQuotes] = useState<IndexQuote[]>([]);
  const [rightTab, setRightTab] = useState<'orderbook' | 'finance' | 'xdxr' | 'signal'>('orderbook');
  const [financeInfo, setFinanceInfo] = useState<FinanceInfo | null>(null);
  const [xdxrInfo, setXdxrInfo] = useState<XdxrResponse | null>(null);
  const [rpsData, setRpsData] = useState<RpsData | null>(null);
  const [divergenceData, setDivergenceData] = useState<DivergenceData | null>(null);
  const [resonanceData, setResonanceData] = useState<ResonanceData | null>(null);
  const [backtestData, setBacktestData] = useState<BacktestSummaryData | null>(null);

  const chartRef = useRef<HTMLDivElement>(null);
  const chartInst = useRef<Chart | null>(null);
  const ticksEndRef = useRef<HTMLDivElement>(null);
  const prevCloseRef = useRef<number>(0);

  useEffect(() => { document.title = '行情 - 牛气'; }, []);
  useEffect(() => { saveWatchlist(watchlist); }, [watchlist]);

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
    if (rightTab === 'finance') {
      let active = true;
      stocksApi.getFinanceInfo(selectedCode)
        .then((data) => { if (active) setFinanceInfo(data); })
        .catch(() => { if (active) setFinanceInfo(null); });
      return () => { active = false; };
    }
    if (rightTab === 'xdxr') {
      let active = true;
      stocksApi.getXdxrInfo(selectedCode)
        .then((data) => { if (active) setXdxrInfo(data); })
        .catch(() => { if (active) setXdxrInfo(null); });
      return () => { active = false; };
    }
    if (rightTab === 'signal') {
      let active = true;
      Promise.all([
        stocksApi.getRps(selectedCode),
        stocksApi.getDivergence(selectedCode),
        stocksApi.getResonance(selectedCode),
        stocksApi.getBacktestSummary(selectedCode),
      ]).then(([rps, div, res, bt]) => {
        if (active) {
          setRpsData(rps);
          setDivergenceData(div);
          setResonanceData(res);
          setBacktestData(bt);
        }
      }).catch(() => {
        if (active) {
          setRpsData(null);
          setDivergenceData(null);
          setResonanceData(null);
          setBacktestData(null);
        }
      });
      return () => { active = false; };
    }
  }, [rightTab, selectedCode]);

  const addStock = useCallback((code: string, name?: string) => {
    setWatchlist((prev) => {
      if (prev.some((item) => item.code === code)) return prev;
      return [...prev, { code, name: name || code }];
    });
    setSelectedCode(code);
    setShowAddStock(false);
  }, []);

  const removeStock = useCallback((code: string) => {
    setWatchlist((prev) => prev.filter((item) => item.code !== code));
  }, []);

  const isFenshi = activePeriod === 'fenshi';

  useEffect(() => {
    if (!chartRef.current) return;
    const chart = init(chartRef.current);
    if (!chart) return;
    chartInst.current = chart;

    chart.setStyles({
      grid: {
        show: true,
        horizontal: { show: true, size: 1, color: 'rgba(255,255,255,0.04)', style: 'dashed' as const },
        vertical: { show: true, size: 1, color: 'rgba(255,255,255,0.04)', style: 'dashed' as const },
      },
      candle: {
        type: 'candle_solid',
        priceMark: { last: { show: true } },
        bar: {
          upColor: '#f87171', downColor: '#34d399',
          upBorderColor: '#f87171', downBorderColor: '#34d399',
          upWickColor: '#f87171', downWickColor: '#34d399',
        },
        tooltip: {
          showRule: 'always',
          showType: 'standard',
          legend: {
            template: [
              { title: '时间', value: '{time}' },
              { title: '开', value: '{open}' },
              { title: '高', value: '{high}' },
              { title: '低', value: '{low}' },
              { title: '收', value: '{close}' },
              { title: '量', value: '{volume}' },
            ],
          },
        },
      },
      indicator: {
        tooltip: {
          showRule: 'always',
          showType: 'standard',
        },
      },
      xAxis: {
        axisLine: { show: true, color: 'rgba(255,255,255,0.08)' },
        tickLine: { show: true, size: 1, length: 3, color: 'rgba(255,255,255,0.08)' },
        tickText: { show: true, color: 'rgba(255,255,255,0.35)', size: 10 },
      },
      yAxis: {
        axisLine: { show: true, color: 'rgba(255,255,255,0.08)' },
        tickLine: { show: true, size: 1, length: 3, color: 'rgba(255,255,255,0.08)' },
        tickText: { show: true, color: 'rgba(255,255,255,0.35)', size: 10 },
      },
      crosshair: {
        horizontal: {
          show: true,
          line: { show: true, style: 'dashed', color: 'rgba(255,255,255,0.15)' },
          text: { show: true, color: 'rgba(255,255,255,0.6)', size: 10 },
        },
        vertical: {
          show: true,
          line: { show: true, style: 'dashed', color: 'rgba(255,255,255,0.15)' },
          text: { show: true, color: 'rgba(255,255,255,0.6)', size: 10 },
        },
      },
    });

    chart.createIndicator('VOL');

    const PERIOD_TO_API: Record<string, string> = {
      'day': 'daily', 'week': 'weekly', 'month': 'monthly',
    };
    const MINUTE_API_MAP: Record<number, string> = {
      1: '1min', 5: '5min', 15: '15min', 30: '30min', 60: '60min',
    };
    const DAYS_MAP: Record<string, number> = {
      daily: 365, weekly: 500, monthly: 200,
      '1min': 1, '5min': 5, '15min': 10, '30min': 15, '60min': 30,
    };

    const realtimeTimers: Record<string, ReturnType<typeof setInterval>> = {};

    chart.setDataLoader({
      getBars: async (params) => {
        const code = params.symbol.ticker || '600519';
        let apiPeriod: string;
        if (params.period.type === 'minute') {
          apiPeriod = MINUTE_API_MAP[params.period.span] || '1min';
        } else {
          apiPeriod = PERIOD_TO_API[params.period.type] || 'daily';
        }
        const days = DAYS_MAP[apiPeriod] || 365;
        try {
          setChartLoading(true);
          setChartError(null);
          const resp = await stocksApi.getHistory(code, apiPeriod, days);
          if (!resp.data.length) {
            setChartError('暂无K线数据');
            params.callback([], { backward: false, forward: false });
            return;
          }
          const klineData = resp.data.map((item) => ({
            timestamp: new Date(item.date).getTime(),
            open: item.open, high: item.high, low: item.low, close: item.close,
            volume: item.volume ?? undefined,
          }));
          params.callback(klineData, { backward: false, forward: false });
        } catch (err) {
          console.error('[KLine] getBars failed:', apiPeriod, err);
          setChartError('图表数据加载失败');
          params.callback([], { backward: false, forward: false });
        } finally {
          setChartLoading(false);
        }
      },
      subscribeBar: (params) => {
        const code = params.symbol.ticker || '600519';
        const periodType = params.period.type;
        const timerKey = `${code}_${periodType}_${params.period.span}`;
        if (realtimeTimers[timerKey]) clearInterval(realtimeTimers[timerKey]);
        const poll = async () => {
          try {
            if (periodType === 'day') {
              const q = await stocksApi.getQuote(code);
              if (q.currentPrice <= 0) return;
              const now = new Date();
              const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
              params.callback({
                timestamp: today,
                open: q.open ?? q.currentPrice,
                high: q.high ?? q.currentPrice,
                low: q.low ?? q.currentPrice,
                close: q.currentPrice,
                volume: q.volume ?? undefined,
              });
            } else if (periodType === 'minute') {
              const apiPeriod = MINUTE_API_MAP[params.period.span] || '1min';
              const days = DAYS_MAP[apiPeriod] || 1;
              const resp = await stocksApi.getHistory(code, apiPeriod, days);
              if (!resp.data.length) return;
              const last = resp.data[resp.data.length - 1];
              params.callback({
                timestamp: new Date(last.date).getTime(),
                open: last.open,
                high: last.high,
                low: last.low,
                close: last.close,
                volume: last.volume ?? undefined,
              });
            }
          } catch { /* ignore */ }
        };
        poll();
        realtimeTimers[timerKey] = setInterval(poll, 30000);
      },
      unsubscribeBar: (params) => {
        const code = params.symbol.ticker || '600519';
        const timerKey = `${code}_${params.period.type}_${params.period.span}`;
        if (realtimeTimers[timerKey]) {
          clearInterval(realtimeTimers[timerKey]);
          delete realtimeTimers[timerKey];
        }
      },
    });

    chart.setSymbol({ ticker: selectedCode });
    chart.setPeriod(PERIOD_MAP['fenshi']);

    const onResize = () => { chart.resize(); };
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      Object.values(realtimeTimers).forEach(t => clearInterval(t));
      if (chartRef.current) { dispose(chartRef.current); }
      chartInst.current = null;
    };
  }, []);

  useEffect(() => {
    if (!chartInst.current) return;
    const chart = chartInst.current;
    if (isFenshi) {
      chart.setStyles({
        candle: {
          type: 'area',
          area: {
            lineSize: 1.5,
            lineColor: '#3b82f6',
            value: 'close',
            smooth: false,
            backgroundColor: [
              { offset: 0, color: 'rgba(59,130,246,0.25)' },
              { offset: 1, color: 'rgba(59,130,246,0)' },
            ],
          },
          priceMark: {
            last: { show: true, line: { show: true } },
          },
          bar: {
            upColor: '#f87171', downColor: '#34d399',
            upBorderColor: '#f87171', downBorderColor: '#34d399',
            upWickColor: '#f87171', downWickColor: '#34d399',
          },
        },
      });
      chart.removeIndicator({ paneId: 'candle_pane', name: 'MA' });
      chart.createIndicator(
        {
          name: 'AVG',
          calc: (data) => {
            let totalVol = 0;
            let totalAmt = 0;
            return data.map((d) => {
              const v = d.volume ?? 0;
              const c = d.close ?? 0;
              totalVol += v;
              totalAmt += v * c;
              if (totalVol > 0) return { avg: totalAmt / totalVol };
              return { avg: undefined };
            });
          },
          series: 'price',
          styles: {
            line: [
              { color: '#f59e0b', size: 1, style: 'solid' as const },
            ],
          },
        },
        false,
        { id: 'candle_pane' },
      );
      chart.setPaneOptions({
        id: 'candle_pane',
        axis: {
          name: 'yAxis',
          position: 'right',
          createTicks: (params) => {
            const pc = prevCloseRef.current;
            if (pc <= 0) return params.defaultTicks;
            return params.defaultTicks.map((tick) => {
              const val = Number(tick.value);
              const pct = ((val - pc) / pc) * 100;
              return { ...tick, text: pct.toFixed(2) + '%' };
            });
          },
        },
      });
      setTimeout(() => {
        const range = chart.getVisibleRange();
        const total = range.to - range.from;
        const barSpace = chart.getBarSpace();
        const containerWidth = chartRef.current?.clientWidth ?? 800;
        const neededSpace = containerWidth / Math.max(total, 1);
        if (neededSpace < barSpace.bar) {
          chart.setBarSpace(Math.max(1, Math.floor(neededSpace * 0.9)));
        }
        chart.scrollToRealTime();
      }, 200);
    } else {
      chart.setStyles({
        candle: {
          type: 'candle_solid',
          priceMark: { last: { show: true } },
          bar: {
            upColor: '#f87171', downColor: '#34d399',
            upBorderColor: '#f87171', downBorderColor: '#34d399',
            upWickColor: '#f87171', downWickColor: '#34d399',
          },
          area: {
            lineSize: 1.5,
            lineColor: '#3b82f6',
            value: 'close',
            smooth: false,
            backgroundColor: [
              { offset: 0, color: 'rgba(59,130,246,0.25)' },
              { offset: 1, color: 'rgba(59,130,246,0)' },
            ],
          },
        },
      });
      chart.removeIndicator({ paneId: 'candle_pane', name: 'AVG' });
      chart.createIndicator('MA', false, { id: 'candle_pane' });
      chart.setPaneOptions({
        id: 'candle_pane',
        axis: {
          name: 'yAxis',
          position: 'right',
        },
      });
      chart.setBarSpace(8);
      chart.scrollToRealTime();
    }
    chart.setSymbol({ ticker: selectedCode });
    chart.setPeriod(PERIOD_MAP[activePeriod]);
  }, [selectedCode, activePeriod]);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try { const q = await stocksApi.getQuote(selectedCode); if (active) { setQuote(q); if ((q.prevClose ?? 0) > 0) prevCloseRef.current = q.prevClose ?? 0; } }
      catch { if (active) setQuote(null); }
    };
    load();
    const t = setInterval(load, 30000);
    return () => { active = false; clearInterval(t); };
  }, [selectedCode]);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try { const ob = await stocksApi.getOrderbook(selectedCode); if (active) setOrderbook(ob); }
      catch { if (active) setOrderbook(null); }
    };
    load();
    const t = setInterval(load, 30000);
    return () => { active = false; clearInterval(t); };
  }, [selectedCode]);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try { const td = await stocksApi.getTradeTicks(selectedCode, 60); if (active) setTicksData(td); }
      catch { if (active) setTicksData(null); }
    };
    load();
    const t = setInterval(load, 30000);
    return () => { active = false; clearInterval(t); };
  }, [selectedCode]);

  useEffect(() => {
    let active = true;
    const loadAll = async () => {
      const map: Record<string, StockQuote> = {};
      await Promise.allSettled(watchlist.map(async (item) => {
        try { const q = await stocksApi.getQuote(item.code); map[item.code] = q; } catch { /* skip */ }
      }));
      if (active) setQuotesMap(map);
    };
    loadAll();
    const t = setInterval(loadAll, 60000);
    return () => { active = false; clearInterval(t); };
  }, [watchlist]);

  useEffect(() => {
    if (!activeIndicator) { setIndicatorOutputs(null); return; }
    let active = true;
    const load = async () => {
      setIndicatorLoading(true);
      try {
        const resp = await formulaApi.runIndicator(activeIndicator, selectedCode, activePeriod, 120);
        if (active) setIndicatorOutputs(resp.outputs);
      } catch { if (active) setIndicatorOutputs(null); }
      finally { if (active) setIndicatorLoading(false); }
    };
    load();
    return () => { active = false; };
  }, [activeIndicator, selectedCode, activePeriod]);

  useEffect(() => {
    if (ticksData && ticksEndRef.current) {
      ticksEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [ticksData]);

  const handleAutocompleteSubmit = useCallback((code: string, name?: string, _source?: string) => {
    addStock(toPureCode(code), name);
    setSearchValue('');
  }, [addStock]);

  const handleSidebarSearchSubmit = useCallback((code: string, name?: string, _source?: string) => {
    addStock(toPureCode(code), name);
    setSidebarSearchValue('');
  }, [addStock]);

  const changeCls = useMemo(() => {
    if (!quote?.changePercent) return 'text-secondary-text';
    return quote.changePercent > 0 ? 'text-red-400' : quote.changePercent < 0 ? 'text-emerald-400' : 'text-secondary-text';
  }, [quote]);

  const refPrice = orderbook?.preClose || quote?.prevClose || 0;

  const maxVol = useMemo(() => {
    if (!orderbook) return 1;
    return Math.max(...orderbook.asks.map(a => a.volume), ...orderbook.bids.map(b => b.volume), 1);
  }, [orderbook]);

  return (
    <div className="h-full flex flex-col overflow-hidden bg-background">

      {/* 大盘指数行情条 */}
      <div className="shrink-0 flex items-center gap-4 px-3 py-0.5 bg-foreground/3 border-b border-border">
        {indexQuotes.map((idx) => {
          const pct = idx.changePercent;
          const cls = pct != null
            ? pct > 0 ? 'text-red-400' : pct < 0 ? 'text-emerald-400' : 'text-secondary-text'
            : 'text-secondary-text';
          return (
            <div key={idx.code} className="flex items-center gap-1 text-[10px] font-mono">
              <span className="text-muted-text">{idx.name}</span>
              <span className={`font-medium ${cls}`}>{idx.price > 0 ? idx.price.toFixed(2) : '--'}</span>
              {pct != null && (
                <span className={`${cls}`}>
                  {pct > 0 ? '+' : ''}{pct.toFixed(2)}%
                </span>
              )}
            </div>
          );
        })}
      </div>

      {/* ====== 顶栏：股票信息 + 搜索 ====== */}
      <div className="shrink-0 border-b border-border bg-background px-3 py-1.5">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-xs font-bold text-foreground shrink-0">
              {quote?.stockName || selectedCode}
            </span>
            <span className="text-[10px] text-muted-text font-mono shrink-0">{selectedCode}</span>
            {quoteLoading ? <Spinner size={3} /> : quote ? (
              <>
                <span className={`text-base font-bold font-mono ${changeCls} shrink-0`}>
                  {fmtP(quote.currentPrice)}
                </span>
                {quote.change != null && (
                  <span className={`text-[11px] font-mono ${changeCls} shrink-0`}>
                    {quote.change > 0 ? '+' : ''}{quote.change.toFixed(2)}
                  </span>
                )}
                {quote.changePercent != null && (
                  <span className={`text-[11px] font-mono px-1 py-px rounded-sm shrink-0 ${
                    quote.changePercent > 0 ? 'bg-red-400/15 text-red-400' :
                    quote.changePercent < 0 ? 'bg-emerald-400/15 text-emerald-400' :
                    'bg-foreground/5 text-secondary-text'
                  }`}>
                    {quote.changePercent > 0 ? '+' : ''}{quote.changePercent.toFixed(2)}%
                  </span>
                )}
              </>
            ) : <span className="text-[10px] text-muted-text">暂无行情</span>}
          </div>
          <div className="w-48 shrink-0">
            <StockAutocomplete
              value={searchValue}
              onChange={setSearchValue}
              onSubmit={handleAutocompleteSubmit}
              placeholder="搜索股票"
              className="!h-7 !text-[11px] !px-2 !rounded-sm !bg-foreground/5 !border-border"
            />
          </div>
        </div>
        {quote && (
          <>
            <div className="flex gap-x-4 gap-y-0 mt-1 text-[10px] font-mono">
              <span><span className="text-muted-text">开</span> <span>{fmtP(quote.open)}</span></span>
              <span><span className="text-muted-text">高</span> <span className="text-red-400">{fmtP(quote.high)}</span></span>
              <span><span className="text-muted-text">低</span> <span className="text-emerald-400">{fmtP(quote.low)}</span></span>
              <span><span className="text-muted-text">昨收</span> <span>{fmtP(quote.prevClose)}</span></span>
              <span><span className="text-muted-text">量</span> <span>{fmtVol(quote.volume)}</span></span>
              <span><span className="text-muted-text">额</span> <span>{fmtAmt(quote.amount)}</span></span>
            </div>
            <div className="flex gap-x-4 gap-y-0 mt-0.5 text-[10px] font-mono">
              <span><span className="text-muted-text">换手</span> <span>{fmtPct(quote.turnoverRate)}</span></span>
              <span><span className="text-muted-text">量比</span> <span>{quote.volumeRatio != null ? quote.volumeRatio.toFixed(2) : '--'}</span></span>
              <span><span className="text-muted-text">振幅</span> <span>{fmtPct(quote.amplitude)}</span></span>
              <span><span className="text-muted-text">PE</span> <span>{quote.peRatio != null ? quote.peRatio.toFixed(2) : '--'}</span></span>
              <span><span className="text-muted-text">PB</span> <span>{quote.pbRatio != null ? quote.pbRatio.toFixed(2) : '--'}</span></span>
              <span><span className="text-muted-text">总市值</span> <span>{fmtMv(quote.totalMv)}</span></span>
              <span><span className="text-muted-text">流通市值</span> <span>{fmtMv(quote.circMv)}</span></span>
              {(quote.high52w != null || quote.low52w != null) && (
                <span><span className="text-muted-text">52周</span> <span className="text-red-400">{fmtP(quote.high52w)}</span>/<span className="text-emerald-400">{fmtP(quote.low52w)}</span></span>
              )}
            </div>
          </>
        )}
      </div>

      {/* ====== 主体三栏 ====== */}
      <div className="flex-1 min-h-0 flex">

        {/* ── 左栏：自选股 ── */}
        <aside className="w-[180px] shrink-0 border-r border-border flex flex-col bg-background">
          <div className="px-2 py-1 border-b border-border flex items-center justify-between">
            <span className="text-[10px] font-medium text-muted-text font-mono">自选股</span>
            <button
              onClick={() => setShowAddStock(!showAddStock)}
              className="text-[10px] text-muted-text hover:text-foreground font-mono"
            >
              {showAddStock ? '[-]' : '[+]'}
            </button>
          </div>
          {showAddStock && (
            <div className="px-1.5 py-1 border-b border-border">
              <StockAutocomplete
                value={sidebarSearchValue}
                onChange={setSidebarSearchValue}
                onSubmit={handleSidebarSearchSubmit}
                placeholder="代码/名称"
                className="!h-6 !text-[10px] !px-2 !rounded-sm !bg-foreground/5 !border-border"
              />
            </div>
          )}
          <div className="flex-1 min-h-0 overflow-y-auto">
            {watchlist.map((item) => {
              const q = quotesMap[item.code];
              const isActive = selectedCode === item.code;
              const pct = q?.changePercent;
              const cls = pct != null
                ? pct > 0 ? 'text-red-400' : pct < 0 ? 'text-emerald-400' : 'text-secondary-text'
                : 'text-secondary-text';
              return (
                <div
                  key={item.code}
                  className={`flex items-center group cursor-pointer ${isActive ? 'bg-foreground/8' : 'hover:bg-foreground/4'}`}
                  onClick={() => setSelectedCode(item.code)}
                >
                  <div className={`w-[2px] self-stretch shrink-0 ${isActive ? 'bg-red-400' : 'bg-transparent'}`} />
                  <div className="flex-1 flex items-center justify-between px-2 py-[5px] min-w-0">
                    <div className="min-w-0">
                      <p className="truncate text-[11px] text-foreground leading-tight">{item.name || item.code}</p>
                      <p className="text-[9px] text-muted-text font-mono">{item.code}</p>
                    </div>
                    <div className="text-right shrink-0 ml-1">
                      {q ? (
                        <>
                          <p className={`text-[11px] font-mono ${cls} leading-tight`}>{fmtP(q.currentPrice)}</p>
                          <p className={`text-[9px] font-mono ${cls}`}>
                            {pct != null ? `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%` : '--'}
                          </p>
                        </>
                      ) : <p className="text-[9px] text-muted-text font-mono">--</p>}
                    </div>
                  </div>
                  <button
                    onClick={(e) => { e.stopPropagation(); removeStock(item.code); }}
                    className="px-0.5 text-muted-text hover:text-red-400 opacity-0 group-hover:opacity-100 transition-opacity text-[9px] shrink-0"
                  >✕</button>
                </div>
              );
            })}
          </div>
        </aside>

        {/* ── 中栏：K线 ── */}
        <main className="flex-1 min-w-0 flex flex-col">
          {/* 周期 + 指标标签 */}
          <div className="shrink-0 px-2 py-0.5 border-b border-border flex items-center gap-0.5 bg-background">
            {PERIODS.map((p, i) => (
              <Fragment key={p.value}>
                {i === 5 && <span className="w-px h-3 bg-border mx-1" />}
                <button
                  onClick={() => setActivePeriod(p.value)}
                  className={`px-2 py-0.5 text-[10px] font-mono rounded-sm transition-colors ${
                    activePeriod === p.value
                      ? 'bg-foreground/12 text-foreground'
                      : 'text-muted-text hover:text-foreground hover:bg-foreground/5'
                  }`}
                >
                  {p.label}
                </button>
              </Fragment>
            ))}
            <span className="w-px h-3 bg-border mx-1.5" />
            {INDICATOR_LIST.map((ind) => (
              <button
                key={ind.key}
                onClick={() => setActiveIndicator(activeIndicator === ind.key ? null : ind.key)}
                className={`px-1.5 py-0.5 text-[10px] font-mono rounded-sm transition-colors flex items-center gap-1 ${
                  activeIndicator === ind.key
                    ? 'bg-foreground/12 text-foreground'
                    : 'text-muted-text hover:text-foreground hover:bg-foreground/5'
                }`}
              >
                <span className="w-1 h-1 rounded-full shrink-0" style={{ backgroundColor: ind.color }} />
                {ind.display}
              </button>
            ))}
          </div>

          {/* K线图 */}
          <div className="flex-1 min-h-0 relative bg-[#0a0a0f]">
            {chartLoading && (
              <div className="absolute inset-0 flex items-center justify-center z-10 bg-[#0a0a0f]/80">
                <div className="flex items-center gap-2 text-muted-text">
                  <Spinner size={4} />
                  <span className="text-[10px] font-mono">加载中...</span>
                </div>
              </div>
            )}
            {chartError && !chartLoading && (
              <div className="absolute inset-0 flex items-center justify-center z-10">
                <span className="text-[10px] text-muted-text font-mono">{chartError}</span>
              </div>
            )}
            <div ref={chartRef} className="w-full h-full" />
          </div>

          {/* 指标结果条 */}
          {activeIndicator && indicatorOutputs && !indicatorLoading && (
            <div className="shrink-0 px-2 py-1 border-t border-border bg-background">
              <div className="flex items-center gap-3 flex-wrap">
                {Object.entries(indicatorOutputs).map(([key, values]) => {
                  const last = values.length > 0 ? values[values.length - 1] : null;
                  const prev = values.length > 1 ? values[values.length - 2] : null;
                  const trend = last != null && prev != null
                    ? last > prev ? '↑' : last < prev ? '↓' : '→' : '';
                  const tc = trend === '↑' ? 'text-red-400' : trend === '↓' ? 'text-emerald-400' : 'text-muted-text';
                  return (
                    <div key={key} className="flex items-center gap-1">
                      <span className="text-[10px] text-muted-text font-mono">{key}</span>
                      <span className="text-[11px] font-mono text-foreground">
                        {last != null ? last.toFixed(2) : '--'}
                      </span>
                      <span className={`text-[9px] font-mono ${tc}`}>{trend}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
          {activeIndicator && indicatorLoading && (
            <div className="shrink-0 px-2 py-1 border-t border-border bg-background flex items-center gap-2">
              <Spinner size={3} />
              <span className="text-[10px] text-muted-text font-mono">计算中...</span>
            </div>
          )}
        </main>

        {/* ── 右栏：盘口/财务/除权 ── */}
        <aside className="w-[240px] shrink-0 border-l border-border flex flex-col bg-background">
          {/* Tab 切换 */}
          <div className="shrink-0 px-1.5 py-0.5 border-b border-border flex items-center gap-0.5">
            {([
              { key: 'orderbook' as const, label: '盘口' },
              { key: 'finance' as const, label: '财务' },
              { key: 'xdxr' as const, label: '除权' },
              { key: 'signal' as const, label: '信号' },
            ]).map((tab) => (
              <button
                key={tab.key}
                onClick={() => setRightTab(tab.key)}
                className={`px-2 py-0.5 text-[10px] font-mono rounded-sm transition-colors ${
                  rightTab === tab.key
                    ? 'bg-foreground/12 text-foreground'
                    : 'text-muted-text hover:text-foreground hover:bg-foreground/5'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {/* 盘口Tab */}
          {rightTab === 'orderbook' && (
            <>
              <div className="shrink-0 border-b border-border">
                <div className="px-2 py-1 border-b border-border">
                  <span className="text-[10px] font-medium text-muted-text font-mono">五档盘口</span>
                </div>
                {orderbook ? (
                  <div className="px-2 py-0.5">
                    <div className="space-y-px">
                      {[...orderbook.asks].reverse().map((level, i) => {
                        const label = `卖${5 - i}`;
                        const bw = Math.min((level.volume / maxVol) * 100, 100);
                        return (
                          <div key={`a${4 - i}`} className="flex items-center text-[10px] h-[18px] relative">
                            <div className="absolute right-0 top-0 bottom-0 bg-emerald-400/8" style={{ width: `${bw}%` }} />
                            <span className="w-6 text-muted-text relative z-[1] font-mono">{label}</span>
                            <span className={`flex-1 font-mono ${pColor(level.price, refPrice)} relative z-[1]`}>{level.price.toFixed(2)}</span>
                            <span className="w-12 text-right font-mono text-foreground relative z-[1]">{fmtHand(level.volume)}</span>
                          </div>
                        );
                      })}
                    </div>
                    <div className="flex items-center justify-center py-0.5 border-y border-border my-px">
                      <span className={`text-xs font-bold font-mono ${changeCls}`}>{orderbook.price.toFixed(2)}</span>
                    </div>
                    <div className="space-y-px">
                      {orderbook.bids.map((level, i) => {
                        const label = `买${i + 1}`;
                        const bw = Math.min((level.volume / maxVol) * 100, 100);
                        return (
                          <div key={`b${i}`} className="flex items-center text-[10px] h-[18px] relative">
                            <div className="absolute right-0 top-0 bottom-0 bg-red-400/8" style={{ width: `${bw}%` }} />
                            <span className="w-6 text-muted-text relative z-[1] font-mono">{label}</span>
                            <span className={`flex-1 font-mono ${pColor(level.price, refPrice)} relative z-[1]`}>{level.price.toFixed(2)}</span>
                            <span className="w-12 text-right font-mono text-foreground relative z-[1]">{fmtHand(level.volume)}</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : (
                  <div className="px-2 py-2 text-center text-[10px] text-muted-text font-mono">暂无数据</div>
                )}
              </div>

              <div className="flex-1 min-h-0 flex flex-col border-b border-border">
                <div className="shrink-0 px-2 py-1 border-b border-border">
                  <span className="text-[10px] font-medium text-muted-text font-mono">成交明细</span>
                </div>
                <div className="flex-1 min-h-0 overflow-y-auto">
                  {ticksData && ticksData.ticks.length > 0 ? (
                    <table className="w-full text-[9px]">
                      <thead>
                        <tr className="text-muted-text sticky top-0 bg-background">
                          <th className="text-left font-normal px-1.5 py-px font-mono">时间</th>
                          <th className="text-right font-normal px-1.5 py-px font-mono">价格</th>
                          <th className="text-right font-normal px-1.5 py-px font-mono">量</th>
                          <th className="text-center font-normal px-1.5 py-px">方向</th>
                        </tr>
                      </thead>
                      <tbody>
                        {ticksData.ticks.map((tick, i) => {
                          const dir = tick.type === 0 ? '买' : tick.type === 1 ? '卖' : '中';
                          const dc = tick.type === 0 ? 'text-red-400' : tick.type === 1 ? 'text-emerald-400' : 'text-muted-text';
                          const pc = refPrice > 0 ? pColor(tick.price, refPrice) : 'text-foreground';
                          return (
                            <tr key={i} className="hover:bg-foreground/4">
                              <td className="px-1.5 py-px font-mono text-muted-text">{tick.time}</td>
                              <td className={`px-1.5 py-px font-mono text-right ${pc}`}>{tick.price.toFixed(2)}</td>
                              <td className="px-1.5 py-px font-mono text-right text-foreground">{tick.volume}</td>
                              <td className={`px-1.5 py-px text-center ${dc}`}>{dir}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  ) : (
                    <div className="px-2 py-2 text-center text-[10px] text-muted-text font-mono">暂无数据</div>
                  )}
                  <div ref={ticksEndRef} />
                </div>
              </div>
            </>
          )}

          {/* 财务Tab */}
          {rightTab === 'finance' && (
            <div className="flex-1 min-h-0 overflow-y-auto">
              {financeInfo ? (
                <div className="px-2 py-1.5 space-y-2">
                  {/* 核心指标突出显示 */}
                  <div className="grid grid-cols-3 gap-1">
                    <div className="text-center">
                      <p className="text-[9px] text-muted-text font-mono">动态PE</p>
                      <p className="text-[11px] font-mono text-foreground font-medium">{financeInfo.peDynamic != null ? financeInfo.peDynamic.toFixed(2) : '--'}</p>
                    </div>
                    <div className="text-center">
                      <p className="text-[9px] text-muted-text font-mono">PB</p>
                      <p className="text-[11px] font-mono text-foreground font-medium">{financeInfo.pbRatio != null ? financeInfo.pbRatio.toFixed(2) : '--'}</p>
                    </div>
                    <div className="text-center">
                      <p className="text-[9px] text-muted-text font-mono">ROE</p>
                      <p className="text-[11px] font-mono text-foreground font-medium">{financeInfo.roe != null ? financeInfo.roe.toFixed(2) + '%' : '--'}</p>
                    </div>
                  </div>
                  <div className="border-t border-border" />
                  {/* 两列布局详细数据 */}
                  <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[10px] font-mono">
                    <div className="flex justify-between">
                      <span className="text-muted-text">每股净资产</span>
                      <span className="text-foreground">{financeInfo.bps != null ? financeInfo.bps.toFixed(2) : '--'}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-text">净利润</span>
                      <span className="text-foreground">{fmtAmt(financeInfo.netProfit)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-text">主营收入</span>
                      <span className="text-foreground">{fmtAmt(financeInfo.mainRevenue)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-text">净资产</span>
                      <span className="text-foreground">{fmtAmt(financeInfo.netAssets)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-text">总资产</span>
                      <span className="text-foreground">{fmtAmt(financeInfo.totalAssets)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-text">经营现金流</span>
                      <span className="text-foreground">{fmtAmt(financeInfo.operatingCashFlow)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-text">总股本</span>
                      <span className="text-foreground">{fmtVol(financeInfo.totalShares)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-text">流通股</span>
                      <span className="text-foreground">{fmtVol(financeInfo.floatShares)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-text">股东人数</span>
                      <span className="text-foreground">{financeInfo.shareholderCount != null ? fmtVol(financeInfo.shareholderCount) : '--'}</span>
                    </div>
                  </div>
                  <div className="border-t border-border" />
                  {/* 数据来源与更新日期 */}
                  <div className="text-[9px] text-muted-text font-mono flex justify-between">
                    <span>来源: {financeInfo.source || '--'}</span>
                    <span>{financeInfo.updatedDate || '--'}</span>
                  </div>
                </div>
              ) : (
                <div className="px-2 py-4 text-center text-[10px] text-muted-text font-mono">加载中...</div>
              )}
            </div>
          )}

          {/* 除权Tab */}
          {rightTab === 'xdxr' && (
            <div className="flex-1 min-h-0 overflow-y-auto">
              {xdxrInfo && xdxrInfo.records.length > 0 ? (
                <table className="w-full text-[9px]">
                  <thead>
                    <tr className="text-muted-text sticky top-0 bg-background">
                      <th className="text-left font-normal px-1.5 py-px font-mono">日期</th>
                      <th className="text-left font-normal px-1.5 py-px font-mono">类别</th>
                      <th className="text-right font-normal px-1.5 py-px font-mono">分红</th>
                      <th className="text-right font-normal px-1.5 py-px font-mono">送股</th>
                      <th className="text-right font-normal px-1.5 py-px font-mono">配股</th>
                    </tr>
                  </thead>
                  <tbody>
                    {xdxrInfo.records.slice(0, 10).map((rec, i) => {
                      const dateStr = [rec.year, rec.month, rec.day]
                        .filter((v) => v != null)
                        .map((v) => String(v).padStart(2, '0'))
                        .join('-');
                      return (
                        <tr key={i} className="hover:bg-foreground/4">
                          <td className="px-1.5 py-px font-mono text-muted-text">{dateStr || '--'}</td>
                          <td className="px-1.5 py-px font-mono text-foreground">{rec.categoryName || '--'}</td>
                          <td className="px-1.5 py-px font-mono text-right text-foreground">
                            {rec.dividendPerShare != null ? rec.dividendPerShare.toFixed(4) : '--'}
                          </td>
                          <td className="px-1.5 py-px font-mono text-right text-foreground">
                            {rec.bonusShareRatio != null ? rec.bonusShareRatio.toFixed(4) : '--'}
                          </td>
                          <td className="px-1.5 py-px font-mono text-right text-foreground">
                            {rec.rightsIssueRatio != null
                              ? `${rec.rightsIssueRatio.toFixed(4)}${rec.rightsIssuePrice != null ? '/' + rec.rightsIssuePrice.toFixed(2) : ''}`
                              : '--'}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              ) : (
                <div className="px-2 py-4 text-center text-[10px] text-muted-text font-mono">
                  {xdxrInfo ? '暂无除权除息记录' : '加载中...'}
                </div>
              )}
            </div>
          )}

          {/* 信号Tab */}
          {rightTab === 'signal' && (
            <div className="flex-1 min-h-0 overflow-y-auto">
              {/* RPS相对强度 */}
              <div className="border-b border-border">
                <div className="px-2 py-1 border-b border-border">
                  <span className="text-[10px] font-medium text-muted-text font-mono">RPS 相对强度</span>
                </div>
                {rpsData && rpsData.rps != null ? (
                  <div className="px-2 py-1.5">
                    <div className="flex items-center justify-between">
                      <span className="text-[10px] text-muted-text font-mono">RPS值</span>
                      <span className={`text-sm font-bold font-mono ${
                        rpsData.rps >= 90 ? 'text-red-400' :
                        rpsData.rps >= 70 ? 'text-orange-400' :
                        rpsData.rps >= 50 ? 'text-yellow-400' :
                        rpsData.rps >= 30 ? 'text-blue-400' : 'text-emerald-400'
                      }`}>
                        {rpsData.rps.toFixed(1)}
                      </span>
                    </div>
                    <div className="mt-1 h-1.5 bg-foreground/5 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${
                          rpsData.rps >= 90 ? 'bg-red-400' :
                          rpsData.rps >= 70 ? 'bg-orange-400' :
                          rpsData.rps >= 50 ? 'bg-yellow-400' :
                          rpsData.rps >= 30 ? 'bg-blue-400' : 'bg-emerald-400'
                        }`}
                        style={{ width: `${rpsData.rps}%` }}
                      />
                    </div>
                    <div className="flex items-center justify-between mt-1">
                      <span className="text-[9px] font-mono text-muted-text">
                        {rpsData.periodDays}日涨幅 {rpsData.periodReturn != null ? `${rpsData.periodReturn > 0 ? '+' : ''}${rpsData.periodReturn.toFixed(2)}%` : '--'}
                      </span>
                      <span className={`text-[9px] font-mono font-medium ${
                        rpsData.rps >= 90 ? 'text-red-400' :
                        rpsData.rps >= 70 ? 'text-orange-400' :
                        rpsData.rps >= 50 ? 'text-yellow-400' :
                        rpsData.rps >= 30 ? 'text-blue-400' : 'text-emerald-400'
                      }`}>
                        {rpsData.rankDesc}
                      </span>
                    </div>
                  </div>
                ) : (
                  <div className="px-2 py-3 text-center text-[10px] text-muted-text font-mono">
                    {rpsData ? '计算中...' : '加载中...'}
                  </div>
                )}
              </div>

              {/* 共振评分 */}
              <div className="border-b border-border">
                <div className="px-2 py-1 border-b border-border">
                  <span className="text-[10px] font-medium text-muted-text font-mono">多指标共振</span>
                </div>
                {resonanceData ? (
                  <div className="px-2 py-1.5">
                    <div className="flex items-center justify-between">
                      <span className="text-[10px] text-muted-text font-mono">共振评分</span>
                      <span className={`text-sm font-bold font-mono ${
                        resonanceData.score >= 40 ? 'text-red-400' :
                        resonanceData.score >= 20 ? 'text-orange-400' :
                        resonanceData.score > -20 ? 'text-yellow-400' :
                        resonanceData.score > -40 ? 'text-blue-400' : 'text-emerald-400'
                      }`}>
                        {resonanceData.score > 0 ? '+' : ''}{resonanceData.score}
                      </span>
                    </div>
                    <div className="mt-1 h-1.5 bg-foreground/5 rounded-full overflow-hidden relative">
                      <div className="absolute left-1/2 top-0 bottom-0 w-px bg-foreground/20" />
                      <div
                        className={`h-full rounded-full transition-all ${
                          resonanceData.score >= 40 ? 'bg-red-400' :
                          resonanceData.score >= 20 ? 'bg-orange-400' :
                          resonanceData.score > -20 ? 'bg-yellow-400' :
                          resonanceData.score > -40 ? 'bg-blue-400' : 'bg-emerald-400'
                        }`}
                        style={{
                          width: `${Math.abs(resonanceData.score) / 2}%`,
                          marginLeft: resonanceData.score >= 0 ? '50%' : `${50 - Math.abs(resonanceData.score) / 2}%`,
                        }}
                      />
                    </div>
                    <div className="flex items-center justify-between mt-1">
                      <span className="text-[9px] font-mono text-muted-text">
                        多{resonanceData.bullCount} / 空{resonanceData.bearCount}
                      </span>
                      <span className={`text-[9px] font-mono font-medium ${
                        resonanceData.score >= 40 ? 'text-red-400' :
                        resonanceData.score >= 20 ? 'text-orange-400' :
                        resonanceData.score > -20 ? 'text-yellow-400' :
                        resonanceData.score > -40 ? 'text-blue-400' : 'text-emerald-400'
                      }`}>
                        {resonanceData.level}
                      </span>
                    </div>
                    {resonanceData.signals.length > 0 && (
                      <div className="mt-1 space-y-px">
                        {resonanceData.signals.map((sig, i) => (
                          <div key={i} className="flex items-center justify-between text-[9px] font-mono">
                            <span className={sig.direction === 'bullish' ? 'text-red-400' : 'text-emerald-400'}>
                              {sig.direction === 'bullish' ? '▲' : '▼'} {sig.name}
                            </span>
                            <span className="text-muted-text">
                              {sig.weight > 0 ? '+' : ''}{sig.weight}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="px-2 py-3 text-center text-[10px] text-muted-text font-mono">加载中...</div>
                )}
              </div>

              {/* 量价背离 */}
              <div className="border-b border-border">
                <div className="px-2 py-1 border-b border-border">
                  <span className="text-[10px] font-medium text-muted-text font-mono">量价背离检测</span>
                </div>
                {divergenceData ? (
                  <div className="px-2 py-1.5">
                    {divergenceData.signals.length > 0 ? (
                      <div className="space-y-1">
                        {divergenceData.signals.map((sig, i) => (
                          <div key={i} className="rounded-sm border border-border/50 px-1.5 py-1">
                            <div className="flex items-center justify-between">
                              <span className={`text-[10px] font-medium font-mono ${
                                sig.direction === 'bullish' ? 'text-red-400' : 'text-emerald-400'
                              }`}>
                                {sig.direction === 'bullish' ? '▲' : '▼'} {sig.name}
                              </span>
                              <span className={`text-[8px] px-1 rounded-sm font-mono ${
                                sig.strength === 'strong'
                                  ? 'bg-red-400/15 text-red-400'
                                  : sig.strength === 'medium'
                                  ? 'bg-yellow-400/15 text-yellow-400'
                                  : 'bg-foreground/8 text-muted-text'
                              }`}>
                                {sig.strength === 'strong' ? '强' : sig.strength === 'medium' ? '中' : '弱'}
                              </span>
                            </div>
                            <p className="text-[8px] text-muted-text font-mono mt-0.5 leading-tight">{sig.description}</p>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-[9px] text-muted-text font-mono text-center py-1">暂无明显背离信号</p>
                    )}
                    <p className="text-[8px] text-muted-text font-mono mt-1">{divergenceData.summary}</p>
                  </div>
                ) : (
                  <div className="px-2 py-3 text-center text-[10px] text-muted-text font-mono">加载中...</div>
                )}
              </div>

              {/* 回测准确率 */}
              <div>
                <div className="px-2 py-1 border-b border-border">
                  <span className="text-[10px] font-medium text-muted-text font-mono">历史回测</span>
                </div>
                {backtestData && backtestData.hasData && backtestData.summary ? (
                  <div className="px-2 py-1.5">
                    <div className="grid grid-cols-2 gap-1">
                      <div className="rounded-sm bg-foreground/3 px-1.5 py-1">
                        <p className="text-[8px] text-muted-text font-mono">方向准确率</p>
                        <p className="text-[11px] font-bold font-mono text-foreground">
                          {backtestData.summary.directionAccuracyPct != null
                            ? `${backtestData.summary.directionAccuracyPct.toFixed(1)}%`
                            : '--'}
                        </p>
                      </div>
                      <div className="rounded-sm bg-foreground/3 px-1.5 py-1">
                        <p className="text-[8px] text-muted-text font-mono">胜率</p>
                        <p className="text-[11px] font-bold font-mono text-foreground">
                          {backtestData.summary.winRatePct != null
                            ? `${backtestData.summary.winRatePct.toFixed(1)}%`
                            : '--'}
                        </p>
                      </div>
                      <div className="rounded-sm bg-foreground/3 px-1.5 py-1">
                        <p className="text-[8px] text-muted-text font-mono">评估次数</p>
                        <p className="text-[11px] font-bold font-mono text-foreground">
                          {backtestData.summary.totalEvaluations}
                        </p>
                      </div>
                      <div className="rounded-sm bg-foreground/3 px-1.5 py-1">
                        <p className="text-[8px] text-muted-text font-mono">平均收益</p>
                        <p className={`text-[11px] font-bold font-mono ${
                          backtestData.summary.avgSimulatedReturnPct != null && backtestData.summary.avgSimulatedReturnPct > 0
                            ? 'text-red-400'
                            : backtestData.summary.avgSimulatedReturnPct != null && backtestData.summary.avgSimulatedReturnPct < 0
                            ? 'text-emerald-400'
                            : 'text-foreground'
                        }`}>
                          {backtestData.summary.avgSimulatedReturnPct != null
                            ? `${backtestData.summary.avgSimulatedReturnPct > 0 ? '+' : ''}${backtestData.summary.avgSimulatedReturnPct.toFixed(2)}%`
                            : '--'}
                        </p>
                      </div>
                    </div>
                    {backtestData.recent.length > 0 && (
                      <div className="mt-1.5">
                        <p className="text-[8px] text-muted-text font-mono mb-0.5">近期评估</p>
                        {backtestData.recent.slice(0, 3).map((item, i) => (
                          <div key={i} className="flex items-center justify-between text-[8px] font-mono py-px">
                            <span className="text-muted-text">{item.analysisDate || '--'}</span>
                            <span className={item.directionCorrect === true ? 'text-red-400' : item.directionCorrect === false ? 'text-emerald-400' : 'text-muted-text'}>
                              {item.directionCorrect === true ? '✓' : item.directionCorrect === false ? '✗' : '--'}
                            </span>
                            <span className="text-foreground">
                              {item.stockReturnPct != null ? `${item.stockReturnPct > 0 ? '+' : ''}${item.stockReturnPct.toFixed(2)}%` : '--'}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="px-2 py-3 text-center text-[10px] text-muted-text font-mono">
                    {backtestData ? '暂无回测数据' : '加载中...'}
                  </div>
                )}
              </div>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
};

export default MarketPage;
