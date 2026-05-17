# -*- coding: utf-8 -*-
from typing import Dict, List

import pandas as pd

from src.formula.engine import FormulaEngine


def macd(df: pd.DataFrame, short: int = 12, long: int = 26, m: int = 9) -> Dict[str, List[float]]:
    formula = (
        f"DIF:EMA(CLOSE,{short})-EMA(CLOSE,{long});"
        f"DEA:EMA(DIF,{m});"
        f"MACD:2*(DIF-DEA);"
    )
    engine = FormulaEngine()
    return engine.evaluate(formula, df)


def kdj(df: pd.DataFrame, n: int = 9, m1: int = 3, m2: int = 3) -> Dict[str, List[float]]:
    formula = (
        f"RSV:=(CLOSE-LLV(LOW,{n}))/(HHV(HIGH,{n})-LLV(LOW,{n}))*100;"
        f"K:SMA(RSV,{m1},1);"
        f"D:SMA(K,{m2},1);"
        f"J:3*K-2*D;"
    )
    engine = FormulaEngine()
    return engine.evaluate(formula, df)


def rsi(df: pd.DataFrame, n1: int = 6, n2: int = 12, n3: int = 24) -> Dict[str, List[float]]:
    formula = (
        f"LC:=REF(CLOSE,1);"
        f"UP:=MAX(CLOSE-LC,0);"
        f"DN:=ABS(CLOSE-LC);"
        f"RSI1:SMA(UP,{n1},1)/SMA(DN,{n1},1)*100;"
        f"RSI2:SMA(UP,{n2},1)/SMA(DN,{n2},1)*100;"
        f"RSI3:SMA(UP,{n3},1)/SMA(DN,{n3},1)*100;"
    )
    engine = FormulaEngine()
    return engine.evaluate(formula, df)


def boll(df: pd.DataFrame, n: int = 20, k: int = 2) -> Dict[str, List[float]]:
    formula = (
        f"MID:MA(CLOSE,{n});"
        f"TMP:=STD(CLOSE,{n});"
        f"UPPER:MID+{k}*TMP;"
        f"LOWER:MID-{k}*TMP;"
    )
    engine = FormulaEngine()
    return engine.evaluate(formula, df)


def zhu_li_sha_zhuang(df: pd.DataFrame) -> Dict[str, List[float]]:
    formula = (
        "MA6:MA(C,6);"
        "MA18:MA(C,18);"
        "MA30:MA(C,30);"
        "MA60:MA(C,60);"
        "M250:MA(C,250);"
        "LC:=REF(CLOSE,1);"
        "RSI:=SMA(MAX(CLOSE-LC,0),4,1)/SMA(ABS(CLOSE-LC),4,1)*100;"
        "VAR27:=REF(CLOSE,1);"
        "VAR28:=SMA(MAX(CLOSE-VAR27,0),5,1)/SMA(ABS(CLOSE-VAR27),6,1)*100;"
        "VARA:=(AMOUNT)/(VOL)/(100);"
        "VARB:=(3)*(HIGH)+LOW+OPEN+(2)*(CLOSE)/(7);"
        "VARC:=(SUM(AMOUNT,7))/(VARA)/(100);"
        "VARD:=DMA(VARB,(VOL)/(VARC));"
        "VARE:=((CLOSE-VARD)/(VARD))*(100);"
        "VARF:=((CLOSE-LLV(LOW,34))/(HHV(HIGH,34)-LLV(LOW,34)))*(100);"
        "VARJ:=MA(VARE,20)+STD(VARE,20);"
        "VARH:=(C+L+H)/3;"
        "VARL:=EMA(VARH,6);"
        "VARG:=EMA(VARL,5);"
        "BIAS18:=((CLOSE-MA(CLOSE,18))/(MA(CLOSE,18)))*(100);"
        "AA:=SMA(VARF,3,1);"
        "VAR1:=ZIG(3,10)<REF(ZIG(3,10),1) AND REF(ZIG(3,10),1)>REF(ZIG(3,10),2);"
        "VAR2:=ZIG(3,10)>REF(ZIG(3,10),1) AND REF(ZIG(3,10),1)<REF(ZIG(3,10),2);"
        "高位信号:STICKLINE(VAR1,H,L,8,0);"
        "底位信号:STICKLINE(VAR2,H,L,8,0);"
        "金叉信号:STICKLINE(CROSS(VARL,VARG),H,L,2,1);"
    )
    engine = FormulaEngine()
    return engine.evaluate(formula, df)


def capital_flow(df: pd.DataFrame, n: int = 5) -> Dict[str, List[float]]:
    formula = (
        "VOL_RATIO:=VOL/MA(VOL,5);"
        "UP_VOL:=IF(CLOSE>REF(CLOSE,1),VOL,0);"
        "DN_VOL:=IF(CLOSE<REF(CLOSE,1),VOL,0);"
        "EQ_VOL:=IF(CLOSE=REF(CLOSE,1),VOL,0);"
        f"主力流入:SUM(UP_VOL,{n});"
        f"主力流出:SUM(DN_VOL,{n});"
        f"主力净流入:主力流入-主力流出;"
        f"量比:VOL_RATIO;"
    )
    engine = FormulaEngine()
    return engine.evaluate(formula, df)


INDICATOR_REGISTRY = {
    "MACD": {
        "name": "MACD",
        "description": "指数平滑异同移动平均线，由DIF、DEA和MACD柱组成",
        "fn": macd,
        "params": [
            {"name": "short", "type": "int", "default": 12, "description": "短期EMA周期"},
            {"name": "long", "type": "int", "default": 26, "description": "长期EMA周期"},
            {"name": "m", "type": "int", "default": 9, "description": "信号线周期"},
        ],
    },
    "KDJ": {
        "name": "KDJ",
        "description": "随机指标，由K、D、J三条线组成，用于判断超买超卖",
        "fn": kdj,
        "params": [
            {"name": "n", "type": "int", "default": 9, "description": "RSV周期"},
            {"name": "m1", "type": "int", "default": 3, "description": "K值平滑周期"},
            {"name": "m2", "type": "int", "default": 3, "description": "D值平滑周期"},
        ],
    },
    "RSI": {
        "name": "RSI",
        "description": "相对强弱指标，衡量价格变动速度和幅度",
        "fn": rsi,
        "params": [
            {"name": "n1", "type": "int", "default": 6, "description": "短期RSI周期"},
            {"name": "n2", "type": "int", "default": 12, "description": "中期RSI周期"},
            {"name": "n3", "type": "int", "default": 24, "description": "长期RSI周期"},
        ],
    },
    "BOLL": {
        "name": "BOLL",
        "description": "布林带，由中轨、上轨、下轨组成，反映价格波动区间",
        "fn": boll,
        "params": [
            {"name": "n", "type": "int", "default": 20, "description": "中轨周期"},
            {"name": "k", "type": "int", "default": 2, "description": "标准差倍数"},
        ],
    },
    "ZHU_LI_SHA_ZHUANG": {
        "name": "ZHU_LI_SHA_ZHUANG",
        "description": "主力杀庄指标，分析主力控盘程度和庄家动向",
        "fn": zhu_li_sha_zhuang,
        "params": [],
    },
    "CAPITAL_FLOW": {
        "name": "CAPITAL_FLOW",
        "description": "资金流向指标，分析主力资金进出情况",
        "fn": capital_flow,
        "params": [
            {"name": "n", "type": "int", "default": 5, "description": "统计周期"},
        ],
    },
}
