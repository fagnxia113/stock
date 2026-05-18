# -*- coding: utf-8 -*-
"""
===================================
PytdxFetcher - 通达信数据源 (Priority 0)
===================================

数据来源：通达信行情服务器（pytdx 库）
特点：免费、无需 Token、直连行情服务器
优点：实时数据、稳定、无配额限制

关键策略：
1. 多服务器自动切换
2. 连接超时自动重连
3. 失败后指数退避重试
"""

import logging
import re
import time
import glob as glob_mod
from configparser import ConfigParser
from contextlib import contextmanager
from typing import Optional, Generator, List, Tuple, Dict

import pandas as pd
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from .base import BaseFetcher, DataFetchError, STANDARD_COLUMNS, is_bse_code, _is_hk_market, summarize_exception
import os
import sys

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))

_TDX_COMMON_INSTALL_DIRS: List[str] = []
if sys.platform == "win32":
    for _drive in "CDEFGH":
        for _prefix in [
            f"{_drive}:\\new_tdx",
            f"{_drive}:\\tdx",
            f"{_drive}:\\通达信",
        ]:
            _TDX_COMMON_INSTALL_DIRS.append(_prefix)
        for _d in glob_mod.glob(f"{_drive}:\\软件\\*tdx*"):
            _TDX_COMMON_INSTALL_DIRS.append(_d)
        for _d in glob_mod.glob(f"{_drive}:\\Program Files\\*tdx*"):
            _TDX_COMMON_INSTALL_DIRS.append(_d)
        for _d in glob_mod.glob(f"{_drive}:\\Program Files (x86)\\*tdx*"):
            _TDX_COMMON_INSTALL_DIRS.append(_d)


def _detect_tdx_install_dir() -> Optional[str]:
    custom = os.getenv("PYTDX_INSTALL_DIR", "").strip()
    if custom and os.path.isfile(os.path.join(custom, "connect.cfg")):
        return custom
    for d in _TDX_COMMON_INSTALL_DIRS:
        if os.path.isfile(os.path.join(d, "connect.cfg")):
            return d
    return None


def _parse_connect_cfg(install_dir: str) -> List[Tuple[str, int]]:
    cfg_path = os.path.join(install_dir, "connect.cfg")
    if not os.path.isfile(cfg_path):
        return []

    hosts: List[Tuple[str, int]] = []
    primary_idx = 0

    try:
        with open(cfg_path, "r", encoding="gbk", errors="ignore") as f:
            content = f.read()

        cp = ConfigParser(strict=False)
        cp.read_string(content)

        if cp.has_option("HQHOST", "PrimaryHost"):
            try:
                primary_idx = int(cp.get("HQHOST", "PrimaryHost"))
            except (ValueError, TypeError):
                pass

        host_num = 0
        if cp.has_option("HQHOST", "HostNum"):
            try:
                host_num = int(cp.get("HQHOST", "HostNum"))
            except (ValueError, TypeError):
                pass

        for i in range(1, host_num + 1):
            ip_key = f"IPAddress{i:02d}"
            port_key = f"Port{i:02d}"
            if cp.has_option("HQHOST", ip_key) and cp.has_option("HQHOST", port_key):
                ip = cp.get("HQHOST", ip_key).strip()
                try:
                    port = int(cp.get("HQHOST", port_key).strip())
                except (ValueError, TypeError):
                    continue
                if ip and port > 0:
                    hosts.append((ip, port))

        if primary_idx > 0 and primary_idx <= len(hosts):
            primary = hosts[primary_idx - 1]
            hosts.pop(primary_idx - 1)
            hosts.insert(0, primary)

    except Exception as e:
        logger.warning(f"解析 connect.cfg 失败: {e}")

    return hosts


