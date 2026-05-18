import React, { useEffect, useRef, useState } from 'react';
import type { ProgressStep } from '../../stores/agentChatStore';

type PhaseId = 'overview' | 'tech' | 'fundamental' | 'news' | 'conclude';

interface Phase {
  id: PhaseId;
  label: string;
  subLabel: string;
  icon: string;
  tools: string[];
}

const PHASES: Phase[] = [
  {
    id: 'overview',
    label: '全景扫描',
    subLabel: '大盘+个股定位',
    icon: '🌍',
    tools: ['get_market_indices', 'get_sector_rankings', 'get_realtime_quote'],
  },
  {
    id: 'tech',
    label: '量价深度',
    subLabel: '技术+筹码+资金',
    icon: '📈',
    tools: ['get_daily_history', 'analyze_trend', 'get_volume_analysis', 'analyze_pattern', 'get_chip_distribution', 'get_capital_flow'],
  },
  {
    id: 'fundamental',
    label: '基本面+估值',
    subLabel: '财务+估值水位',
    icon: '🏦',
    tools: ['get_stock_info', 'get_financial_deep_analysis', 'get_valuation_percentile'],
  },
  {
    id: 'news',
    label: '消息+情绪',
    subLabel: '新闻+市场心理',
    icon: '📰',
    tools: ['search_stock_news', 'search_comprehensive_intel', 'web_search', 'web_scrape', 'get_stock_sentiment'],
  },
  {
    id: 'conclude',
    label: '交叉验证',
    subLabel: '综合研判+策略',
    icon: '🎯',
    tools: ['sequential_thinking'],
  },
];

const ALL_PHASE_TOOLS = new Set(PHASES.flatMap((p) => p.tools));

const TOOL_DISPLAY: Record<string, string> = {
  get_market_indices: '大盘指数',
  get_sector_rankings: '板块排名',
  get_realtime_quote: '实时行情',
  get_daily_history: 'K线数据',
  analyze_trend: '技术指标',
  get_volume_analysis: '量价分析',
  analyze_pattern: 'K线形态',
  get_chip_distribution: '筹码分布',
  get_capital_flow: '资金流向',
  get_stock_info: '基本面',
  get_financial_deep_analysis: '财务深度',
  get_valuation_percentile: '估值百分位',
  search_stock_news: '新闻搜索',
  search_comprehensive_intel: '综合情报',
  web_search: '网络搜索',
  web_scrape: '网页抓取',
  get_stock_sentiment: '市场情绪',
  sequential_thinking: '结构化思考',
};

type NodeStatus = 'pending' | 'running' | 'done' | 'failed';

interface ToolNode {
  tool: string;
  displayName: string;
  status: NodeStatus;
  duration?: number;
  phaseId: PhaseId;
}

interface AgentNode {
  agentName: string;
  displayName: string;
  status: NodeStatus;
  signal?: string;
  confidence?: number;
  reasoning?: string;
  evidence: string[];
  risks: string[];
  invalidation: string[];
  thinking: string[];
}

const AGENT_DISPLAY: Record<string, string> = {
  technical: '技术面 Agent',
  intel: '情报 Agent',
  risk: '风险 Agent',
  industry: '行业 Agent',
  capital_flow: '资金 Agent',
  fundamental: '基本面 Agent',
  sentiment: '情绪 Agent',
  devils_advocate: '反方审计 Agent',
  debate: '辩论 Agent',
  scenario_analysis: '情景 Agent',
  factor_scoring: '因子 Agent',
  decision: '决策 Agent',
};

function getPhaseForTool(toolName: string): PhaseId {
  if (toolName === 'sequential_thinking') {
    return 'conclude';
  }
  for (const phase of PHASES) {
    if (phase.tools.includes(toolName)) return phase.id;
  }
  return 'overview';
}

function buildNodes(steps: ProgressStep[]): ToolNode[] {
  const nodes: ToolNode[] = [];
  const seen = new Map<string, number>();
  const hasClassicToolEvents = steps.some((s) => s.type === 'tool_start' || s.type === 'tool_done');

  for (const step of steps) {
    if (step.type === 'tool_start' || (!hasClassicToolEvents && step.type === 'agent_tool_call')) {
      const tool = step.tool || step.tool_name || '';
      if (!ALL_PHASE_TOOLS.has(tool) && tool !== 'sequential_thinking') continue;
      const idx = seen.get(tool) ?? 0;
      seen.set(tool, idx + 1);
      const key = idx > 0 ? `${tool}_${idx}` : tool;
      nodes.push({
        tool: key,
        displayName: TOOL_DISPLAY[tool] || tool,
        status: 'running',
        phaseId: getPhaseForTool(tool),
      });
    } else if (step.type === 'tool_done' || (!hasClassicToolEvents && step.type === 'agent_tool_result')) {
      const tool = step.tool || step.tool_name || '';
      if (!ALL_PHASE_TOOLS.has(tool) && tool !== 'sequential_thinking') continue;
      let runningIdx = -1;
      for (let i = nodes.length - 1; i >= 0; i--) {
        const n = nodes[i];
        if ((n.tool === tool || n.tool.startsWith(tool + '_')) && n.status === 'running') {
          runningIdx = i;
          break;
        }
      }
      if (runningIdx >= 0) {
        nodes[runningIdx].status = step.success ? 'done' : 'failed';
        nodes[runningIdx].duration = step.duration;
      }
    }
  }

  return nodes;
}

