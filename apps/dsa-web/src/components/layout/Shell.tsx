import type React from 'react';
import { useEffect, useState, useCallback } from 'react';
import { Menu } from 'lucide-react';
import { Outlet } from 'react-router-dom';
import { SidebarNav } from './SidebarNav';
import { cn } from '../../utils/cn';
import { ThemeToggle } from '../theme/ThemeToggle';
import { stocksApi, type IndexQuote } from '../../api/stocks';

type ShellProps = {
  children?: React.ReactNode;
};

const MARKET_INDICES: { name: string; code: string }[] = [
  { name: '上证', code: '000001' },
  { name: '深证', code: '399001' },
  { name: '创业板', code: '399006' },
];

function getMarketStatus() {
  const now = new Date();
  const h = now.getHours();
  const m = now.getMinutes();
  const day = now.getDay();
  if (day === 0 || day === 6) return { label: '休市', active: false };
  const inMorning = (h === 9 && m >= 30) || h === 10 || (h === 11 && m <= 30);
  const inAfternoon = h === 13 || (h === 14) || (h === 15 && m === 0);
  if (inMorning || inAfternoon) return { label: '开盘中', active: true };
  if ((h === 9 && m < 30) || (h < 9)) return { label: '盘前', active: false };
  return { label: '已收盘', active: false };
}

function formatTime(d: Date) {
  return d.toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export const Shell: React.FC<ShellProps> = ({ children }) => {
  const [collapsed, setCollapsed] = useState(true);
  const [now, setNow] = useState(new Date());
  const [mobileOpen, setMobileOpen] = useState(false);
  const [indexMap, setIndexMap] = useState<Record<string, IndexQuote>>({});

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const data = await stocksApi.getIndexQuotes();
        if (active) {
          const map: Record<string, IndexQuote> = {};
          data.forEach((q) => { map[q.code] = q; });
          setIndexMap(map);
        }
      } catch { /* ignore */ }
    };
    load();
    const t = setInterval(load, 120000);
    return () => { active = false; clearInterval(t); };
  }, []);

  useEffect(() => {
    if (!mobileOpen) return undefined;
    const handleResize = () => {
      if (window.innerWidth >= 1024) setMobileOpen(false);
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [mobileOpen]);

  const toggleCollapsed = useCallback(() => setCollapsed((c) => !c), []);

  const market = getMarketStatus();

  return (
    <div className="h-screen bg-background text-foreground flex flex-col overflow-hidden">
      <header className="h-12 shrink-0 flex items-center border-b border-border bg-card/80 backdrop-blur-sm px-3 gap-3 z-40">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold tracking-wider text-foreground">牛气</span>
          <span className="hidden sm:inline text-[10px] text-muted-text font-mono">TERMINAL</span>
        </div>

        <div className="h-4 w-px bg-border mx-1" />

        <div className="flex items-center gap-1.5">
          <span
            className={cn(
              'h-1.5 w-1.5 rounded-full',
              market.active ? 'bg-success' : 'bg-muted-text'
            )}
          />
          <span className={cn('text-[11px] font-mono', market.active ? 'text-success' : 'text-muted-text')}>
            {market.label}
          </span>
        </div>

        <div className="h-4 w-px bg-border mx-1" />

        <div className="hidden md:flex items-center gap-3">
          {MARKET_INDICES.map((idx) => {
            const q = indexMap[idx.code];
            const pct = q?.changePercent;
            const isUp = pct != null && pct > 0;
            const isDown = pct != null && pct < 0;
            return (
              <span key={idx.name} className="flex items-center gap-1 text-[11px] font-mono">
                <span className="text-muted-text">{idx.name}</span>
                <span className="text-foreground">{q && q.price > 0 ? q.price.toFixed(2) : '--'}</span>
                <span className={isUp ? 'text-success' : isDown ? 'text-danger' : 'text-muted-text'}>
                  {pct != null ? `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%` : '--'}
                </span>
              </span>
            );
          })}
        </div>

        <div className="flex-1" />

        <span className="text-[11px] font-mono text-muted-text tabular-nums">{formatTime(now)}</span>

        <div className="h-4 w-px bg-border mx-1" />

        <ThemeToggle />

        <button
          type="button"
          onClick={() => setMobileOpen(true)}
          className="lg:hidden inline-flex h-7 w-7 items-center justify-center rounded border border-border/70 bg-card/80 text-secondary-text transition-colors hover:bg-hover hover:text-foreground"
          aria-label="打开导航菜单"
        >
          <Menu className="h-3.5 w-3.5" />
        </button>
      </header>

      <div className="flex flex-1 min-h-0">
        <aside
          className={cn(
            'hidden lg:flex shrink-0 flex-col border-r border-border bg-card/60 transition-[width] duration-200 overflow-hidden',
            collapsed ? 'w-12' : 'w-[200px]'
          )}
          aria-label="桌面侧边导航"
        >
          <SidebarNav collapsed={collapsed} onNavigate={() => setMobileOpen(false)} onToggleCollapse={toggleCollapsed} />
        </aside>

        <main className="min-h-0 min-w-0 flex-1 overflow-auto">
          {children ?? <Outlet />}
        </main>
      </div>

      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setMobileOpen(false)} />
          <aside className="absolute left-0 top-0 bottom-0 w-[220px] bg-card border-r border-border flex flex-col animate-slide-in-left">
            <SidebarNav collapsed={false} onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}
    </div>
  );
};