def _parse_hosts_from_env() -> Optional[List[Tuple[str, int]]]:
    servers = os.getenv("PYTDX_SERVERS", "").strip()
    if servers:
        result = []
        for part in servers.split(","):
            part = part.strip()
            if ":" in part:
                host, port_str = part.rsplit(":", 1)
                host, port_str = host.strip(), port_str.strip()
                if host and port_str:
                    try:
                        result.append((host, int(port_str)))
                    except ValueError:
                        logger.warning(f"Invalid PYTDX_SERVERS entry: {part}")
            else:
                logger.warning(f"Invalid PYTDX_SERVERS entry (missing port): {part}")
        if result:
            return result

    host = os.getenv("PYTDX_HOST", "").strip()
    port_str = os.getenv("PYTDX_PORT", "").strip()
    if host and port_str:
        try:
            return [(host, int(port_str))]
        except ValueError:
            logger.warning(f"Invalid PYTDX_HOST/PYTDX_PORT: {host}:{port_str}")

    return None


def _is_us_code(stock_code: str) -> bool:
    """
    判断代码是否为美股
    
    美股代码规则：
    - 1-5个大写字母，如 'AAPL', 'TSLA'
    - 可能包含 '.'，如 'BRK.B'
    """
    code = stock_code.strip().upper()
    return bool(re.match(r'^[A-Z]{1,5}(\.[A-Z])?$', code))


