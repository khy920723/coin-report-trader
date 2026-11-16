"""기간별 봉 통계와 지표의 봉 계산(M1 명세 3.5, 3.6). 계산만 한다.

`core`와 표준 라이브러리만 import한다. 파일, DB, 네트워크, 시계를 읽지 않는다.
"""

from coin_report_trader.stats.compute import CandleStats, compute_candle_stats

__all__ = ["CandleStats", "compute_candle_stats"]