function buildAgentNodes(steps: ProgressStep[]): AgentNode[] {
  const agents: AgentNode[] = [];

  const ensureAgent = (agentName: string, displayName?: string) => {
    let agent = agents.find((a) => a.agentName === agentName);
    if (!agent) {
      agent = {
        agentName,
        displayName: displayName || AGENT_DISPLAY[agentName] || agentName,
        status: 'running',
        evidence: [],
        risks: [],
        invalidation: [],
        thinking: [],
      };
      agents.push(agent);
    } else if (displayName && agent.displayName === agent.agentName) {
      agent.displayName = displayName;
    }
    return agent;
  };

  for (const step of steps) {
    const agentName = step.agent_name || step.stage || '';
    if (!agentName) continue;

    if (step.type === 'agent_start' || step.type === 'stage_start') {
      ensureAgent(agentName, step.display_name);
    } else if (step.type === 'agent_thinking') {
      const agent = ensureAgent(agentName, step.display_name);
      const thinking = step.thinking || step.message || '';
      if (thinking) {
        agent.thinking.push(thinking);
      }
    } else if (step.type === 'agent_opinion') {
      const agent = ensureAgent(agentName, step.display_name);
      agent.status = 'done';
      agent.signal = step.signal;
      agent.confidence = step.confidence;
      agent.reasoning = step.reasoning;
      const rawData = step.raw_data || {};
      agent.evidence = pickStringList(rawData, ['evidence', 'positive_catalysts', 'key_observations', 'key_strengths']);
      agent.risks = pickStringList(rawData, ['risks', 'risk_alerts', 'key_concerns']);
      agent.invalidation = pickStringList(rawData, ['invalid_if', 'thesis_breakers', 'watch_items']);
    } else if (step.type === 'stage_done') {
      const agent = ensureAgent(agentName, step.display_name);
      agent.status = step.success === false || step.status === 'failed' ? 'failed' : 'done';
    }
  }

  return agents;
}

function pickStringList(rawData: Record<string, unknown>, keys: string[]): string[] {
  const result: string[] = [];
  for (const key of keys) {
    const value = rawData[key];
    if (!Array.isArray(value)) continue;
    for (const item of value) {
      if (typeof item === 'string' && item.trim()) {
        result.push(item.trim());
      } else if (item && typeof item === 'object') {
        const record = item as Record<string, unknown>;
        const text = record.claim || record.title || record.description;
        if (typeof text === 'string' && text.trim()) {
          result.push(text.trim());
        }
      }
      if (result.length >= 3) return result;
    }
  }
  return result;
}

function getPhaseStatus(nodes: ToolNode[], phaseId: PhaseId): NodeStatus {
  const phaseNodes = nodes.filter((n) => n.phaseId === phaseId);
  if (phaseNodes.length === 0) return 'pending';
  if (phaseNodes.some((n) => n.status === 'running')) return 'running';
  if (phaseNodes.some((n) => n.status === 'failed') && !phaseNodes.some((n) => n.status === 'running')) {
    const doneCount = phaseNodes.filter((n) => n.status === 'done').length;
    if (doneCount > 0) return 'done';
    return 'failed';
  }
  if (phaseNodes.every((n) => n.status === 'done')) return 'done';
  return 'pending';
}