class PytdxFetcher(BaseFetcher):
    """
    通达信数据源实现
    
    优先级：2（与 Tushare 同级）
    数据来源：通达信行情服务器
    
    关键策略：
    - 自动选择最优服务器
    - 连接失败自动切换服务器
    - 失败后指数退避重试
    
    Pytdx 特点：
    - 免费、无需注册
    - 直连行情服务器
    - 支持实时行情和历史数据
    - 支持股票名称查询
    """
    
    name = "PytdxFetcher"
    priority = int(os.getenv("PYTDX_PRIORITY", "0"))
    
    # 默认通达信行情服务器列表
    DEFAULT_HOSTS = [
        ("119.147.212.81", 7709),  # 深圳
        ("112.74.214.43", 7727),   # 深圳
        ("221.231.141.60", 7709),  # 上海
        ("101.227.73.20", 7709),   # 上海
        ("101.227.77.254", 7709),  # 上海
        ("14.215.128.18", 7709),   # 广州
        ("59.173.18.140", 7709),   # 武汉
        ("180.153.39.51", 7709),   # 杭州
    ]
    # Pytdx get_security_list returns at most 1000 items per page
    SECURITY_LIST_PAGE_SIZE = 1000
    
    def __init__(self, hosts: Optional[List[Tuple[str, int]]] = None):
        if hosts is not None:
            self._hosts = hosts
        else:
            env_hosts = _parse_hosts_from_env()
            if env_hosts:
                self._hosts = env_hosts
                logger.debug(f"Pytdx 使用环境变量配置的 {len(env_hosts)} 个服务器")
            else:
                tdx_dir = _detect_tdx_install_dir()
                if tdx_dir:
                    cfg_hosts = _parse_connect_cfg(tdx_dir)
                    if cfg_hosts:
                        self._hosts = cfg_hosts
                        logger.info(f"Pytdx 自动检测到通达信安装目录 {tdx_dir}，加载 {len(cfg_hosts)} 个行情服务器")
                    else:
                        self._hosts = self.DEFAULT_HOSTS
                        logger.debug("Pytdx connect.cfg 中未找到有效服务器，使用默认列表")
                else:
                    self._hosts = self.DEFAULT_HOSTS
                    logger.debug("Pytdx 未检测到本地通达信安装，使用默认服务器列表")
        self._api = None
        self._connected = False
        self._current_host_idx = 0
        self._stock_list_cache = None
        self._stock_name_cache = {}
        self._host_cooldown_until: Dict[int, float] = {}
        self._host_failures: Dict[int, int] = {}
        self._connect_timeout = _env_float("PYTDX_CONNECT_TIMEOUT", 1.2, minimum=0.2, maximum=5.0)
        self._server_cooldown = _env_float("PYTDX_SERVER_COOLDOWN", 120.0, minimum=0.0, maximum=1800.0)
        self._max_connect_hosts = _env_int(
            "PYTDX_MAX_CONNECT_HOSTS",
            3,
            minimum=1,
            maximum=max(1, len(self._hosts)),
        )
        self._reuse_connection = (
            os.getenv("PYTDX_REUSE_CONNECTION", "true").strip().lower()
            not in {"0", "false", "no", "off"}
        )
    
    def _get_pytdx(self):
        """
        延迟加载 pytdx 模块
        
        只在首次使用时导入，避免未安装时报错
        """
        try:
            from pytdx.hq import TdxHq_API
            return TdxHq_API
        except ImportError:
            logger.warning("pytdx 未安装，请运行: pip install pytdx")
            return None

    def _ordered_host_indices(self) -> List[int]:
        if not self._hosts:
            return []
        total = len(self._hosts)
        indices = [(self._current_host_idx + i) % total for i in range(total)]
        now = time.monotonic()
        active = [idx for idx in indices if self._host_cooldown_until.get(idx, 0.0) <= now]
        cooling = [idx for idx in indices if idx not in active]
        return active + cooling

    def _record_host_success(self, host_idx: int) -> None:
        self._current_host_idx = host_idx
        self._host_failures.pop(host_idx, None)
        self._host_cooldown_until.pop(host_idx, None)

    def _record_host_failure(self, host_idx: int) -> None:
        failures = self._host_failures.get(host_idx, 0) + 1
        self._host_failures[host_idx] = failures
        if self._server_cooldown > 0:
            cooldown = min(self._server_cooldown * failures, self._server_cooldown * 3)
            self._host_cooldown_until[host_idx] = time.monotonic() + cooldown
    
    @contextmanager
    def _pytdx_session(self) -> Generator:
        """
        Pytdx 连接上下文管理器
        
        确保：
        1. 进入上下文时自动连接
        2. 退出上下文时自动断开
        3. 异常时也能正确断开
        
        使用示例：
            with self._pytdx_session() as api:
                # 在这里执行数据查询
        """
        TdxHq_API = self._get_pytdx()
        if TdxHq_API is None:
            raise DataFetchError("pytdx 库未安装")
        
        api = TdxHq_API()
        connected = False
        
        try:
            # 尝试连接服务器（自动选择最优）
            attempted = 0
            for i in range(len(self._hosts)):
                ordered_hosts = self._ordered_host_indices()
                if i >= len(ordered_hosts) or attempted >= self._max_connect_hosts:
                    break
                attempted += 1
                host_idx = ordered_hosts[i]
                host, port = self._hosts[host_idx]
                
                try:
                    if api.connect(host, port, time_out=self._connect_timeout):
                        connected = True
                        self._record_host_success(host_idx)
                        logger.debug(f"Pytdx 连接成功: {host}:{port}")
                        break
                    self._record_host_failure(host_idx)
                except Exception as e:
                    self._record_host_failure(host_idx)
                    logger.debug(f"Pytdx 连接 {host}:{port} 失败: {e}")
                    continue
            
            if not connected:
                raise DataFetchError("Pytdx 无法连接任何服务器")
            
            yield api
            
        finally:
            # 确保断开连接
            try:
                api.disconnect()
                logger.debug("Pytdx 连接已断开")
            except Exception as e:
                logger.warning(f"Pytdx 断开连接时出错: {e}")
    
    def _get_market_code(self, stock_code: str) -> Tuple[int, str]:
        """
        根据股票代码判断市场
        
        Pytdx 市场代码：
        - 0: 深圳
        - 1: 上海
        
        Args:
            stock_code: 股票代码
            
        Returns:
            (market, code) 元组
        """
        code = stock_code.strip()
        
        # 去除可能的前缀后缀
        code = code.replace('.SH', '').replace('.SZ', '')
        code = code.replace('.sh', '').replace('.sz', '')
        code = code.replace('sh', '').replace('sz', '')
        
        # 根据代码前缀判断市场
        # 上海：60xxxx, 68xxxx（科创板）
        # 深圳：00xxxx, 30xxxx（创业板）, 002xxx（中小板）
        if code.startswith(('60', '68')):
            return 1, code  # 上海
        else:
            return 0, code  # 深圳

    def _build_stock_list_cache(self, api) -> None:
        """
        Build a full stock code -> name cache from paginated security lists.
        """
        self._stock_list_cache = {}

        for market in (0, 1):
            start = 0
            while True:
                stocks = api.get_security_list(market, start) or []
                for stock in stocks:
                    code = stock.get('code')
                    name = stock.get('name')
                    if code and name:
                        self._stock_list_cache[code] = name

                if len(stocks) < self.SECURITY_LIST_PAGE_SIZE:
                    break

                start += self.SECURITY_LIST_PAGE_SIZE
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str, period: str = 'daily') -> pd.DataFrame:
        if _is_us_code(stock_code):
            raise DataFetchError(f"PytdxFetcher 不支持美股 {stock_code}，请使用 AkshareFetcher 或 YfinanceFetcher")

        if _is_hk_market(stock_code):
            raise DataFetchError(f"PytdxFetcher 不支持港股 {stock_code}，请使用 AkshareFetcher")

        if is_bse_code(stock_code):
            raise DataFetchError(
                f"PytdxFetcher 不支持北交所 {stock_code}，将自动切换其他数据源"
            )
        
        market, code = self._get_market_code(stock_code)
        
        CATEGORY_MAP = {
            'daily': 9,
            'weekly': 5,
            'monthly': 6,
        }
        category = CATEGORY_MAP.get(period, 9)
        
        from datetime import datetime as dt
        start_dt = dt.strptime(start_date, '%Y-%m-%d')
        end_dt = dt.strptime(end_date, '%Y-%m-%d')
        days = (end_dt - start_dt).days
        
        if period == 'weekly':
            count = min(max(days // 7 + 10, 30), 500)
        elif period == 'monthly':
            count = min(max(days // 30 + 10, 24), 200)
        else:
            count = min(max(days * 5 // 7 + 10, 30), 800)
        
        logger.debug(f"调用 Pytdx get_security_bars(market={market}, code={code}, category={category}({period}), count={count})")
        
        with self._pytdx_session() as api:
            try:
                data = api.get_security_bars(
                    category=category,
                    market=market,
                    code=code,
                    start=0,
                    count=count
                )
                
                if data is None or len(data) == 0:
                    raise DataFetchError(f"Pytdx 未查询到 {stock_code} 的{period}数据")
                
                df = api.to_df(data)
                
                df['datetime'] = pd.to_datetime(df['datetime'])
                df = df[(df['datetime'] >= start_date) & (df['datetime'] <= end_date)]
                
                return df
                
            except Exception as e:
                if isinstance(e, DataFetchError):
                    raise
                raise DataFetchError(f"Pytdx 获取数据失败: {e}") from e
    
    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """
        标准化 Pytdx 数据
        
        Pytdx 返回的列名：
        datetime, open, high, low, close, vol, amount
        
        需要映射到标准列名：
        date, open, high, low, close, volume, amount, pct_chg
        """
        df = df.copy()
        
        # 列名映射
        column_mapping = {
            'datetime': 'date',
            'vol': 'volume',
        }
        
        df = df.rename(columns=column_mapping)
        
        # 计算涨跌幅（pytdx 不返回涨跌幅，需要自己计算）
        if 'pct_chg' not in df.columns and 'close' in df.columns:
            df['pct_chg'] = df['close'].pct_change() * 100
            df['pct_chg'] = df['pct_chg'].fillna(0).round(2)
        
        # 添加股票代码列
        df['code'] = stock_code
        
        # 只保留需要的列
        keep_cols = ['code'] + STANDARD_COLUMNS
        existing_cols = [col for col in keep_cols if col in df.columns]
        df = df[existing_cols]
        
        return df
    
    def get_stock_name(self, stock_code: str) -> Optional[str]:
        """
        获取股票名称
        
        Args:
            stock_code: 股票代码
            
        Returns:
            股票名称，失败返回 None
        """
        # 港股不支持（pytdx 不含港股数据）
        if _is_hk_market(stock_code):
            return None

        # 先检查缓存
        if stock_code in self._stock_name_cache:
            return self._stock_name_cache[stock_code]
        
        try:
            market, code = self._get_market_code(stock_code)
            
            with self._pytdx_session() as api:
                # 获取股票列表（缓存）
                if self._stock_list_cache is None:
                    self._build_stock_list_cache(api)
                
                # 查找股票名称
                name = self._stock_list_cache.get(code)
                if name:
                    self._stock_name_cache[stock_code] = name
                    return name
                
                # 尝试使用 get_finance_info
                finance_info = api.get_finance_info(market, code)
                if finance_info and 'name' in finance_info:
                    name = finance_info['name']
                    self._stock_name_cache[stock_code] = name
                    return name
                
        except Exception as e:
            logger.warning(f"Pytdx 获取股票名称失败 {stock_code}: {e}")
        
        return None
    
    def get_realtime_quote(self, stock_code: str) -> Optional['UnifiedRealtimeQuote']:
        """
        获取实时行情
        
        Args:
            stock_code: 股票代码
            
        Returns:
            UnifiedRealtimeQuote 对象，失败返回 None
        """
        from .realtime_types import UnifiedRealtimeQuote, RealtimeSource
        if is_bse_code(stock_code):
            raise DataFetchError(
                f"PytdxFetcher 不支持北交所 {stock_code}，将自动切换其他数据源"
            )
        try:
            market, code = self._get_market_code(stock_code)
            
            with self._pytdx_session() as api:
                data = api.get_security_quotes([(market, code)])
                
                if data and len(data) > 0:
                    q = data[0]
                    price = q.get('price', 0)
                    pre_close = q.get('last_close', 0)
                    change_pct = None
                    change_amount = None
                    if pre_close and pre_close > 0 and price:
                        change_amount = round(price - pre_close, 2)
                        change_pct = round((change_amount / pre_close) * 100, 2)
                    return UnifiedRealtimeQuote(
                        code=stock_code,
                        name=q.get('name', ''),
                        source=RealtimeSource.PYTDX,
                        price=price,
                        open_price=q.get('open', 0) or None,
                        high=q.get('high', 0) or None,
                        low=q.get('low', 0) or None,
                        pre_close=pre_close or None,
                        volume=q.get('vol', 0) or None,
                        amount=q.get('amount', 0) or None,
                        change_pct=change_pct,
                        change_amount=change_amount,
                    )
        except Exception as e:
            logger.warning(f"Pytdx 获取实时行情失败 {stock_code}: {e}")
        
        return None

    def get_orderbook(self, stock_code: str) -> Optional[dict]:
        if is_bse_code(stock_code):
            raise DataFetchError(f"PytdxFetcher 不支持北交所 {stock_code}")
        try:
            market, code = self._get_market_code(stock_code)
            with self._pytdx_session() as api:
                data = api.get_security_quotes([(market, code)])
                if data and len(data) > 0:
                    q = data[0]
                    bids = []
                    asks = []
                    for i in range(1, 6):
                        bids.append({
                            'price': q.get(f'bid{i}', 0),
                            'volume': q.get(f'bid_vol{i}', 0),
                        })
                        asks.append({
                            'price': q.get(f'ask{i}', 0),
                            'volume': q.get(f'ask_vol{i}', 0),
                        })
                    return {
                        'code': stock_code,
                        'name': q.get('name', ''),
                        'price': q.get('price', 0),
                        'pre_close': q.get('last_close', 0),
                        'bids': bids,
                        'asks': asks,
                    }
        except Exception as e:
            logger.warning(f"Pytdx 获取五档盘口失败 {stock_code}: {e}")
        return None

    def get_trade_ticks(self, stock_code: str, count: int = 50) -> Optional[list]:
        if is_bse_code(stock_code):
            raise DataFetchError(f"PytdxFetcher 不支持北交所 {stock_code}")
        try:
            market, code = self._get_market_code(stock_code)
            with self._pytdx_session() as api:
                data = api.get_transaction_data(market, code, start=0, count=count)
                if data:
                    ticks = []
                    for item in data:
                        ticks.append({
                            'time': item.get('time', ''),
                            'price': item.get('price', 0),
                            'volume': item.get('vol', 0),
                            'num': item.get('num', 0),
                            'type': item.get('nature', 0),
                        })
                    return ticks
        except Exception as e:
            logger.warning(f"Pytdx 获取成交明细失败 {stock_code}: {e}")
        return None

    def _resolve_index_market(self, index_code: str) -> Tuple[int, str]:
        code = index_code.strip()
        code = code.replace('.SH', '').replace('.SZ', '')
        code = code.replace('.sh', '').replace('.sz', '')
        code = code.replace('sh', '').replace('sz', '')
        if code.startswith(('000', '880', '9')):
            return 1, code
        elif code.startswith(('399',)):
            return 0, code
        elif code.startswith(('60', '68')):
            return 1, code
        else:
            return 0, code

    def get_index_bars(self, index_code: str, period: str = 'daily', count: int = 120) -> pd.DataFrame:
        CATEGORY_MAP = {
            'daily': 9,
            'weekly': 5,
            'monthly': 6,
        }
        category = CATEGORY_MAP.get(period, 9)

        market, code = self._resolve_index_market(index_code)

        request_start = time.time()
        logger.info(f"[{self.name}] 开始获取指数 {index_code} {period}K线: count={count}")

        try:
            with self._pytdx_session() as api:
                data = api.get_index_bars(
                    category=category,
                    market=market,
                    code=code,
                    start=0,
                    count=count
                )

                if data is None or len(data) == 0:
                    raise DataFetchError(f"Pytdx 未查询到指数 {index_code} 的{period}数据")

                df = api.to_df(data)
                df = self._normalize_data(df, index_code)
                df = self._clean_data(df)
                df = self._calculate_indicators(df)

                elapsed = time.time() - request_start
                logger.info(
                    f"[{self.name}] 指数 {index_code} {period}K线获取成功: "
                    f"rows={len(df)}, elapsed={elapsed:.2f}s"
                )
                return df

        except DataFetchError:
            raise
        except Exception as e:
            elapsed = time.time() - request_start
            error_type, error_reason = summarize_exception(e)
            logger.error(
                f"[{self.name}] 指数 {index_code} {period}K线获取失败: "
                f"error_type={error_type}, elapsed={elapsed:.2f}s, reason={error_reason}"
            )
            raise DataFetchError(f"[{self.name}] 指数 {index_code}: {error_reason}") from e

    def get_xdxr_info(self, stock_code: str) -> Optional[list]:
        if is_bse_code(stock_code):
            raise DataFetchError(f"PytdxFetcher 不支持北交所 {stock_code}")
        try:
            market, code = self._get_market_code(stock_code)
            with self._pytdx_session() as api:
                data = api.get_xdxr_info(market, code)
                if not data:
                    return []
                result = []
                for item in data:
                    entry = {
                        'year': item.get('year'),
                        'month': item.get('month'),
                        'day': item.get('day'),
                        'category': item.get('category'),
                        'category_name': item.get('name', ''),
                    }
                    if item.get('fenhong') is not None and item.get('fenhong', 0) != 0:
                        entry['dividend_per_share'] = round(float(item['fenhong']) / 10, 4)
                    if item.get('songzhuangu') is not None and item.get('songzhuangu', 0) != 0:
                        entry['bonus_share_ratio'] = float(item['songzhuangu'])
                    if item.get('peigu') is not None and item.get('peigu', 0) != 0:
                        entry['rights_issue_ratio'] = float(item['peigu'])
                    if item.get('peigujia') is not None and item.get('peigujia', 0) != 0:
                        entry['rights_issue_price'] = float(item['peigujia'])
                    if item.get('suogu') is not None and item.get('suogu', 0) != 0:
                        entry['lockup_ratio'] = float(item['suogu'])
                    if item.get('houzongguben') is not None:
                        entry['total_shares_after'] = float(item['houzongguben'])
                    if item.get('panhouliutong') is not None:
                        entry['float_shares_after'] = float(item['panhouliutong'])
                    result.append(entry)
                return result
        except DataFetchError:
            raise
        except Exception as e:
            logger.warning(f"Pytdx 获取除权除息失败 {stock_code}: {e}")
        return None

    def get_finance_info(self, stock_code: str) -> Optional[dict]:
        if is_bse_code(stock_code):
            raise DataFetchError(f"PytdxFetcher 不支持北交所 {stock_code}")
        try:
            market, code = self._get_market_code(stock_code)
            with self._pytdx_session() as api:
                data = api.get_finance_info(market, code)
                if not data:
                    return None
                from .realtime_types import safe_float
                result = {
                    'code': stock_code,
                    'source': 'pytdx',
                    'updated_date': str(data.get('updated_date', '')),
                    'ipo_date': str(data.get('ipo_date', '')),
                    'province': data.get('province'),
                    'industry': data.get('industry'),
                    'total_shares': safe_float(data.get('zongguben')),
                    'float_shares': safe_float(data.get('liutongguben')),
                    'bps': safe_float(data.get('meigujingzichan')),
                    'main_revenue': safe_float(data.get('zhuyingshouru')),
                    'main_profit': safe_float(data.get('zhuyinglirun')),
                    'operating_profit': safe_float(data.get('yingyelirun')),
                    'total_profit': safe_float(data.get('lirunzonghe')),
                    'net_profit': safe_float(data.get('jinglirun')),
                    'after_tax_profit': safe_float(data.get('shuihoulirun')),
                    'undistributed_profit': safe_float(data.get('weifenpeilirun')),
                    'net_assets': safe_float(data.get('jingzichan')),
                    'total_assets': safe_float(data.get('zongzichan')),
                    'current_assets': safe_float(data.get('liudongzichan')),
                    'fixed_assets': safe_float(data.get('gudingzichan')),
                    'intangible_assets': safe_float(data.get('wuxingzichan')),
                    'current_liabilities': safe_float(data.get('liudongfuzhai')),
                    'long_term_liabilities': safe_float(data.get('changqifuzhai')),
                    'operating_cash_flow': safe_float(data.get('jingyingxianjinliu')),
                    'total_cash_flow': safe_float(data.get('zongxianjinliu')),
                    'investment_income': safe_float(data.get('touzishouyu')),
                    'accounts_receivable': safe_float(data.get('yingshouzhangkuan')),
                    'surplus_reserve': safe_float(data.get('zibengongjijin')),
                    'shareholder_count': safe_float(data.get('gudongrenshu')),
                }
                return result
        except DataFetchError:
            raise
        except Exception as e:
            logger.warning(f"Pytdx 获取财务信息失败 {stock_code}: {e}")
        return None

    def get_block_info(self, block_type: str = 'industry') -> Optional[list]:
        BLOCK_FILE_MAP = {
            'industry': 'block.incon',
            'concept': 'block.concept',
            'region': 'block.region',
        }
        blockfile = BLOCK_FILE_MAP.get(block_type, 'block.incon')
        try:
            with self._pytdx_session() as api:
                data = api.get_and_parse_block_info(blockfile)
                if not data:
                    return []
                result = []
                for item in data:
                    entry = {
                        'code': item.get('code', ''),
                        'name': item.get('block_name', '') or item.get('name', ''),
                        'type': block_type,
                    }
                    if item.get('block_type'):
                        entry['block_type'] = item['block_type']
                    result.append(entry)
                return result
        except Exception as e:
            logger.warning(f"Pytdx 获取板块信息失败 {block_type}: {e}")
        return None

    def get_history_transaction_data(self, stock_code: str, date: int, count: int = 2000) -> Optional[list]:
        if is_bse_code(stock_code):
            raise DataFetchError(f"PytdxFetcher 不支持北交所 {stock_code}")
        try:
            market, code = self._get_market_code(stock_code)
            all_ticks = []
            start = 0
            with self._pytdx_session() as api:
                while True:
                    data = api.get_history_transaction_data(market, code, start, 2000, date)
                    if not data:
                        break
                    for item in data:
                        all_ticks.append({
                            'time': item.get('time', ''),
                            'price': item.get('price', 0),
                            'volume': item.get('vol', 0),
                            'num': item.get('num', 0),
                            'direction': '买' if item.get('buyorsell', 0) == 0 else ('卖' if item.get('buyorsell', 0) == 1 else '平'),
                        })
                    if len(data) < 2000:
                        break
                    start += 2000
                    if start >= count:
                        break
            return all_ticks if all_ticks else None
        except DataFetchError:
            raise
        except Exception as e:
            logger.warning(f"Pytdx 获取历史成交明细失败 {stock_code} date={date}: {e}")
        return None

    def get_intraday_data(self, stock_code: str, period: str = '5min', count: int = 240) -> pd.DataFrame:
        PERIOD_CATEGORY_MAP = {
            '1min': 7,
            '5min': 0,
            '15min': 1,
            '30min': 2,
            '60min': 3,
        }

        if period not in PERIOD_CATEGORY_MAP:
            raise ValueError(f"不支持的分时周期: {period}")

        if _is_us_code(stock_code):
            raise DataFetchError(f"PytdxFetcher 不支持美股 {stock_code} 分时数据")

        if _is_hk_market(stock_code):
            raise DataFetchError(f"PytdxFetcher 不支持港股 {stock_code} 分时数据")

        if is_bse_code(stock_code):
            raise DataFetchError(f"PytdxFetcher 不支持北交所 {stock_code} 分时数据")

        category = PERIOD_CATEGORY_MAP[period]
        market, code = self._get_market_code(stock_code)

        request_start = time.time()
        logger.info(f"[{self.name}] 开始获取 {stock_code} 分时数据: period={period}, count={count}")

        try:
            with self._pytdx_session() as api:
                data = api.get_security_bars(
                    category=category,
                    market=market,
                    code=code,
                    start=0,
                    count=count
                )

                if data is None or len(data) == 0:
                    raise DataFetchError(f"Pytdx 未查询到 {stock_code} 的分时数据")

                df = api.to_df(data)
                df = self._normalize_data(df, stock_code)
                df = self._clean_data(df)
                df = self._calculate_indicators(df)

                elapsed = time.time() - request_start
                logger.info(
                    f"[{self.name}] {stock_code} 分时数据获取成功: period={period}, "
                    f"rows={len(df)}, elapsed={elapsed:.2f}s"
                )
                return df

        except Exception as e:
            elapsed = time.time() - request_start
            error_type, error_reason = summarize_exception(e)
            logger.error(
                f"[{self.name}] {stock_code} 分时数据获取失败: period={period}, "
                f"error_type={error_type}, elapsed={elapsed:.2f}s, reason={error_reason}"
            )
            raise DataFetchError(f"[{self.name}] {stock_code}: {error_reason}") from e


if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.DEBUG)
    
    fetcher = PytdxFetcher()
    
    try:
        # 测试历史数据
        df = fetcher.get_daily_data('600519')  # 茅台
        print(f"获取成功，共 {len(df)} 条数据")
        print(df.tail())
        
        # 测试股票名称
        name = fetcher.get_stock_name('600519')
        print(f"股票名称: {name}")
        
        # 测试实时行情
        quote = fetcher.get_realtime_quote('600519')
        print(f"实时行情: {quote}")
        
    except Exception as e:
        print(f"获取失败: {e}")