const PhaseNode: React.FC<{
  phase: Phase;
  status: NodeStatus;
  nodes: ToolNode[];
  isActive: boolean;
}> = ({ phase, status, nodes, isActive }) => {
  const phaseNodes = nodes.filter((n) => n.phaseId === phase.id);

  return (
    <div className={`research-phase ${isActive ? 'research-phase-active' : ''} research-phase-${status}`}>
      <div className="research-phase-header">
        <div className="research-phase-indicator">
          {status === 'running' ? (
            <div className="research-pulse-ring">
              <div className="research-pulse-core" />
            </div>
          ) : status === 'done' ? (
            <div className="research-check-icon">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12" />
              </svg>
            </div>
          ) : status === 'failed' ? (
            <div className="research-fail-icon">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
            </div>
          ) : (
            <div className="research-pending-dot" />
          )}
        </div>
        <span className="research-phase-icon">{phase.icon}</span>
        <div className="research-phase-text">
          <span className="research-phase-label">{phase.label}</span>
          <span className="research-phase-sublabel">{phase.subLabel}</span>
        </div>
        {status === 'running' && (
          <span className="research-phase-status-text research-phase-status-running">进行中</span>
        )}
        {status === 'done' && (
          <span className="research-phase-status-text research-phase-status-done">完成</span>
        )}
      </div>

      {phaseNodes.length > 0 && (
        <div className="research-tool-list">
          {phaseNodes.map((node) => (
            <div key={node.tool} className={`research-tool-item research-tool-${node.status}`}>
              <div className="research-tool-dot" />
              <span className="research-tool-name">{node.displayName}</span>
              {node.status === 'running' && (
                <span className="research-tool-spinner" />
              )}
              {node.status === 'done' && node.duration != null && (
                <span className="research-tool-duration">{node.duration.toFixed(1)}s</span>
              )}
              {node.status === 'failed' && (
                <span className="research-tool-failed-text">失败</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

const PhaseConnector: React.FC<{ fromStatus: NodeStatus }> = ({ fromStatus }) => (
  <div className={`research-connector research-connector-${fromStatus === 'done' ? 'active' : fromStatus === 'running' ? 'running' : 'idle'}`}>
    <div className="research-connector-line" />
    <svg className="research-connector-arrow" width="10" height="10" viewBox="0 0 10 10">
      <path d="M5 0 L10 5 L5 10" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  </div>
);

interface ResearchPathViewProps {
  steps: ProgressStep[];
  isGenerating?: boolean;
}

const ResearchPathView: React.FC<ResearchPathViewProps> = ({ steps, isGenerating }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const prevStepCountRef = useRef(0);

  const nodes = buildNodes(steps);
  const agents = buildAgentNodes(steps);

  const activePhaseIdx = PHASES.findIndex((phase) => {
    const s = getPhaseStatus(nodes, phase.id);
    return s === 'running';
  });

  const currentPhaseIdx = activePhaseIdx >= 0 ? activePhaseIdx : PHASES.length - 1;

  useEffect(() => {
    if (autoScroll && containerRef.current && steps.length > prevStepCountRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
    prevStepCountRef.current = steps.length;
  }, [steps, autoScroll]);

  const handleScroll = () => {
    if (!containerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    const nearBottom = scrollHeight - scrollTop - clientHeight < 40;
    setAutoScroll(nearBottom);
  };

  return (
    <div className="research-path-container" ref={containerRef} onScroll={handleScroll}>
      <div className="research-path-title">
        <svg className="research-path-title-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10" />
          <path d="M12 6v6l4 2" />
        </svg>
        <span>研究路径</span>
        {isGenerating && <span className="research-path-generating-badge">生成中</span>}
      </div>

      {agents.length > 0 && (
        <div className="mb-3 space-y-1.5">
          {agents.map((agent) => (
            <div key={agent.agentName} className={`rounded-sm border px-2 py-1.5 ${
              agent.status === 'running'
                ? 'border-primary/30 bg-primary/5'
                : agent.status === 'failed'
                  ? 'border-red-400/30 bg-red-500/5'
                  : 'border-border bg-surface/40'
            }`}>
              <div className="flex items-center justify-between gap-2">
                <div className="flex min-w-0 items-center gap-1.5">
                  <span className={`h-1.5 w-1.5 flex-shrink-0 rounded-full ${
                    agent.status === 'running'
                      ? 'bg-primary animate-pulse'
                      : agent.status === 'failed'
                        ? 'bg-red-400'
                        : 'bg-emerald-400'
                  }`} />
                  <span className="truncate text-[11px] font-medium text-foreground">{agent.displayName}</span>
                </div>
                {agent.signal && (
                  <span className="flex-shrink-0 text-[10px] text-muted-text">
                    {agent.signal}{agent.confidence != null ? ` · ${(agent.confidence * 100).toFixed(0)}%` : ''}
                  </span>
                )}
              </div>
              {agent.reasoning && (
                <div className="mt-1 text-[11px] leading-relaxed text-secondary-text">{agent.reasoning}</div>
              )}
              {(agent.evidence.length > 0 || agent.risks.length > 0 || agent.invalidation.length > 0) && (
                <div className="mt-1.5 grid gap-1 text-[10px] leading-relaxed text-muted-text">
                  {agent.evidence.slice(0, 2).map((item) => (
                    <div key={`ev-${agent.agentName}-${item}`}><span className="text-emerald-400">证据</span> {item}</div>
                  ))}
                  {agent.risks.slice(0, 2).map((item) => (
                    <div key={`risk-${agent.agentName}-${item}`}><span className="text-red-400">风险</span> {item}</div>
                  ))}
                  {agent.invalidation.slice(0, 1).map((item) => (
                    <div key={`inv-${agent.agentName}-${item}`}><span className="text-yellow-300">观察</span> {item}</div>
                  ))}
                </div>
              )}
              {!agent.reasoning && agent.thinking.length > 0 && (
                <div className="mt-1 text-[10px] leading-relaxed text-muted-text">
                  {agent.thinking[agent.thinking.length - 1]}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="research-path-timeline">
        {PHASES.map((phase, idx) => {
          const status = getPhaseStatus(nodes, phase.id);
          const isActive = idx === currentPhaseIdx;

          return (
            <React.Fragment key={phase.id}>
              <PhaseNode phase={phase} status={status} nodes={nodes} isActive={isActive} />
              {idx < PHASES.length - 1 && (
                <PhaseConnector fromStatus={status} />
              )}
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
};

export default ResearchPathView;
